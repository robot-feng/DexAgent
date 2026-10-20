"""Lightweight RSS execution defaults shared by the CLI and Studio inspection."""

from __future__ import annotations


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
