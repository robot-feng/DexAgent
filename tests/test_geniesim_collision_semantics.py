"""Small native USD regressions for the Omnipicker collision import boundary."""
import os

import pytest

from agent import geniesim_import


@pytest.fixture
def native():
    pytest.importorskip("pxr")
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    wp = pytest.importorskip("warp")
    wp.config.enable_cuda = False
    newton = pytest.importorskip("newton")
    from newton.solvers import SolverMuJoCo
    from newton.usd import SchemaResolverNewton, SchemaResolverPhysx
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    return (newton, SolverMuJoCo, SchemaResolverNewton, SchemaResolverPhysx,
            Gf, Sdf, Usd, UsdGeom, UsdPhysics)


def make_stage(tmp_path, native, *, physx=False, newton_override=None,
               root_on_fixed_joint=False):
    """Three articulated cubes, plus a separate sphere touching the last cube."""
    _, _, _, _, Gf, Sdf, Usd, UsdGeom, UsdPhysics = native
    path = tmp_path / "robot.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    root = UsdGeom.Xform.Define(stage, "/Robot").GetPrim()
    stage.SetDefaultPrim(root)
    def body(path, position):
        transform = UsdGeom.Xform.Define(stage, path)
        transform.AddTranslateOp().Set(Gf.Vec3d(*position))
        prim = transform.GetPrim()
        UsdPhysics.RigidBodyAPI.Apply(prim)
        mass = UsdPhysics.MassAPI.Apply(prim)
        mass.CreateMassAttr(1.0)
        mass.CreateDiagonalInertiaAttr(Gf.Vec3f(0.01))
        return prim

    for i in range(3):
        body(f"/Robot/link{i}", (i * 0.25, 0, 0.2))
        cube = UsdGeom.Cube.Define(stage, f"/Robot/link{i}/collision")
        cube.CreateSizeAttr(0.1)
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    fixed = UsdPhysics.FixedJoint.Define(stage, "/Robot/fixed")
    fixed.CreateBody1Rel().SetTargets(["/Robot/link0"])
    fixed.CreateLocalPos0Attr(Gf.Vec3f(0, 0, 0.2))
    articulation_root = fixed.GetPrim() if root_on_fixed_joint else root
    UsdPhysics.ArticulationRootAPI.Apply(articulation_root)
    articulation_root.CreateAttribute("physxArticulation:enabledSelfCollisions",
                                      Sdf.ValueTypeNames.Bool).Set(physx)
    if newton_override is not None:
        articulation_root.CreateAttribute("newton:selfCollisionEnabled",
                                          Sdf.ValueTypeNames.Bool).Set(newton_override)
    for i in range(2):
        joint = UsdPhysics.RevoluteJoint.Define(stage, f"/Robot/joint{i}")
        joint.CreateBody0Rel().SetTargets([f"/Robot/link{i}"])
        joint.CreateBody1Rel().SetTargets([f"/Robot/link{i + 1}"])
        joint.CreateLocalPos0Attr(Gf.Vec3f(0.25, 0, 0))
        joint.CreateAxisAttr("Z")
        joint.CreateLowerLimitAttr(-45)
        joint.CreateUpperLimitAttr(45)
    body("/External", (0.5, 0.06, 0.2))
    sphere = UsdGeom.Sphere.Define(stage, "/External/collision")
    sphere.CreateRadiusAttr(0.025)
    UsdPhysics.CollisionAPI.Apply(sphere.GetPrim())
    stage.GetRootLayer().Save()
    return path, stage


def import_stage(stage, native):
    newton, SolverMuJoCo, NewtonResolver, PhysxResolver, *_ = native
    builder = newton.ModelBuilder()
    SolverMuJoCo.register_custom_attributes(builder)
    builder.add_usd(
        stage, collapse_fixed_joints=False,
        schema_resolvers=[NewtonResolver(), PhysxResolver()],
    )
    return builder


def shape_ids(builder):
    return {path: i for i, path in enumerate(builder.shape_label)}


def filtered(builder, a, b):
    ids = shape_ids(builder)
    pair = tuple(sorted((ids[a], ids[b])))
    return pair in {tuple(sorted(p)) for p in builder.shape_collision_filter_pairs}


