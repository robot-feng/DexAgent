"""Use LiteText text weights with SimFoundry's native SAM3 video pipeline."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def runtime(repo: Path) -> dict:
    """Inspect local files/modules only; never load a model in the HTTP service."""
    result = {"ready": False, "python": sys.executable}
    try:
        from agent.config import load_config

        cfg = load_config(repo / "dexagent.yaml")
        model = cfg.get("models") or {}
        environments = cfg.get("environments") or {}
        result["repo_root"] = str(repo.resolve())
        result["backend"] = model.get("segmentation_backend", "sam3-litetext-s0")
        result["model_dir"] = str(Path(model.get("sam3_litetext_dir") or "").resolve())
        result["source_root"] = str(Path(model.get("sam3_litetext_source") or
                                         repo / ".workbench/resources/efficientsam3-litetext/sam3").resolve())
        result["video_checkpoint"] = str(Path(model.get("sam3_video_checkpoint") or "").resolve())
        result["python"] = str(Path(environments.get("sam3_litetext_video") or "").resolve())
        missing_paths = []
        for label, path in (
            ("SAM3 LiteText 权重", Path(result["model_dir"]) / "model.safetensors"),
            ("EfficientSAM3 LiteText 视频源码", Path(result["source_root"]) / "sam3/model_builder.py"),
            ("SAM3 视频 checkpoint", Path(result["video_checkpoint"])),
            ("LiteText 视频 Python", Path(result["python"])),
        ):
            if not path.is_file():
                missing_paths.append(f"{label}不存在：{path}")
        if missing_paths:
            raise ValueError("；".join(missing_paths))
        probe = subprocess.run(
            [result["python"], "-c", (
                "import importlib.util,json; "
                "names=['torch','torchvision','timm','ftfy','safetensors','iopath','numpy','PIL','cv2','einops']; "
                "print(json.dumps({'missing':[n for n in names if importlib.util.find_spec(n) is None]}))"
            )],
            cwd=repo,
            env=dict(os.environ, PYTHONPATH=result["source_root"] + os.pathsep + str(repo)),
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        missing = json.loads(probe.stdout).get("missing", [])
        if missing:
            raise ValueError(f"LiteText 视频环境缺少模块：{'、'.join(missing)}")
        result.update(
            ready=True,
            detail="使用 SAM3 LiteText-S0 文本编码器接入原生 SAM3 视频跟踪；不需要 SAM2，缺少目标时会自动换帧播种。",
        )
    except (OSError, ImportError, TypeError, ValueError, AttributeError, subprocess.SubprocessError) as exc:
        result["detail"] = f"轻量分割运行时不可用：{exc}"
    return result


def run(stage: Path, config: dict):
    """Use LiteText text weights with the native SAM3 video tracker."""
    # The native source remains unchanged. Stage 1b and the Hydra stage resolve
    # SimFoundry's own config and data paths through this package root.
    sys.path.insert(0, str(stage.resolve().parents[5]))
    spec = importlib.util.spec_from_file_location("embodiforge_quadmask", stage)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    worker_meta = {}

    def lite_video_propagate(video_mp4, keyframe_idx, object_names, num_frames,
                             out_h, out_w, sam_conf=0.3):
        import numpy as np

        with tempfile.TemporaryDirectory(prefix="sam3_litetext_video_") as temp:
            output_dir = Path(temp)
            command = [
                config["python"], "-m", "workbench.sam3_litetext_video_worker",
                "--video", str(video_mp4),
                "--keyframe-idx", str(keyframe_idx),
                "--num-frames", str(num_frames),
                "--height", str(out_h),
                "--width", str(out_w),
                "--prompts", json.dumps(object_names, ensure_ascii=False),
                "--weights-dir", config["model_dir"],
                "--video-checkpoint", config["video_checkpoint"],
                "--source-root", config["source_root"],
                "--output-dir", str(output_dir),
            ]
            subprocess.run(
                command,
                cwd=Path(config.get("repo_root") or Path.cwd()),
                env=dict(
                    os.environ,
                    PYTHONPATH=os.pathsep.join((
                        str(config.get("repo_root") or Path.cwd()),
                        config["source_root"],
                        os.environ.get("PYTHONPATH", ""),
                    )),
                    HF_HUB_OFFLINE="1",
                ),
                check=True,
            )
            union = np.load(output_dir / "union.npy")
            canonical = np.load(output_dir / "canonical_masks.npy")
            worker_meta.update(json.loads((output_dir / "metadata.json").read_text()))
        if union.shape != (num_frames, out_h, out_w):
            raise ValueError(f"SAM3 LiteText tracker 输出尺寸错误：{union.shape}")
        if canonical.shape != (len(object_names), out_h, out_w):
            raise ValueError(f"SAM3 LiteText keyframe masks 尺寸错误：{canonical.shape}")
        return union, {
            name: canonical[index].astype("uint8")
            for index, name in enumerate(object_names)
        }

    module._sam3_video_propagate = lite_video_propagate
    try:
        module.main()
    finally:
        from tool_library.skills.detect_objects import _load_sam3_components

        _load_sam3_components.cache_clear()
    # Hydra has changed cwd; use the resolved config root to locate this scene.
    scene = next(
        (
            value.split("=", 1)[1]
            for value in sys.argv[1:]
            if value.startswith("scene_name=")
        ),
        None,
    )
    if not scene:
        raise ValueError("缺少 scene_name，不能绑定分割模型身份")
    metadata = (
        stage.resolve().parents[5]
        / "Data"
        / scene
        / "auto_bg/void/input/quadmask_meta.json"
    )
    data = json.loads(metadata.read_text())
    data.update(
        objects=worker_meta.get("objects", data.get("objects", [])),
        propagation_model="sam3-litetext-video",
        segmentation_identity=worker_meta.get("segmentation_identity", {}),
        propagation_identity={
            "model": "sam3-native-video-tracker",
            "checkpoint_sha256": worker_meta.get("video_checkpoint_sha256"),
            "source_revision": worker_meta.get("video_source_revision"),
            "temporal_propagation": True,
            "device": worker_meta.get("device"),
            "torch_version": worker_meta.get("torch_version"),
            "cuda_version": worker_meta.get("cuda_version"),
        },
        seed_frame_indices=worker_meta.get("seed_frame_indices", {}),
        seed_scores=worker_meta.get("seed_scores", {}),
    )
    metadata.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    args, overrides = parser.parse_known_args()
    sys.argv = [str(args.stage), *overrides]
    run(args.stage.resolve(), json.loads(args.runtime.read_text()))


if __name__ == "__main__":
    main()
