"""Lightweight RSS execution defaults shared by the CLI and Studio inspection."""

from __future__ import annotations


def build_cpu_mujoco_solver(model, *, integrator: str = "implicitfast", **kwargs):
    """Lazy entry point; the same solver class is available to RSS backends."""
    from agent.rss_mujoco_cpu import CpuMuJoCoSolver

    return CpuMuJoCoSolver(model, integrator=integrator, **kwargs)


def cpu_mujoco_backend(*, integrator: str = "implicitfast", **kwargs):
    """Declare the CPU adapter through RSS, including native contact routing.

    A bare dotted path has unknown contact capabilities in RSS. Register its
    metadata in the existing registry; leave the standard MuJoCo alias alone.
    """
    from robosimstudio.specs.physics import backend, get_backend, register_backend

    path = "agent.rss_mujoco_cpu.CpuMuJoCoSolver"
    native = get_backend("mujoco")
    register_backend(path, cls=path, supports=native.supports, native_contacts=True,
                     summary="EmbodiForge native CPU MuJoCo adapter")
    return backend(path, integrator=integrator, **kwargs)


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