@pytest.mark.parametrize(
    "physx,newton_override,expected",
    [(False, None, False), (True, None, True), (False, True, True)],
)
def test_native_qualification_preserves_collision_semantics(
        tmp_path, native, physx, newton_override, expected):
    path, stage = make_stage(
        tmp_path, native, physx=physx, newton_override=newton_override)
    report = geniesim_import.qualify(path, tmp_path / "report.json")
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["native_rss_import"]["status"] == "passed"
    assert checks["native_cpu_solver_build"]["status"] == "passed"
    assert checks["authored_self_collision_filters_preserved"]["status"] == "passed"
    root = next(a for a in report["source_inventory"]["articulations"]
                if a["path"] == "/Robot")
    assert root["self_collision_enabled"] is expected
    assert root["attribute"] == (
        "newton:selfCollisionEnabled" if newton_override is not None
        else "physxArticulation:enabledSelfCollisions")
    assert report["task_success_evaluated"] is False

    # Inspect real imported filters, including the non-adjacent pair that
    # ordinary parent-child filtering would leave collidable.
    builder = import_stage(stage, native)
    assert filtered(builder, "/Robot/link0/collision",
                    "/Robot/link2/collision") is (not expected)
    if not expected:
        assert filtered(builder, "/Robot/link0/collision", "/Robot/link1/collision")
        assert filtered(builder, "/Robot/link1/collision", "/Robot/link2/collision")
    for i in range(3):
        assert not filtered(builder, f"/Robot/link{i}/collision", "/External/collision")

    # Solver construction performs native collision detection, without stepping.
    # Assert a real external contact, not only the absence of an exclusion.
    _, SolverMuJoCo, *_ = native
    solver = SolverMuJoCo(builder.finalize(device="cpu"), use_mujoco_cpu=True)
    external_contacts = []
    for contact in solver.mj_data.contact:
        names = [solver.mj_model.geom(int(g)).name
                 for g in (contact.geom1, contact.geom2)]
        if any("/External/collision" in name for name in names):
            external_contacts.append(names)
    assert external_contacts, "The external sphere must still collide with the robot"


def test_report_rejects_missing_nonadjacent_filter(tmp_path, native):
    _, stage = make_stage(tmp_path, native, physx=False)
    builder = import_stage(stage, native)
    source = geniesim_import.source_inventory(stage)
    imported = geniesim_import.builder_inventory(builder)
    pairs = {tuple(sorted(pair)) for pair in builder.shape_collision_filter_pairs}
    intact = geniesim_import.compare_collision_filters(source, imported, pairs)
    assert intact["status"] == "passed"
    assert intact["evidence"][0]["expected_filtered_pairs"] == 3
    assert intact["evidence"][0]["missing_filtered_pairs"] == 0
    ids = shape_ids(builder)
    pairs.remove(tuple(sorted((ids["/Robot/link0/collision"],
                               ids["/Robot/link2/collision"]))))
    broken = geniesim_import.compare_collision_filters(source, imported, pairs)
    assert broken["id"] == "authored_self_collision_filters_preserved"
    assert broken["status"] == "failed"
    assert broken["required"] is True
    assert broken["evidence"][0]["expected_filtered_pairs"] == 3
    assert broken["evidence"][0]["missing_filtered_pairs"] == 1
    assert {frozenset(pair) for pair in broken["evidence"][0]["examples"]} == {
        frozenset(("/Robot/link0/collision", "/Robot/link2/collision"))}


def test_joint_root_membership_uses_native_articulation_graph(tmp_path, native):
    path, stage = make_stage(tmp_path, native, root_on_fixed_joint=True)
    source = geniesim_import.source_inventory(stage)
    root = next(a for a in source["articulations"] if a["path"] == "/Robot/fixed")
    assert root["self_collision_enabled"] is False
    assert set(root["articulated_bodies"]) == {
        "/Robot/link0", "/Robot/link1", "/Robot/link2"}
    assert "/External" not in root["articulated_bodies"]
    assert all(not body.startswith(root["path"] + "/")
               for body in root["articulated_bodies"])

    builder = import_stage(stage, native)
    imported = geniesim_import.builder_inventory(builder)
    pairs = {tuple(sorted(pair)) for pair in builder.shape_collision_filter_pairs}
    result = geniesim_import.compare_collision_filters(source, imported, pairs)
    assert result["status"] == "passed"
    assert result["evidence"][0]["expected_filtered_pairs"] == 3
    assert result["evidence"][0]["missing_filtered_pairs"] == 0
    ids = shape_ids(builder)
    pairs.remove(tuple(sorted((ids["/Robot/link0/collision"],
                               ids["/Robot/link2/collision"]))))
    broken = geniesim_import.compare_collision_filters(source, imported, pairs)
    assert broken["status"] == "failed"
    assert broken["evidence"][0]["missing_filtered_pairs"] == 1

    report = geniesim_import.qualify(path, tmp_path / "joint_root_report.json")
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["native_cpu_solver_build"]["status"] == "passed"
    assert checks["authored_self_collision_filters_preserved"]["status"] == "passed"
    assert checks["authored_self_collision_filters_preserved"][
        "evidence"][0]["expected_filtered_pairs"] == 3


