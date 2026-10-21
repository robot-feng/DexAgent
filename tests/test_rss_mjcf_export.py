"""Zero-step, real CPU Newton/MuJoCo export and RSS import contracts."""
import numpy as np
import pytest


@pytest.fixture
def snapshot(tmp_path):
    pytest.importorskip("mujoco")
    wp = pytest.importorskip("warp")
    wp.set_device("cpu")
    import newton
    from newton.solvers import SolverMuJoCo
    from agent.rss_runtime import build_cpu_mujoco_solver

    source = tmp_path / "source.xml"
    source.write_text('''<mujoco><compiler angle="radian"/><worldbody>
    <body name="free_first" pos="0 0 1"><freejoint/>
      <geom type="sphere" size=".03" mass=".1"/></body>
    <body name="rotor" pos="1 0 1"><joint name="hinge" range="-1 1"/>
      <geom type="sphere" size=".1" mass="1" gap=".001"/></body>
    <body name="spare" pos="2 0 1"><joint name="motorjoint"/>
      <geom type="box" size=".1 .2 .3" mass="1"/></body>
    </worldbody><actuator>
      <position name="original_position" joint="hinge" kp="100" kv="5"
        ctrlrange="-.5 .5" forcerange="-5 5"/>
      <motor name="torque" joint="motorjoint" ctrlrange="-1 1"/>
    </actuator><equality><joint name="coupling" joint1="hinge" joint2="motorjoint"
      polycoef="0 1 0 0 0"/></equality></mujoco>''')
    builder = newton.ModelBuilder()
    SolverMuJoCo.register_custom_attributes(builder)
    builder.add_mjcf(str(source), collapse_fixed_joints=False, ctrl_direct=False)
    model = builder.finalize(device="cpu")
    raw = tmp_path / "constructor.xml"
    solver = build_cpu_mujoco_solver(model, save_to_mjcf=str(raw))
    return model, solver, raw, tmp_path / "portable.xml"


def test_generated_identity_free_layout_and_named_motor_survive_rss_import(snapshot):
    import mujoco
    import robosimstudio as rss
    from robosimstudio.specs import ArrangementSpec
    from agent.rss_scene import TaskSceneSpec
    from agent.rss_runtime import cpu_mujoco_backend
    from agent.rss_position_targets import CpuPositionTargetOutlet
    from agent.rss_mjcf_export import export_portable_mjcf

    model, solver, raw, output = snapshot
    native = solver.mj_model
    original_bytes = raw.read_bytes()
    native_arrays = {key: getattr(native, key).copy() for key in
                     ("body_mass", "body_inertia", "jnt_range", "actuator_gainprm", "actuator_biasprm")}
    assert [native.actuator(i).name for i in range(native.nu)] == ["", ""]
    report = export_portable_mjcf(model, solver, raw, output)
    servo, motor = report["actuator_mapping"]
    assert servo["exported_name"] == servo["newton_joint_label"] + "/__position_servo"
    assert servo["q_slot"] == servo["target_slot"] == 7
    assert servo["qd_slot"] == 6 and servo["control_source"] == 0
    assert motor["exported_name"] == "torque" and motor["control_source"] == 1
    assert raw.read_bytes() == original_bytes and solver.mj_data.time == 0
    for key, before in native_arrays.items():
        np.testing.assert_array_equal(getattr(native, key), before)
    scene = rss.Scene(cell="void", physics=ArrangementSpec(
        backends=(("*", cpu_mujoco_backend()),)))
    scene.add(TaskSceneSpec(path=str(output)))
    sim = scene.build(device="cpu", warmup=False, use_cuda_graph=False)
    try:
        # No reset: RSS reset has a warmup path that would step physics.
        labels = list(sim.model.mujoco.actuator_label)
        selected = [label for label in labels if label.endswith(servo["exported_name"])]
        assert len(selected) == 1
        outlet = CpuPositionTargetOutlet(sim.model, sim.pipeline.solver, selected)
        targets = sim.control.joint_target_q.numpy().copy()
        targets[outlet.target_slots] = .2
        sim.control.joint_target_q.assign(targets)
        direct = sim.control.mujoco.ctrl.numpy().copy()
        motor_slot = next(i for i, label in enumerate(labels) if label.endswith("/torque") or label == "torque")
        direct[motor_slot] = .125
        sim.control.mujoco.ctrl.assign(direct)
        outlet.apply(sim.control)
        assert sim.control.mujoco.ctrl.numpy()[outlet.ctrl_slots[0]] == pytest.approx(.2)
        assert sim.control.mujoco.ctrl.numpy()[motor_slot] == .125
        assert sim.pipeline.solver.mj_data.time == 0
        roundtrip = sim.pipeline.solver.mj_model
        # RSS adds its own floor: compare actual imported robot collision geoms,
        # not the unrelated scene floor. This catches MjSpec scalar shorthand
        # being incorrectly padded with Newton's unauthored zero sentinel.
        robot_geoms = roundtrip.geom_type != int(mujoco.mjtGeom.mjGEOM_PLANE)
        np.testing.assert_allclose(roundtrip.geom_solref[robot_geoms], native.geom_solref)
        np.testing.assert_allclose(roundtrip.geom_solimp[robot_geoms], native.geom_solimp)
        np.testing.assert_allclose(roundtrip.jnt_solref, native.jnt_solref)
        for row in report["actuator_mapping"]:
            slot = next(i for i, label in enumerate(labels) if label.endswith(row["exported_name"]))
            indices = sim.pipeline.solver.mjc_actuator_to_newton_idx.numpy()
            a = int(np.flatnonzero(indices == slot)[0])
            for key in ("actuator_gainprm", "actuator_biasprm", "actuator_forcerange", "actuator_ctrlrange"):
                np.testing.assert_allclose(getattr(roundtrip, key)[a], getattr(native, key)[row["native_actuator"]])
    finally:
        sim.close()


