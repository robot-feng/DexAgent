"""Text-prompted erase masks with SAM3-LiteText (run as a subprocess by plate.py).

    python -m stage4.masks <frames_dir> <out_dir> "hand,arm,cup" [min_score]

For every frame, each prompt is segmented and all instances above the score
floor are merged into one binary mask: 255 = erase this pixel.
"""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np


def main():
    frames_dir, out_dir, prompts = sys.argv[1], sys.argv[2], sys.argv[3]
    min_score = float(sys.argv[4]) if len(sys.argv) > 4 else 0.3
    backend_name = sys.argv[5] if len(sys.argv) > 5 else os.environ.get("DEXAGENT_SEGMENTATION_BACKEND", "sam3-litetext-s0")
    prompts = [p.strip() for p in prompts.split(",") if p.strip()]
    files = sorted(glob.glob(os.path.join(frames_dir, "frame*.png")))
    assert files and prompts, f"nothing to segment ({len(files)} frames, {len(prompts)} prompts)"
    os.makedirs(out_dir, exist_ok=True)

    from PIL import Image
    repo = Path(__file__).resolve().parents[2]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from tool_library.common.segmentation_backend import load_backend

    processor = load_backend(backend_name, min_score=min_score)
    print(f"[masks] backend {backend_name}; prompts {prompts} on {len(files)} frames", flush=True)
    cover = []
    for t, path in enumerate(files):
        img = Image.open(path).convert("RGB")
        union = np.zeros((img.height, img.width), bool)
        for prompt in prompts:
            result = processor.segment(img, prompt)
            masks = result.get("masks")
            if masks is None or len(masks) == 0:
                continue
            if hasattr(masks, "detach"):
                masks = masks.detach().cpu().numpy()
            masks = np.asarray(masks).astype(bool).reshape(len(masks), -1, img.height, img.width).any(axis=1).any(axis=0)
            if masks.shape != union.shape:
                masks = cv2.resize(masks.astype(np.uint8), union.shape[::-1],
                                   interpolation=cv2.INTER_NEAREST) > 0
            union |= masks
        cv2.imwrite(os.path.join(out_dir, os.path.basename(path)), union.astype(np.uint8) * 255)
        cover.append(float(union.mean()))
        if t % 20 == 0 or t == len(files) - 1:
            print(f"[masks] {t + 1}/{len(files)}", flush=True)
    identity = getattr(processor, "model_identity", {})
    metadata = {"schema": "dexagent.segmentation.v1", "backend": backend_name,
                "prompts": prompts, "n_frames": len(files), "min_score": min_score,
                "mean_coverage": float(np.mean(cover)), "coverage_per_frame": cover,
                "model_identity": identity}
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "meta.json").write_text(json.dumps(metadata, indent=1, allow_nan=False, default=str))
    print(f"[masks] mean erase coverage {np.mean(cover):.1%} -> {out_dir}", flush=True)


if __name__ == "__main__":
    main()