@pytest.mark.parametrize("membership", [None, []])
def test_report_rejects_unverified_articulation_membership(tmp_path, native, membership):
    _, stage = make_stage(tmp_path, native)
    source = geniesim_import.source_inventory(stage)
    builder = import_stage(stage, native)
    imported = geniesim_import.builder_inventory(builder)
    root = next(a for a in source["articulations"] if a["path"] == "/Robot")
    if membership is None:
        root.pop("articulated_bodies", None)
    else:
        root["articulated_bodies"] = membership
    result = geniesim_import.compare_collision_filters(
        source, imported, builder.shape_collision_filter_pairs)
    assert result["status"] == "failed"
    assert result["required"] is True


@pytest.mark.parametrize("explicit_zero", [False, True])
def test_empty_fixed_frame_inertia_change_remains_a_fidelity_failure(tmp_path, native, explicit_zero):
    """An explanation of finalization must not turn lost source inertia green."""
    _, _, _, _, Gf, _, _, UsdGeom, UsdPhysics = native
    path, stage = make_stage(tmp_path, native)
    frame = UsdGeom.Xform.Define(stage, "/Robot/tcp").GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(frame)
    mass = UsdPhysics.MassAPI.Apply(frame)
    if explicit_zero:
        mass.CreateMassAttr(0.0)
    mass.CreateDiagonalInertiaAttr(Gf.Vec3f(0.0001))
    fixed = UsdPhysics.FixedJoint.Define(stage, "/Robot/tcp_fixed")
    fixed.CreateBody0Rel().SetTargets(["/Robot/link2"])
    fixed.CreateBody1Rel().SetTargets(["/Robot/tcp"])
    stage.GetRootLayer().Save()

    report = geniesim_import.qualify(path, tmp_path / "frame_report.json")
    checks = {c["id"]: c for c in report["checks"]}
    assert checks["native_cpu_solver_build"]["status"] == "passed"
    assert checks["finalized_authored_mass_principal_inertia_preserved"]["status"] == "failed"
    assert report["status"] == "failed"
    assert report["task_success_evaluated"] is False
    change, = report["runtime_inertia_changes"]
    assert change["path"] == "/Robot/tcp"
    assert change["source"]["mass"] == 0
    assert change["source"]["mass_authored"] is explicit_zero
    assert change["source"]["density_authored"] is False
    assert change["source"]["diagonal_inertia_authored"] is True
    assert change["source"]["geometry_paths"] == []
    assert [j["path"] for j in change["attached_joints"]] == ["/Robot/tcp_fixed"]
    assert change["before_finalize"]["inertia"][0][0] == pytest.approx(0.0001)
    assert change["after_finalize"]["inertia"] == [[0.0] * 3] * 3


@pytest.mark.parametrize("child_offset", [0.0, 0.001])
def test_native_ball_loop_audit_rejects_recomputed_child_anchor(tmp_path, native, child_offset):
    _, _, _, _, Gf, _, _, _, UsdPhysics = native
    path, stage = make_stage(tmp_path, native)
    loop = UsdPhysics.SphericalJoint.Define(stage, "/Robot/loop")
    loop.CreateBody0Rel().SetTargets(["/Robot/link0"])
    loop.CreateBody1Rel().SetTargets(["/Robot/link2"])
    loop.CreateLocalPos0Attr(Gf.Vec3f(0.5, 0, 0))
    loop.CreateLocalPos1Attr(Gf.Vec3f(child_offset, 0, 0))
    loop.CreateExcludeFromArticulationAttr(True)
    stage.GetRootLayer().Save()
    report = geniesim_import.qualify(path, tmp_path / "loop_report.json")
    checks = {c["id"]: c for c in report["checks"]}
    assert checks["native_cpu_solver_build"]["status"] == "passed"
    audit = checks["native_ball_loop_anchors_preserved"]
    assert audit["required"] is True
    assert audit["status"] == ("passed" if child_offset == 0 else "failed")
    entry, = audit["evidence"]
    assert entry["path"] == "/Robot/loop"
    assert entry["bodies_match"] is True
    assert entry["expected_anchors"][3] == pytest.approx(child_offset)
    assert entry["max_anchor_component_error_m"] == pytest.approx(child_offset, abs=1e-7)
    assert report["task_success_evaluated"] is False


