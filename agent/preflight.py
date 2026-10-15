"""Read-only environment and input checks run before a DexAgent pipeline starts."""
from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent.run_reports import config_identity, make_report, sha256_file, status_for_checks


def _check(name, ok, *, observed=None, evidence=None, required=True, status=None):
    return {"id": name, "required": required,
            "status": status or ("passed" if ok else "failed"),
            "observed": observed, "evidence": evidence}


def _under_data(path) -> bool:
    try:
        Path(path).resolve().relative_to(Path("/data"))
        return True
    except (ValueError, OSError):
        return False


def _probe_python(interpreter: str) -> dict:
    script = r'''import importlib.metadata as md, importlib.util, json, sys
names = ["torch", "mujoco", "transformers", "accelerate", "opencv-python", "numpy", "PyYAML",
         "bpy", "scipy", "trimesh", "diffusers", "safetensors"]
versions = {}
for name in names:
    try: versions[name] = md.version(name)
    except md.PackageNotFoundError: versions[name] = None
cuda = {"available": False, "devices": []}
try:
    import torch
    cuda["available"] = bool(torch.cuda.is_available())
    cuda["devices"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
except Exception as e: cuda["error"] = f"{type(e).__name__}: {e}"
print(json.dumps({"python": sys.executable, "versions": versions, "cuda": cuda}))'''
    result = subprocess.run([interpreter, "-c", script], capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[-2000:])
    return json.loads(result.stdout.strip().splitlines()[-1])


def _model_weight_digest(directory: str | None):
    if not directory or not Path(directory).is_dir():
        return None, []
    files = sorted(p for p in Path(directory).rglob("*") if p.is_file()
                   and p.suffix.lower() in {".safetensors", ".bin", ".pt", ".pth"})
    if not files:
        return None, []
    hashes = [{"file": str(p), "sha256": sha256_file(p)} for p in files]
    import hashlib
    aggregate = hashlib.sha256(json.dumps([(Path(x["file"]).name, x["sha256"]) for x in hashes],
                                          separators=(",", ":")).encode()).hexdigest()
    return aggregate, hashes


