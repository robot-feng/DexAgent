"""Opt-in native CPU solver usable by RSS's existing backend declaration."""
import mujoco
from newton.solvers import SolverMuJoCo


class CpuMuJoCoSolver(SolverMuJoCo):
    """Keep Newton's attribute registration and solver API for RSS composition.

    Newton 1.6 constructs an inactive Warp mirror even in CPU mode. That
    mirror rejects discrete, so select it on the native model after building
    the mirror with implicitfast. GPU discrete is not enabled by this class.
    """

    def __init__(self, model, *, integrator="implicitfast", **kwargs):
        if integrator not in {"euler", "rk4", "implicit", "implicitfast", "discrete"}:
            raise ValueError(f"unsupported CPU integrator: {integrator}")
        if str(model.device) != "cpu":
            raise ValueError("this solver adapter requires a CPU Newton model")
        discrete = getattr(mujoco.mjtIntegrator, "mjINT_DISCRETE", None)
        if integrator == "discrete":
            if discrete is None:
                raise RuntimeError("discrete requires MuJoCo >= 3.13 in the selected runtime environment")
            if kwargs.get("save_to_mjcf"):
                raise ValueError("Newton's constructor export would record the mirror integrator; export separately")
        super().__init__(model, use_mujoco_cpu=True,
                         integrator="implicitfast" if integrator == "discrete" else integrator,
                         **kwargs)
        if integrator == "discrete":
            self.mj_model.opt.integrator = discrete