def test_authored_body_gravity_mapping_keeps_other_objects_falling(tmp_path, native):
    newton, SolverMuJoCo, _, _, Gf, Sdf, Usd, UsdGeom, UsdPhysics = native
    path = tmp_path / "gravity.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    for name, disabled, x in [("hover", True, 0), ("fall", False, 1)]:
        transform = UsdGeom.Xform.Define(stage, "/" + name)
        transform.AddTranslateOp().Set(Gf.Vec3d(x, 0, 1))
        prim = transform.GetPrim()
        UsdPhysics.RigidBodyAPI.Apply(prim)
        mass = UsdPhysics.MassAPI.Apply(prim)
        mass.CreateMassAttr(1)
        mass.CreateDiagonalInertiaAttr(Gf.Vec3f(0.01))
        prim.CreateAttribute("physxRigidBody:disableGravity", Sdf.ValueTypeNames.Bool).Set(disabled)
        cube = UsdGeom.Cube.Define(stage, f"/{name}/collision")
        cube.CreateSizeAttr(0.1)
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    stage.GetRootLayer().Save()
    original = path.read_bytes()
    report = geniesim_import.qualify(path, tmp_path / "gravity_report.json")
    checks = {c["id"]: c for c in report["checks"]}
    assert checks["authored_body_gravity_preserved"]["status"] == "passed"
    assert len(checks["authored_body_gravity_preserved"]["evidence"]) == 2
    assert path.read_bytes() == original

    geniesim_import.map_physx_body_gravity(stage)
    builder = import_stage(stage, native)
    model = builder.finalize(device="cpu")
    solver = SolverMuJoCo(model, use_mujoco_cpu=True)
    state, other, control = model.state(), model.state(), model.control()
    newton.eval_fk(model, state.joint_q, state.joint_qd, state)
    for _ in range(100):
        state.clear_forces()
        solver.step(state, other, control, None, 0.001)
        state, other = other, state
    positions = state.body_q.numpy()
    assert positions[list(model.body_label).index("/hover"), 2] == pytest.approx(1, abs=1e-5)
    assert positions[list(model.body_label).index("/fall"), 2] < 0.97
    assert path.read_bytes() == original


def test_conflicting_body_gravity_opinions_reject_without_session_edits(tmp_path, native):
    _, _, _, _, _, Sdf, _, _, _ = native
    _, stage = make_stage(tmp_path, native)
    first = stage.GetPrimAtPath("/Robot/link0")
    first.CreateAttribute("physxRigidBody:disableGravity", Sdf.ValueTypeNames.Bool).Set(True)
    conflict = stage.GetPrimAtPath("/Robot/link1")
    conflict.CreateAttribute("physxRigidBody:disableGravity", Sdf.ValueTypeNames.Bool).Set(True)
    conflict.CreateAttribute("mjc:gravcomp", Sdf.ValueTypeNames.Float).Set(0.0)
    before = stage.GetSessionLayer().ExportToString()
    with pytest.raises(ValueError, match="Conflicting authored gravity"):
        geniesim_import.map_physx_body_gravity(stage)
    assert stage.GetSessionLayer().ExportToString() == before


def set_offsets(stage, native, path="/Robot/link0/collision", **values):
    Sdf = native[5]
    names = {"contact": "physxCollision:contactOffset", "rest": "physxCollision:restOffset",
             "gap": "newton:contactGap", "margin": "newton:contactMargin"}
    prim = stage.GetPrimAtPath(path)
    for key, value in values.items():
        prim.CreateAttribute(names[key], Sdf.ValueTypeNames.Float).Set(value)
    return prim