def preflight(cfg: dict, requested_stages: list[str], profile: str) -> dict:
    """Return a report; callers must stop before generation if its status is failed."""
    checks, metrics, inputs, models = [], {}, {}, {}
    effective_identity = config_identity(cfg)
    config_path = Path(cfg.get("_config_path") or "").resolve()
    config_ok = config_path.is_file()
    checks.append(_check("config_readable", config_ok, observed=str(config_path),
                         evidence=None if config_ok else "resolved configuration file is missing"))
    inputs["config"] = {"path": str(config_path),
                        "effective_config": effective_identity["effective_config"],
                        "effective_sha256": effective_identity["effective_sha256"]}
    if config_ok:
        inputs["config"]["sha256"] = sha256_file(config_path)

    output_dir = Path(cfg.get("run", {}).get("output_dir", "runs")).resolve()
    checks.append(_check("output_on_data_disk", _under_data(output_dir), observed=str(output_dir),
                         evidence="run output must resolve under /data"))
    hf_home = ((cfg.get("models") or {}).get("hf_home") or os.environ.get("HF_HOME"))
    checks.append(_check("model_cache_on_data_disk", bool(hf_home) and _under_data(hf_home),
                         observed=hf_home, evidence="configure models.hf_home under /data"))
    if hf_home:
        metrics["hf_home"] = str(Path(hf_home).resolve())
        metrics["hf_home_environment_override"] = os.environ.get("HF_HOME")

    agent_cfg = cfg.get("agent") or {}
    provider_ok = agent_cfg.get("provider") == "external"
    checks.append(_check("no_model_api", provider_ok,
                         observed=agent_cfg.get("provider"),
                         evidence="set agent.provider: external for Codex file transport"))
    transport = agent_cfg.get("transport_dir")
    checks.append(_check("external_transport_on_data_disk", bool(transport) and _under_data(transport),
                         observed=transport, evidence="set agent.transport_dir under /data"))

    video_needed = any(s in {"stage1", "stage2", "stage3", "stage4"} for s in requested_stages)
    video = Path(str((cfg.get("inputs") or {}).get("video") or "")).expanduser()
    video_ok = not video_needed
    if video_needed and video.is_file():
        try:
            import cv2
            cap = cv2.VideoCapture(str(video))
            try:
                opened = cap.isOpened()
                frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if opened else 0
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) if opened else 0
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) if opened else 0
                fps = float(cap.get(cv2.CAP_PROP_FPS)) if opened else 0.0
                decoded, first_frame = cap.read() if opened else (False, None)
                frame_shape = getattr(first_frame, "shape", ()) if first_frame is not None else ()
                first_frame_ok = (bool(decoded) and len(frame_shape) >= 2 and
                                  frame_shape[0] > 0 and frame_shape[1] > 0)
            finally:
                cap.release()
            video_ok = opened and first_frame_ok and frames > 0 and width > 0 and height > 0 and fps > 0
            video_meta = {"path": str(video.resolve()), "frames": frames, "width": width, "height": height,
                          "fps": fps, "first_frame_decoded": first_frame_ok, "sha256": sha256_file(video)}
            inputs["video"] = video_meta
            metrics["video"] = video_meta
        except Exception as exc:
            evidence = f"{type(exc).__name__}: {exc}"
        else:
            evidence = None if video_ok else "video decoder could not read frames or valid dimensions/fps"
    else:
        evidence = None if not video_needed else f"inputs.video does not exist: {video}"
    checks.append(_check("input_video_readable", video_ok,
                         observed=str(video) if video_needed else "not needed for selected stages", evidence=evidence))

    models_cfg = cfg.get("models") or {}
    segmentation_backend = models_cfg.get("segmentation_backend", "sam3-litetext-s0")
    inpaint_backend = str((cfg.get("stage4") or {}).get("inpaint_backend", "void")).lower()
    envs = cfg.get("environments") or {}
    pipeline_python = str(envs.get("pipeline") or sys.executable)
    stage4_python = str(envs.get("stage4") or pipeline_python)
    sam_python = str(envs.get("sam3") or pipeline_python)
    interpreter_results = {}
    probe_cache = {}
    needed_interpreters = {"pipeline": pipeline_python}
    if any(s in {"stage1", "stage2", "stage3"} for s in requested_stages):
        needed_interpreters["sam3"] = sam_python
    if "stage4" in requested_stages:
        needed_interpreters["stage4"] = stage4_python
    for name, interpreter in needed_interpreters.items():
        resolved_interpreter = Path(interpreter).resolve()
        exists = resolved_interpreter.is_file() and os.access(resolved_interpreter, os.X_OK)
        current_match = name != "pipeline" or resolved_interpreter == Path(sys.executable).resolve()
        if not exists:
            checks.append(_check(f"{name}_python", False, observed=interpreter,
                                 evidence="configured Python executable is missing or not executable"))
            continue
        try:
            cache_key = str(resolved_interpreter)
            if cache_key not in probe_cache:
                try:
                    probe_cache[cache_key] = ("result", _probe_python(str(resolved_interpreter)))
                except Exception as exc:
                    probe_cache[cache_key] = ("error", exc)
            probe_status, probe_value = probe_cache[cache_key]
            if probe_status == "error":
                raise probe_value
            info = probe_value
            interpreter_results[name] = info
            required_modules = {"pipeline": ("numpy", "PyYAML"), "sam3": ("torch", "transformers", "accelerate"),
                                "stage4": ("numpy", "mujoco", "opencv-python", "bpy", "scipy", "trimesh")}[name]
            if name == "stage4" and info["versions"].get("bpy") is None:
                blender = ((cfg.get("models") or {}).get("blender_executable")
                           or os.environ.get("DEXAGENT_BLENDER") or shutil.which("blender"))
                if blender and Path(blender).is_file() and os.access(blender, os.X_OK):
                    probe = subprocess.run([str(blender), "--background", "--version"],
                                           capture_output=True, text=True, timeout=30)
                    if probe.returncode == 0 and probe.stdout.startswith("Blender "):
                        required_modules = tuple(m for m in required_modules if m != "bpy")
                        metrics["blender"] = {"executable": str(blender), "version": probe.stdout.splitlines()[0]}
            if name == "stage4" and segmentation_backend == "sam3-litetext-s0":
                required_modules = (*required_modules, "torch", "transformers", "accelerate")
            if name == "stage4" and inpaint_backend == "lama":
                required_modules = (*required_modules, "torch")
            if name == "stage4" and inpaint_backend == "void":
                required_modules = (*required_modules, "torch", "transformers", "accelerate", "diffusers", "safetensors")
            if name == "pipeline" and "stage3" in requested_stages:
                required_modules = (*required_modules, "mujoco")
            missing = [module for module in required_modules if info["versions"].get(module) is None]
            ok = not missing and current_match
            evidence = []
            if missing:
                evidence.append("missing packages: " + ", ".join(missing))
            if not current_match:
                evidence.append(f"run with configured pipeline interpreter: {interpreter}")
            checks.append(_check(f"{name}_environment", ok, observed=info,
                                 evidence="; ".join(evidence) if evidence else None))
        except Exception as exc:
            checks.append(_check(f"{name}_environment", False, observed=interpreter,
                                 evidence=f"{type(exc).__name__}: {exc}"))
    metrics["interpreters"] = interpreter_results

    gpu = shutil.which("nvidia-smi")
    if gpu:
        result = subprocess.run([gpu, "--query-gpu=index,name,memory.free,memory.total", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=10)
        metrics["nvidia_smi"] = result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
        checks.append(_check("gpu_inventory", result.returncode == 0, required=False,
                             status="passed" if result.returncode == 0 else "warning", observed=metrics["nvidia_smi"]))
    else:
        checks.append(_check("gpu_inventory", True, required=False, status="warning",
                             observed="nvidia-smi unavailable; CPU execution may still be valid"))

    models["segmentation"] = {"backend": segmentation_backend}
    sam_path = models_cfg.get("sam3_litetext_dir")
    needs_segmentation = any(s in {"stage1", "stage2", "stage3", "stage4"} for s in requested_stages)
    if needs_segmentation and segmentation_backend == "sam3-litetext-s0":
        exists = bool(sam_path) and Path(sam_path).is_dir()
        digest, files = _model_weight_digest(sam_path) if exists else (None, [])
        models["segmentation"].update({"path": str(sam_path), "checkpoint_sha256": digest, "files": files})
        checks.append(_check("segmentation_checkpoint", exists and bool(files),
                             observed=models["segmentation"],
                             evidence=None if exists and files else "local SAM3-LiteText checkpoint or weight files are missing"))
        checks.append(_check("segmentation_checkpoint_on_data_disk", bool(sam_path) and _under_data(sam_path), observed=sam_path,
                             evidence="model weights must be stored under /data"))
    elif needs_segmentation:
        known = isinstance(segmentation_backend, str) and bool(segmentation_backend.strip())
        if known:
            try:
                known = any(ep.name == segmentation_backend
                            for ep in importlib.metadata.entry_points(group="dexagent.segmentation_backends"))
            except Exception:
                known = False
        checks.append(_check("segmentation_backend_registered", known, observed=segmentation_backend,
                             evidence="install a package entry point in dexagent.segmentation_backends"))

    if "stage4" in requested_stages:
        for command in ("ffmpeg", "ffprobe"):
            executable = shutil.which(command)
            checks.append(_check(f"{command}_available", bool(executable), observed=executable,
                                 evidence=f"{command} is required for Stage 4 video compositing and inspection"))
        if inpaint_backend == "lama":
            checkpoint = models_cfg.get("lama_checkpoint")
            exists = bool(checkpoint) and Path(checkpoint).is_file()
            identity = {"backend": "lama", "path": checkpoint}
            if exists:
                identity.update({"size_bytes": Path(checkpoint).stat().st_size,
                                 "sha256": sha256_file(checkpoint)})
            models["inpainting"] = identity
            checks.append(_check("inpainting_checkpoint", exists, observed=identity,
                                 evidence=None if exists else "configure an existing models.lama_checkpoint"))
            checks.append(_check("inpainting_checkpoint_on_data_disk", exists and _under_data(checkpoint),
                                 observed=checkpoint, evidence="inpainting weights must be stored under /data"))
        elif inpaint_backend == "void":
            void_dir = models_cfg.get("void_dir") or (Path(__file__).resolve().parents[1] /
                                                        "stage4_robot_data_generation" / "third_party" / "void-model")
            code_ok = (Path(void_dir) / "inference/cogvideox_fun/predict_v2v.py").is_file()
            cache = Path(hf_home or "") / "hub"
            void_weights = sorted(cache.glob("models--netflix--void-model/**/void_pass1.safetensors")) if cache.is_dir() else []
            cogvideo_dirs = sorted(cache.glob("models--alibaba-pai--CogVideoX-Fun-V1.5-5b-InP/snapshots/*")) if cache.is_dir() else []
            cogvideo_ok = any(any(p.suffix.lower() in {".safetensors", ".bin"} for p in d.rglob("*"))
                              for d in cogvideo_dirs if d.is_dir())
            models["inpainting"] = {"backend": "void", "void_dir": str(void_dir),
                                    "void_checkpoint": str(void_weights[0]) if void_weights else None,
                                    "cogvideox_snapshot": str(cogvideo_dirs[0]) if cogvideo_ok else None}
            checks.append(_check("void_runtime_and_weights", code_ok and bool(void_weights) and cogvideo_ok,
                                 observed=models["inpainting"],
                                 evidence="VOID code, void_pass1.safetensors, or CogVideoX weights are missing from configured paths/cache"))
            if void_weights:
                models["inpainting"]["void_checkpoint_sha256"] = sha256_file(void_weights[0])
        else:
            models["inpainting"] = {"backend": inpaint_backend}
            checks.append(_check("inpainting_backend_supported", False, observed=inpaint_backend,
                                 evidence="supported Stage 4 inpainting backends are lama and void"))

    usage_path = output_dir
    while not usage_path.exists() and usage_path != usage_path.parent:
        usage_path = usage_path.parent
    disk = shutil.disk_usage(usage_path)
    metrics["output_disk"] = {"path": str(usage_path), "free_bytes": disk.free, "total_bytes": disk.total}
    checks.append(_check("output_disk_space_measured", True, observed=metrics["output_disk"], required=False))

    report = make_report(run=str(cfg.get("run", {}).get("name", "run")), profile=profile,
                         stage="preflight", status="running", checks=checks,
                         inputs=inputs, config_sha256=effective_identity["effective_sha256"],
                         model_identity=models, metrics=metrics)
    report["status"] = status_for_checks(checks)
    report["artifacts"] = [str(config_path)] + ([str(video.resolve())] if video_needed and video.is_file() else [])
    return report
