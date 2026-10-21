"""Inspect the selected execution environment without importing a physics engine."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import yaml

from packaging.requirements import Requirement
from packaging.version import InvalidVersion

from agent.config import load_config
from agent.rss_runtime import default_physics_device, rss_python
from .models import RuntimePackage, SimulationProfile, SimulationRuntime


# Run in the exact interpreter used by Executions/run_robosimstudio, not in the
# API's environment. Only metadata is read: no CUDA allocation or model imports.
PROBE = """
import importlib.metadata as m
import json, sys
packages = []
for name in ('newton', 'warp-lang', 'mujoco', 'mujoco-warp', 'robosimstudio', 'numpy', 'scipy', 'viser'):
    try:
        version = m.version(name)
    except m.PackageNotFoundError:
        version = None
    packages.append(dict(name=name, version=version, available=version is not None))
try:
    requirements = m.requires('robosimstudio') or []
except m.PackageNotFoundError:
    requirements = []
print(json.dumps(dict(python=sys.executable, packages=packages, requirements=requirements)))
"""


def profiles(cfg: dict) -> list[SimulationProfile]:
    result = []
    for mode in ("rigid", "towel", "sharpa", "grasp", "reference", "simfoundry", "candidate"):
        device = default_physics_device(mode, cfg)
        result.append(SimulationProfile(
            mode=mode,
            solver="SolverMuJoCo + SolverVBD" if mode == "towel" else "SolverMuJoCo",
            physics_device=device,
            detail=("Newton 耦合刚体与布料求解；是否完成任务以原生报告为准。"
                    if mode == "towel" else
                    "Newton 使用 CPU MuJoCo 接触物理；状态与渲染仍可能在 GPU。"
                    if device == "cpu" else
                    "Newton 使用 MuJoCo Warp GPU 物理；启动与任务验收由原生运行报告确认。"),
        ))
    return result


def interpret_probe(data: dict) -> tuple[str, str, list[RuntimePackage]]:
    packages = [RuntimePackage(**item) for item in data["packages"]]
    expected = {"newton", "warp-lang", "mujoco", "mujoco-warp", "robosimstudio", "numpy", "scipy", "viser"}
    if len(packages) != len(expected) or {item.name for item in packages} != expected:
        raise ValueError("incomplete package metadata")
    if any(item.available != (item.version is not None) for item in packages):
        raise ValueError("inconsistent package metadata")
    missing = [item.name for item in packages if not item.available]
    if missing:
        return "missing_dependencies", "运行环境缺少：" + "、".join(missing), packages
    versions = {item.name: item.version for item in packages}
    requirements = [Requirement(value) for value in data["requirements"]]
    newton = [req for req in requirements if req.name.lower() == "newton"]
    if not newton:
        return "error", "已检测包版本，但 RoboSimStudio 未声明可核对的 Newton 兼容约束。", packages
    for requirement in newton:
        if not requirement.specifier.contains(versions["newton"], prereleases=True):
            return ("incompatible",
                    f"Newton {versions['newton']} 不满足当前 RoboSimStudio 的 {requirement}；请保留独立环境。",
                    packages)
    return ("environment_detected",
            "已核对所选解释器的核心包版本与 RSS 的 Newton 约束；尚未在本次检查中运行物理或验证 GPU。",
            packages)


def inspect_runtime(repo: Path, configs: dict[str, Path], config_id: str | None = None) -> SimulationRuntime:
    checked_at = datetime.now(timezone.utc).isoformat()
    if config_id is not None and config_id not in configs:
        raise ValueError("配置已移除，请刷新后重新选择")
    if config_id is None:
        config_id = min(configs, key=lambda key: configs[key].name) if configs else None
    if config_id is None:
        return SimulationRuntime(status="error", checked_at=checked_at, detail="没有可读取的任务配置。")
    path = configs[config_id]
    base = dict(config_id=config_id, config_name=path.name, checked_at=checked_at)
    try:
        cfg = load_config(path)
        python = rss_python(cfg)
        base.update(python=python, profiles=profiles(cfg))
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
        return SimulationRuntime(**base, status="error", detail="无法读取有效的运行配置。")
    try:
        output = subprocess.run(
            [python, "-c", PROBE], cwd=repo, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=10, check=False,
        )
    except subprocess.TimeoutExpired:
        return SimulationRuntime(**base, status="error", detail="运行环境检查超过 10 秒，请检查解释器。")
    except OSError:
        return SimulationRuntime(**base, status="error", detail="无法启动配置中的运行解释器。")
    if output.returncode:
        # Arbitrary interpreter startup output can contain environment values.
        # Keep it out of the response; this probe has no reason to show them.
        return SimulationRuntime(**base, status="error", detail="运行解释器未能完成包版本检查。")
    try:
        data = json.loads(output.stdout)
        status, detail, packages = interpret_probe(data)
    except (ValueError, TypeError, KeyError, InvalidVersion):
        return SimulationRuntime(**base, status="error", detail="运行环境返回了无效的版本信息。")
    return SimulationRuntime(**base, status=status, detail=detail, packages=packages)