@pytest.mark.parametrize("rest", [None, float("-inf"), 0.0])
def test_rigid_contact_offset_qualifies_without_changing_source(tmp_path, native, rest):
    path, stage = make_stage(tmp_path, native)
    set_offsets(stage, native, contact=.001, **({} if rest is None else {"rest": rest}))
    stage.GetRootLayer().Save()
    before = path.read_bytes()
    report = geniesim_import.qualify(path, tmp_path / "offsets.json")
    checks = {c["id"]: c for c in report["checks"]}
    for name in ["native_cpu_solver_build", "imported_collision_offsets_preserved",
                 "native_collision_offsets_preserved", "source_layers_unchanged"]:
        assert checks[name]["status"] == "passed", checks[name]
    row, = checks["native_collision_offsets_preserved"]["evidence"]
    assert row["expected"] == pytest.approx({"gap_m": .001, "margin_m": 0})
    assert row["hull_count"] == row["native_geom_count"] == 1
    adaptations = [a for a in report["adaptations"]
                   if a["operation"] == "resolve_physx_rigid_collision_rest_default"]
    assert bool(adaptations) is (rest != 0.0)
    if adaptations:
        entry, = adaptations[0]["colliders"]
        assert entry["rigid_body"] == "/Robot/link0"
        assert entry["resolved_rest_offset_m"] == 0
        assert "not source-authored" in entry["rest_resolution"]
        assert adaptations[0]["source_layers_modified"] is False
    assert path.read_bytes() == before
    source = next(c for c in report["source_inventory"]["collisions"]
                  if c["path"] == "/Robot/link0/collision")
    assert source["offsets"]["source_values"]["rest"] == (
        None if rest is None else "-inf" if rest == float("-inf") else 0)
    assert report["sweep"]["simulation_steps"] == 0
    assert report["task_success_evaluated"] is False


def test_offset_mapping_is_anonymous_and_retains_newton_priority(tmp_path, native):
    path, stage = make_stage(tmp_path, native)
    set_offsets(stage, native, contact=.001)
    set_offsets(stage, native, "/Robot/link1/collision", contact=.001, gap=.003)
    set_offsets(stage, native, "/Robot/link2/collision", contact=.001, rest=.0002)
    stage.GetRootLayer().Save()
    before, root = path.read_bytes(), stage.GetRootLayer().ExportToString()
    source = geniesim_import.source_inventory(stage)
    mapped = geniesim_import.map_physx_collision_offsets(stage)
    assert [entry["path"] for entry in mapped] == ["/Robot/link0/collision"]
    assert stage.GetRootLayer().ExportToString() == root and path.read_bytes() == before
    assert "physxCollision:restOffset" in stage.GetSessionLayer().ExportToString()
    assert not geniesim_import.map_physx_collision_offsets(stage)  # Idempotent.
    builder = import_stage(stage, native)
    imported = geniesim_import.builder_inventory(builder)
    assert geniesim_import.compare_collision_offsets(source, imported)["status"] == "passed"
    shapes = {s["path"]: s for s in imported["collisions"]}
    assert shapes["/Robot/link0/collision"]["gap_m"] == pytest.approx(.001)
    assert shapes["/Robot/link1/collision"]["gap_m"] == pytest.approx(.003)
    assert shapes["/Robot/link2/collision"]["gap_m"] == pytest.approx(.0008)
    assert shapes["/Robot/link2/collision"]["margin_m"] == pytest.approx(.0002)


@pytest.mark.parametrize("contact", [None, float("-inf")])
def test_unspecified_contact_offsets_keep_existing_defaults(tmp_path, native, contact):
    _, stage = make_stage(tmp_path, native)
    if contact is not None:
        set_offsets(stage, native, contact=contact)
    before = stage.GetSessionLayer().ExportToString()
    assert geniesim_import.map_physx_collision_offsets(stage) == []
    assert stage.GetSessionLayer().ExportToString() == before
    builder = import_stage(stage, native)
    assert builder.shape_gap[shape_ids(builder)["/Robot/link0/collision"]] == .1


