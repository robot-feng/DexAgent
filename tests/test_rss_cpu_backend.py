"""Exercise RSS compilation, imported gravity and native CPU contact feedback."""
import pytest


@pytest.mark.parametrize("integrator", ["implicitfast", "discrete"])
def test_cpu_backend_preserves_imported_gravity_and_contact_feedback(tmp_path, integrator):
    mujoco = pytest.importorskip("mujoco")
    if integrator == "discrete" and not hasattr(mujoco.mjtIntegrator, "mjINT_DISCRETE"):
        pytest.skip("requires isolated MuJoCo 3.13 runtime")
    wp = pytest.importorskip("warp")
    wp.set_device("cpu")
    import numpy as np
    import robosimstudio as rss
    from robosimstudio.specs.physics import ArrangementSpec, get_backend
    from agent.rss_runtime import cpu_mujoco_backend
    from agent.rss_scene import CPUContactProbe, TaskSceneSpec
    from agent.rss_mujoco_cpu import CpuMuJoCoSolver

    source = tmp_path / "contact.xml"
    source.write_text('''<mujoco><worldbody>
      <geom name="floor" type="plane" size="1 1 .1"/>
      <body name="falling" pos="0 0 .2"><freejoint/>
        <geom name="ball" type="sphere" size=".02" mass=".1"/>
      </body>
      <body name="hovering" pos=".2 0 .2" gravcomp="1"><freejoint/>
        <geom type="sphere" size=".02" mass=".1"/>
      </body>
    </worldbody></mujoco>''')
    standard = get_backend("mujoco")
    physics = ArrangementSpec(control_hz=100, substeps=10,
                              backends=(("*", cpu_mujoco_backend(integrator=integrator)),))
    scene = rss.Scene(cell="void", physics=physics)
    scene.add(TaskSceneSpec(path=str(source)))
    sim = scene.build(device="cpu", warmup=False, use_cuda_graph=False)
    try:
        solver = sim.pipeline.solver
        assert isinstance(solver, CpuMuJoCoSolver)
        expected = getattr(mujoco.mjtIntegrator, "mjINT_" + integrator.upper())
        assert solver.use_mujoco_cpu and solver.mj_model.opt.integrator == expected
        assert sim.scene.program.islands[0].uses_native_contacts()
        # RSS's generic probe currently recognizes only its standard alias.
        # Use the existing native CPU probe, as the Sharpa execution bridge does.
        sim.contacts = CPUContactProbe(solver, sim.model, sim.pipeline.sim_dt)
        m, d = solver.mj_model, solver.mj_data
        bids = {name: next(i for i in range(1, m.nbody) if m.body(i).name.endswith(name))
                for name in ("falling", "hovering")}
        assert m.body_gravcomp[bids["hovering"]] == 1
        assert m.body_gravcomp[bids["falling"]] == 0
        for _ in range(50):
            sim.step_teleop()
        assert d.time == pytest.approx(.5)
        assert .019 < d.xpos[bids["falling"], 2] < .021
        assert d.xpos[bids["hovering"], 2] == pytest.approx(.2, abs=1e-5)
        assert d.ncon > 0 and np.isfinite(sim.state.body_q.numpy()).all()
        sim.contacts.update(sim.pipeline.step_count)
        ball_shapes = sim.contacts.geom_to_shape[m.geom_bodyid == bids["falling"]]
        ground_shapes = sim.contacts.geom_to_shape[m.geom_bodyid == 0]
        assert sim.contacts.in_contact(ball_shapes, ground_shapes)
        force = sim.contacts.pairwise_force(ball_shapes, ground_shapes)
        assert force[2] == pytest.approx(.1 * 9.81, rel=.03)
        assert get_backend("mujoco") == standard
    finally:
        sim.close()
