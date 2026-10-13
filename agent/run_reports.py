"""Shared, machine-readable run reports for the DexAgent pipeline."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


REPORT_SCHEMA = "dexagent.step_report.v1"


def config_identity(cfg: dict) -> dict:
    """Hash the resolved settings, including defaults and command-line overrides."""
    effective = json.loads(json.dumps({k: v for k, v in cfg.items() if not str(k).startswith("_")},
                                     default=str, allow_nan=False))
    payload = json.dumps(effective, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {"effective_config": effective,
            "effective_sha256": hashlib.sha256(payload.encode()).hexdigest()}


def sha256_file(path: str | os.PathLike, block_size: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(block_size), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path: str | os.PathLike, value: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=1, default=str, allow_nan=False)
            stream.write("\n")
        Path(name).replace(path)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise
    return path


def status_for_checks(checks: list[dict]) -> str:
    """A missing or incomplete required check fails; optional issues remain visible."""
    normalized = [c for c in checks if isinstance(c, dict)]
    if not normalized:
        return "failed"
    if any(c.get("required", True) and c.get("status") not in {"passed", "warning"} for c in normalized):
        return "failed"
    if any(c.get("status") == "failed" for c in normalized):
        return "warning"
    if any(c.get("status") != "passed" for c in normalized):
        return "warning"
    return "passed"


def verifier_checks(verdict: dict | None) -> list[dict]:
    """Convert existing Stage 1-3 verifier output into the common check shape."""
    checks = []
    reports = (verdict or {}).get("reports") or {}
    for name, report in reports.items():
        report = report if isinstance(report, dict) else {"ok": False, "issues": [str(report)]}
        skipped = report.get("skipped") is True or report.get("status") == "skipped"
        ok = report.get("ok") is True
        checks.append({
            "id": name,
            "required": report.get("required", True),
            "status": "skipped" if skipped else "passed" if ok else "failed",
            "observed": {k: v for k, v in report.items() if k not in {"ok", "issues", "required"}},
            "evidence": report.get("issues", []),
        })
    if not checks:
        checks.append({"id": "stage_verifiers", "required": True, "status": "failed",
                       "evidence": ["stage run did not include verifier reports"]})
    for field in ("warnings", "deferred"):
        values = (verdict or {}).get(field)
        if values:
            checks.append({"id": f"stage_{field}", "required": False, "status": "warning",
                           "observed": values, "evidence": "review these verifier findings before treating the stage as clean"})
    return checks


def make_report(*, run: str, profile: str, stage: str, step: str | None = None,
                status: str = "running", checks: list[dict] | None = None,
                inputs: dict | None = None, config_sha256: str | None = None,
                model_identity: dict | None = None, artifacts: list[str] | None = None,
                metrics: dict | None = None, elapsed_s: float | None = None,
                failure: str | None = None, next_action: str | None = None) -> dict:
    report = {
        "schema": REPORT_SCHEMA,
        "run": run,
        "profile": profile,
        "stage": stage,
        "step": step,
        "status": status,
        "checks": checks or [],
        "inputs": inputs or {},
        "config_sha256": config_sha256,
        "model_identity": model_identity or {},
        "artifacts": artifacts or [],
        "metrics": metrics or {},
        "elapsed_s": elapsed_s,
        "failure": failure,
        "next_action": next_action,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return report


def finish_report(report: dict, *, checks: list[dict] | None = None, artifacts=None,
                  metrics=None, elapsed_s=None, failure=None, next_action=None) -> dict:
    if checks is not None:
        report["checks"] = checks
        report["status"] = status_for_checks(checks)
    if artifacts is not None:
        report["artifacts"] = artifacts
    if metrics is not None:
        report["metrics"] = metrics
    if elapsed_s is not None:
        report["elapsed_s"] = round(float(elapsed_s), 3)
    if failure:
        report["failure"] = str(failure)
        report["status"] = "failed"
    if next_action:
        report["next_action"] = next_action
    report["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return report


def print_report(report: dict, path: str | os.PathLike, log=print) -> None:
    checks = report.get("checks") or []
    passed = sum(c.get("status") == "passed" for c in checks)
    label = f"{report.get('stage')}/{report.get('step')}" if report.get("step") else report.get("stage")
    log(f"[{label}] {report.get('status', 'unknown').upper()} — {passed}/{len(checks)} checks passed; report: {path}")
    for check in (c for c in checks if c.get("status") == "warning" or
                  (c.get("required", True) and c.get("status") in {"failed", "skipped"})):
        detail = check.get("evidence") or check.get("message") or check.get("observed") or "required check failed"
        level = "warning" if check.get("status") == "warning" else "failed"
        log(f"  - {level} {check.get('id')}: {str(detail)[:400]}")
    metrics = report.get("metrics") or {}
    if metrics.get("hf_home"):
        log(f"  environment HF_HOME: {metrics['hf_home']}")
    if metrics.get("nvidia_smi"):
        log(f"  GPU: {str(metrics['nvidia_smi'])[:240]}")
    for role, info in (metrics.get("interpreters") or {}).items():
        versions = info.get("versions") or {}
        compact = {k: versions.get(k) for k in ("torch", "mujoco", "transformers", "accelerate", "numpy")}
        log(f"  Python {role}: {info.get('python')} packages={compact} CUDA={info.get('cuda')}")
    for name, identity in (report.get("model_identity") or {}).items():
        if isinstance(identity, dict):
            digest = identity.get("checkpoint_sha256") or identity.get("sha256") or identity.get("void_checkpoint_sha256")
            log(f"  model {name}: path={identity.get('path') or identity.get('void_dir')} sha256={digest}")
    for key in ("video", "output_disk", "acceptance_rate", "accepted", "wanted", "attempts",
                "erase_coverage", "mask_area_fraction", "frame_counts", "render_elapsed_s", "elapsed_s"):
        if key not in metrics or metrics[key] is None:
            continue
        value = metrics[key]
        if key == "output_disk" and isinstance(value, dict) and value.get("free_bytes") is not None:
            value = {"path": value.get("path"), "free_gib": round(value["free_bytes"] / 1024**3, 2)}
        elif key == "video" and isinstance(value, dict):
            value = {k: value.get(k) for k in ("frames", "width", "height", "fps")}
        log(f"  metric {key}: {str(value)[:240]}")