@pytest.mark.parametrize("values", [
    {"contact": -.001}, {"contact": float("inf")}, {"contact": float("nan")},
    {"contact": .001, "rest": -.0001}, {"contact": .001, "rest": .002},
    {"contact": .001, "rest": float("inf")}, {"gap": -.1}, {"margin": float("-inf")},
])
def test_invalid_offsets_reject_before_any_session_opinion(tmp_path, native, values):
    _, stage = make_stage(tmp_path, native)
    set_offsets(stage, native, contact=.001)  # Valid candidate comes first.
    set_offsets(stage, native, "/Robot/link1/collision", **values)
    before = stage.GetSessionLayer().ExportToString()
    with pytest.raises(ValueError, match="offset|Offset|contactMargin"):
        geniesim_import.map_physx_collision_offsets(stage)
    assert stage.GetSessionLayer().ExportToString() == before


def test_offset_mapping_rejects_unknown_units_and_rigid_owner(tmp_path, native):
    _, _, _, _, _, _, _, UsdGeom, UsdPhysics = native
    _, stage = make_stage(tmp_path, native)
    set_offsets(stage, native, contact=.001)
    UsdGeom.SetStageMetersPerUnit(stage, .01)
    before = stage.GetSessionLayer().ExportToString()
    with pytest.raises(ValueError, match="metersPerUnit"):
        geniesim_import.map_physx_collision_offsets(stage)
    assert stage.GetSessionLayer().ExportToString() == before
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    stage.GetPrimAtPath("/Robot/link0").RemoveAPI(UsdPhysics.RigidBodyAPI)
    with pytest.raises(ValueError, match="rigid-body owner"):
        geniesim_import.map_physx_collision_offsets(stage)
    assert stage.GetSessionLayer().ExportToString() == before


@pytest.mark.parametrize("disabled", ["collision", "body"])
def test_disabled_collision_or_body_is_not_adapted(tmp_path, native, disabled):
    _, stage = make_stage(tmp_path, native)
    prim = set_offsets(stage, native, contact=.001)
    UsdPhysics = native[-1]
    if disabled == "collision":
        UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr(False)
    else:
        UsdPhysics.RigidBodyAPI(stage.GetPrimAtPath("/Robot/link0")).CreateRigidBodyEnabledAttr(False)
    before = stage.GetSessionLayer().ExportToString()
    assert geniesim_import.map_physx_collision_offsets(stage) == []
    assert stage.GetSessionLayer().ExportToString() == before


def test_instance_proxy_is_not_silently_edited(tmp_path, native):
    _, stage = make_stage(tmp_path, native)
    set_offsets(stage, native, contact=.001)
    instance = stage.DefinePrim("/Instance")
    instance.GetReferences().AddInternalReference("/Robot/link0")
    instance.SetInstanceable(True)
    assert stage.GetPrimAtPath("/Instance/collision").IsInstanceProxy()
    before = stage.GetSessionLayer().ExportToString()
    with pytest.raises(ValueError, match="instance proxy"):
        geniesim_import.map_physx_collision_offsets(stage)
    assert stage.GetSessionLayer().ExportToString() == before


def test_each_decomposition_hull_offset_is_checked(tmp_path, native):
    """Use actual Newton convex parts; no CoACD job or dynamics is needed."""
    _, stage = make_stage(tmp_path, native)
    set_offsets(stage, native, contact=.001)
    source = geniesim_import.source_inventory(stage)
    geniesim_import.map_physx_collision_offsets(stage)
    builder = import_stage(stage, native)
    newton, SolverMuJoCo, *_ = native
    path = "/Robot/link0/collision"
    first = shape_ids(builder)[path]
    mesh = newton.Mesh(vertices=[[0, 0, 0], [.02, 0, 0], [0, .02, 0], [0, 0, .02]],
                       indices=[0, 2, 1, 0, 1, 3, 1, 2, 3, 0, 3, 2])
    # This is Newton approximate_meshes' exact additional-hull label convention.
    for i in [1, 2]:
        builder.add_shape_convex_hull(body=builder.shape_body[first], mesh=mesh,
                                     cfg=newton.ModelBuilder.ShapeConfig(gap=.001, density=0),
                                     label=f"{path}_convex_{i}")
    imported = geniesim_import.builder_inventory(builder)
    solver = SolverMuJoCo(builder.finalize(device="cpu"), use_mujoco_cpu=True)
    result = geniesim_import.compare_collision_offsets(source, imported, solver)
    assert result["status"] == "passed", result
    assert result["evidence"][0]["hull_count"] == 3
    assert result["evidence"][0]["native_geom_count"] == 3
    extra = shape_ids(builder)[path + "_convex_2"]
    # A lost offset on just one extra part must fail both inventory and native audits.
    imported["collisions"] = [dict(s, gap_m=.1) if s["index"] == extra else s
                              for s in imported["collisions"]]
    assert geniesim_import.compare_collision_offsets(source, imported)["status"] == "failed"
    mapping = solver.mjc_geom_to_newton_shape.numpy()[0]
    solver.mj_model.geom_gap[mapping == extra] = .1
    result = geniesim_import.compare_collision_offsets(source, imported, solver)
    assert result["status"] == "failed"
    assert result["evidence"][0]["mismatched_hulls"] == 1