def test_native_snapshot_limit_profile_gravity_contact_and_discrete(snapshot):
    import mujoco
    from agent.rss_mjcf_export import export_portable_mjcf
    model, solver, raw, output = snapshot
    native = solver.mj_model
    native.jnt_solref[1] = [.003, 1.2]
    native.jnt_solimp[1] = [.98, .99, .001, .5, 2]
    native.body_gravcomp[2] = 1
    native.eq_solref[0] = [.002, 1]
    native.eq_solimp[0] = [.9999, .9999, .001, .5, 2]
    native.geom_solref[1] = [.004, 1]
    if hasattr(mujoco.mjtIntegrator, "mjINT_DISCRETE"):
        native.opt.integrator = mujoco.mjtIntegrator.mjINT_DISCRETE
    report = export_portable_mjcf(model, solver, raw, output)
    exported = mujoco.MjModel.from_xml_path(str(output))
    for key in ("jnt_solref", "jnt_solimp", "body_gravcomp", "geom_solref", "eq_solref", "eq_solimp"):
        np.testing.assert_allclose(getattr(exported, key), getattr(native, key))
        assert key in {change["field"] for change in report["restored_fields"]}
    assert exported.opt.integrator == native.opt.integrator
    assert exported.nq == 9 and exported.nv == 8
    assert solver.mj_data.time == 0


def test_reject_mismatched_constructor_and_unknown_actuator_identity(snapshot):
    from agent.rss_mjcf_export import export_portable_mjcf
    model, solver, raw, output = snapshot
    original = raw.read_text()
    # Same counts and names, materially different mass must still fail.
    raw.write_text(original.replace('mass="1"', 'mass="2"', 1))
    with pytest.raises(ValueError, match="physical mismatch"):
        export_portable_mjcf(model, solver, raw, output)
    assert not output.exists()
    raw.write_text(original)
    labels = model.mujoco.actuator_label
    old = labels[1]
    labels[1] = ""
    try:
        with pytest.raises(ValueError, match="full imported label"):
            export_portable_mjcf(model, solver, raw, output)
    finally:
        labels[1] = old
    assert not output.exists()


