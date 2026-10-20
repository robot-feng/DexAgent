"""Lightweight RSS execution defaults shared by the CLI and Studio inspection."""

from __future__ import annotations


def build_cpu_mujoco_solver(model, *, integrator: str = "implicitfast", **kwargs):
    """Build Newton's CPU solver with an explicitly selected native integrator.

    Newton 1.6 constructs an inactive MuJoCo-Warp mirror even in CPU mode.
    That mirror rejects MuJoCo 3.13's discrete integrator. Construct it using
    the existing default, then select discrete on the native model that
    actually steps. This does not enable discrete on GPU or upgrade MuJoCo.
    """
    if integrator not in {"euler", "rk4", "implicit", "implicitfast", "discrete"}:
        raise ValueError(f"unsupported CPU integrator: {integrator}")
    if str(model.device) != "cpu":
        raise ValueError("this solver adapter requires a CPU Newton model")
    import mujoco
    from newton.solvers import SolverMuJoCo

    discrete = getattr(mujoco.mjtIntegrator, "mjINT_DISCRETE", None)
    if integrator == "discrete":
        if discrete is None:
            raise RuntimeError("discrete requires MuJoCo >= 3.13 in the selected runtime environment")
        if kwargs.get("save_to_mjcf"):
            raise ValueError("Newton's constructor export would record the mirror integrator; export separately")
    solver = SolverMuJoCo(model, use_mujoco_cpu=True,
                          integrator="implicitfast" if integrator == "discrete" else integrator,
                          **kwargs)
    if integrator == "discrete":
        solver.mj_model.opt.integrator = discrete
    return solver


def default_physics_device(task: str, cfg: dict) -> str:
    """The existing CLI default; a GPU render device is not proof of GPU physics."""
    if task in {"simfoundry", "candidate"}:
        return "cpu"
    if task in {"grasp", "reference"}:
        evaluation = cfg.get("evaluation") or {}
        rss = evaluation.get("robosimstudio") or {}
        value = (rss.get("grasp") or {}).get("physics_device", "cpu")
        if value not in {"cpu", "gpu"}:
            raise ValueError("evaluation.robosimstudio.grasp.physics_device must be cpu or gpu")
        return value
    if task in {"rigid", "towel", "sharpa"}:
        return "gpu"
    raise ValueError(f"unknown RSS task: {task}")
