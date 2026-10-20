"""Exercise the real Newton/native boundary for stiff coupled servos."""
import pytest

from agent.rss_runtime import build_cpu_mujoco_solver


def model():
    wp = pytest.importorskip('warp')
    wp.config.enable_cuda = False
    newton = pytest.importorskip('newton')
    from newton.solvers import SolverMuJoCo
    builder = newton.ModelBuilder(gravity=0)
    SolverMuJoCo.register_custom_attributes(builder)
    joints = []
    for i in range(2):
        body = builder.add_link(mass=.1, inertia=wp.mat33(
            .001278, 0, 0, 0, .001278, 0, 0, 0, .001278))
        joint = builder.add_joint_revolute(
            -1, body, axis=(0, 0, 1), target_ke=5729.578125 if i == 0 else 0,
            target_kd=1145.91565 if i == 0 else 0, effort_limit=5,
            limit_lower=-1, limit_upper=1)
        builder.add_articulation([joint])
        joints.append(joint)
    builder.add_constraint_mimic(joints[1], joints[0], coef1=-1)
    return builder.finalize(device='cpu')


def test_discrete_requires_native_support_or_uses_real_cpu_model():
    mujoco = pytest.importorskip('mujoco')
    native_model = model()
    if not hasattr(mujoco.mjtIntegrator, 'mjINT_DISCRETE'):
        with pytest.raises(RuntimeError, match='MuJoCo >= 3.13'):
            build_cpu_mujoco_solver(native_model, integrator='discrete')
        return
    import newton
    solver = build_cpu_mujoco_solver(native_model, integrator='discrete')
    assert solver.use_mujoco_cpu
    assert solver.mj_model.opt.integrator == mujoco.mjtIntegrator.mjINT_DISCRETE
    # Refreshes are used by RSS when editing a model. The CPU choice must persist.
    solver.notify_model_changed(newton.ModelFlags.ALL)
    assert solver.mj_model.opt.integrator == mujoco.mjtIntegrator.mjINT_DISCRETE
    with pytest.raises(ValueError, match='export'):
        build_cpu_mujoco_solver(native_model, integrator='discrete', save_to_mjcf='wrong.xml')


def test_native_discrete_keeps_stiff_servo_mimic_during_newton_steps():
    mujoco = pytest.importorskip('mujoco')
    if not hasattr(mujoco.mjtIntegrator, 'mjINT_DISCRETE'):
        pytest.skip('run this physical regression in the isolated MuJoCo 3.13 environment')
    import numpy as np
    import newton
    outcomes = {}
    for integrator in ['implicitfast', 'discrete']:
        m = model()
        solver = build_cpu_mujoco_solver(m, integrator=integrator)
        solver.mj_model.eq_solref[:] = [.002, 1]
        solver.mj_model.eq_solimp[:] = [.9999, .9999, .001, .5, 2]
        state, other, control = m.state(), m.state(), m.control()
        newton.eval_fk(m, state.joint_q, state.joint_qd, state)
        residual = 0
        for k in range(1900):
            t = (k + 1) * .001
            x = np.clip((t - .1) / .4, 0, 1) if t < .7 else np.clip((t - .7) / .2, 0, 1)
            value = x*x*(3-2*x)
            control.joint_target_q.assign([.1 * (value if t < .7 else 1-value), 0])
            state.clear_forces()
            solver.step(state, other, control, None, .001)
            state, other = other, state
            q = state.joint_q.numpy().copy()
            assert np.isfinite(q).all()
            residual = max(residual, abs(float(q.sum())))
            assert np.max(abs(solver.mj_data.actuator_force)) <= 5 + 1e-6
        outcomes[integrator] = residual
        if integrator == 'discrete':
            assert np.max(abs(q)) < .001
            assert solver.mj_data.time == pytest.approx(1.9)
    assert outcomes['implicitfast'] > .01
    assert outcomes['discrete'] < 1e-5