def test_reject_inconsistent_target_map_and_discrete_constructor_export(snapshot, tmp_path):
    import mujoco
    from agent.rss_runtime import build_cpu_mujoco_solver
    from agent.rss_mjcf_export import export_portable_mjcf
    model, solver, raw, output = snapshot
    old = solver.mjc_actuator_to_newton_target_q_idx.numpy().copy()
    bad = old.copy()
    bad[0] = 6  # The FREE joint makes qd != target-q layout.
    solver.mjc_actuator_to_newton_target_q_idx.assign(bad)
    try:
        with pytest.raises(ValueError, match="target layout"):
            export_portable_mjcf(model, solver, raw, output)
    finally:
        solver.mjc_actuator_to_newton_target_q_idx.assign(old)
    if hasattr(mujoco.mjtIntegrator, "mjINT_DISCRETE"):
        with pytest.raises(ValueError, match="export separately"):
            build_cpu_mujoco_solver(model, integrator="discrete", save_to_mjcf=str(tmp_path / "wrong.xml"))


def test_source_generated_servo_needs_no_imported_actuator_label(tmp_path):
    import newton
    import warp as wp
    from newton.solvers import SolverMuJoCo
    from agent.rss_runtime import build_cpu_mujoco_solver
    from agent.rss_mjcf_export import export_portable_mjcf
    wp.set_device("cpu")
    builder = newton.ModelBuilder()
    SolverMuJoCo.register_custom_attributes(builder)
    link = builder.add_link(mass=1, inertia=wp.mat33(.1, 0, 0, 0, .2, 0, 0, 0, .25), label="/source/arm")
    joint = builder.add_joint_revolute(-1, link, target_ke=100, target_kd=5,
                                       limit_lower=-1, limit_upper=1,
                                       label="/source/joints/driver")
    builder.add_articulation([joint])
    model = builder.finalize(device="cpu")
    assert not hasattr(model.mujoco, "actuator_label")
    raw = tmp_path / "raw.xml"
    solver = build_cpu_mujoco_solver(model, save_to_mjcf=str(raw))
    report = export_portable_mjcf(model, solver, raw, tmp_path / "portable.xml")
    assert report["actuator_mapping"][0]["exported_name"] == "/source/joints/driver/__position_servo"
    assert solver.mj_data.time == 0


def test_empty_massless_frame_com_and_quaternion_equivalence_are_narrow():
    import mujoco
    from agent.rss_mjcf_export import _compare
    xml = '''<mujoco><worldbody><body name="frame" pos="0 .3 0">
      <body name="dynamic"><joint/><geom type="box" size=".1 .2 .3" mass="1"/></body>
      </body></worldbody></mujoco>'''
    native, canonical = mujoco.MjModel.from_xml_string(xml), mujoco.MjModel.from_xml_string(xml)
    assert native.body_mass[1] == 0 and np.all(native.body_inertia[1] == 0)
    canonical.body_ipos[1] = [0, -.305, 0]
    _compare(native, canonical)
    canonical.body_ipos[2, 1] += .001
    with pytest.raises(ValueError, match="body_ipos"):
        _compare(native, canonical)
    canonical.body_ipos[2] = native.body_ipos[2]
    # A nonzero inertia, however tiny, must not inherit the empty-frame exemption.
    native.body_inertia[1] = canonical.body_inertia[1] = [.00001] * 3
    with pytest.raises(ValueError, match="body_ipos"):
        _compare(native, canonical)
    native.body_inertia[1] = canonical.body_inertia[1] = 0
    # Sign and a tiny XML-precision rotation near zero quaternion components.
    canonical.geom_quat[0] = -native.geom_quat[0]
    _compare(native, canonical)
    mujoco.mju_axisAngle2Quat(canonical.geom_quat[0], np.array([0., 0., 1.]), 5e-6)
    _compare(native, canonical)
    mujoco.mju_axisAngle2Quat(canonical.geom_quat[0], np.array([0., 0., 1.]), .001)
    with pytest.raises(ValueError, match="geom_quat"):
        _compare(native, canonical)


def test_runtime_physics_fields_cannot_disappear_silently(snapshot):
    from agent.rss_mjcf_export import export_portable_mjcf
    model, solver, raw, output = snapshot
    native = solver.mj_model
    # These new MuJoCo physics parameters are compared, never silently repaired
    # from an older constructor XML. Older runtimes lack some of the fields.
    fields = ["geom_surfacevel", "geom_adhesion", "jnt_stiffnesspoly",
              "dof_dampingpoly", "actuator_dampingpoly", "actuator_delay",
              "actuator_ctrlspec", "actuator_history", "actuator_length0"]
    checked = []
    for key in fields:
        if not hasattr(native, key):
            continue
        array = getattr(native, key)
        if not array.size:
            continue
        before = array.copy()
        array.flat[0] += 1
        try:
            with pytest.raises(ValueError, match=key):
                export_portable_mjcf(model, solver, raw, output)
            assert not output.exists()
            checked.append(key)
        finally:
            array[:] = before
    assert "actuator_length0" in checked
    assert solver.mj_data.time == 0