@pytest.mark.parametrize("values", [
    {"contact": -.001, "gap": .003},
    {"contact": float("nan"), "rest": -.002, "gap": .003, "margin": 0.},
])
def test_newton_offset_priority_ignores_overridden_physx_values(tmp_path, native, values):
    _, stage = make_stage(tmp_path, native)
    set_offsets(stage, native, **values)
    before = stage.GetSessionLayer().ExportToString()
    source = geniesim_import.source_inventory(stage)
    assert geniesim_import.map_physx_collision_offsets(stage) == []
    assert stage.GetSessionLayer().ExportToString() == before
    builder = import_stage(stage, native)
    result = geniesim_import.compare_collision_offsets(source, geniesim_import.builder_inventory(builder))
    assert result["status"] == "passed", result
    assert builder.shape_gap[shape_ids(builder)["/Robot/link0/collision"]] == pytest.approx(.003)


@pytest.mark.parametrize("values", [{"contact": .1, "rest": 0.}, {"gap": .1}])
def test_native_importer_does_not_scale_custom_offsets_in_centimetre_stage(tmp_path, native, values):
    _, stage = make_stage(tmp_path, native)
    set_offsets(stage, native, **values)
    native[-2].SetStageMetersPerUnit(stage, .01)
    # Read the actual importer result without our adapter: .1 stage units
    # should be .001 m, but custom gap remains .1. Do not silently accept that.
    builder = import_stage(stage, native)
    gap = builder.shape_gap[shape_ids(builder)["/Robot/link0/collision"]]
    assert gap == pytest.approx(.1) and gap != pytest.approx(.001)
    before = stage.GetSessionLayer().ExportToString()
    with pytest.raises(ValueError, match="metersPerUnit=1"):
        geniesim_import.map_physx_collision_offsets(stage)
    assert stage.GetSessionLayer().ExportToString() == before


def test_native_nonzero_rest_margin_loss_remains_failed_qualification(tmp_path, native):
    path, stage = make_stage(tmp_path, native)
    set_offsets(stage, native, contact=.001, rest=.0002)
    stage.GetRootLayer().Save()
    report = geniesim_import.qualify(path, tmp_path / "nonzero-rest.json")
    checks = {c["id"]: c for c in report["checks"]}
    assert checks["imported_collision_offsets_preserved"]["status"] == "passed"
    audit = checks["native_collision_offsets_preserved"]
    assert audit["status"] == "failed" and audit["required"] is True
    row, = audit["evidence"]
    assert row["expected"]["margin_m"] == pytest.approx(.0002)
    assert row["examples"][0]["actual"][0]["margin_m"] == 0
    assert row["examples"][0]["actual"][0]["gap_m"] == pytest.approx(.0008)
    assert report["status"] == "failed"
    assert report["sweep"]["simulation_steps"] == 0


def test_offset_inventory_legacy_rows_remain_readable():
    source = {"collisions": [{"path": "/old/collider", "kind": "Mesh"}]}
    result = geniesim_import.compare_collision_offsets(source, {"collisions": []})
    assert result["status"] == "passed" and result["evidence"] == []


def test_offset_overlay_rejects_persistent_session(tmp_path, native):
    _, stage = make_stage(tmp_path, native)
    set_offsets(stage, native, contact=.001)
    Sdf, Usd = native[5:7]
    session = Sdf.Layer.CreateNew(str(tmp_path / "persistent-session.usda"))
    other = Usd.Stage.Open(stage.GetRootLayer(), session)
    before = session.ExportToString()
    with pytest.raises(ValueError, match="anonymous session"):
        geniesim_import.map_physx_collision_offsets(other)
    assert session.ExportToString() == before