@pytest.mark.parametrize("field,value", [("geom_quat", np.nan), ("geom_quat", 2.),
                                         ("body_iquat", np.nan), ("body_iquat", 2.),
                                         ("body_inertia", np.nan), ("body_inertia", np.inf)])
def test_export_refuses_nonfinite_or_nonunit_native_snapshot(snapshot, field, value):
    from agent.rss_mjcf_export import export_portable_mjcf
    model, solver, raw, output = snapshot
    array = getattr(solver.mj_model, field)
    before = array.copy()
    array[0, 0] = value
    try:
        with pytest.raises(ValueError, match="finite|quaternion|inertia"):
            export_portable_mjcf(model, solver, raw, output)
        assert not output.exists()
    finally:
        array[:] = before
    assert solver.mj_data.time == 0


@pytest.mark.parametrize("addition", ['<site name="tool" pos=".1 0 0"/>',
                                      '<geom type="hfield" hfield="terrain"/>'])
def test_unqualified_site_and_heightfield_rejected_before_export(tmp_path, addition):
    import newton
    import warp as wp
    from newton.solvers import SolverMuJoCo
    from agent.rss_runtime import build_cpu_mujoco_solver
    from agent.rss_mjcf_export import export_portable_mjcf
    wp.set_device("cpu")
    source = tmp_path / "unsupported.xml"
    asset = ('<asset><hfield name="terrain" nrow="2" ncol="2" size="1 1 .1 .1" '
             'elevation="0 0 0 0"/></asset>') if "hfield" in addition else ""
    source.write_text(f'<mujoco>{asset}<worldbody>{addition}<body><joint/>'
                      '<geom type="box" size=".1 .2 .3"/></body></worldbody></mujoco>')
    builder = newton.ModelBuilder()
    SolverMuJoCo.register_custom_attributes(builder)
    builder.add_mjcf(str(source), collapse_fixed_joints=False)
    model = builder.finalize(device="cpu")
    raw = tmp_path / "raw.xml"
    solver = build_cpu_mujoco_solver(model, save_to_mjcf=str(raw))
    assert solver.mj_model.nsite or solver.mj_model.nhfield
    with pytest.raises(ValueError, match="sites, height fields"):
        export_portable_mjcf(model, solver, raw, tmp_path / "portable.xml")
    assert solver.mj_data.time == 0


def test_solref_expansion_preserves_native_defaults_negative_and_nondefault_values(snapshot):
    import mujoco
    import xml.etree.ElementTree as ET
    import newton
    from newton.solvers import SolverMuJoCo
    from agent.rss_runtime import build_cpu_mujoco_solver
    from agent.rss_mjcf_export import export_portable_mjcf
    model, solver, raw, output = snapshot
    native = solver.mj_model
    native.geom_solref[1] = [-100., -10.]
    native.geom_solref[2] = [.007, .7]
    native.jnt_solref[1] = [.008, 1.]
    report = export_portable_mjcf(model, solver, raw, output)
    assert report["explicit_solver_vector_expansions"]
    tree = ET.parse(output)
    for tag, attribute in (("geom", "solref"), ("joint", "solreflimit")):
        for node in tree.findall(f".//worldbody//{tag}"):
            if attribute in node.attrib:
                assert len(node.get(attribute).split()) == 2
    # Native compilation has already been checked by the exporter. The second
    # import exercises Newton's separate custom-attribute route as well.
    builder = newton.ModelBuilder()
    SolverMuJoCo.register_custom_attributes(builder)
    builder.add_mjcf(str(output), collapse_fixed_joints=False, ctrl_direct=True)
    imported = builder.finalize(device="cpu")
    second = build_cpu_mujoco_solver(imported)
    np.testing.assert_allclose(second.mj_model.geom_solref, native.geom_solref)
    np.testing.assert_allclose(second.mj_model.jnt_solref, native.jnt_solref)
    assert solver.mj_data.time == second.mj_data.time == 0
