"""Open-vocabulary object detection on sampled frames with SAM3-LiteText."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from contextlib import nullcontext
from functools import lru_cache
from pathlib import Path

import numpy as np

from tool_library.common.video import frame_files, write_json

SAM3_LITETEXT_REPO = "yonigozlan/sam3-litetext-s0"
SAM3_LITETEXT_VERSION = "sam3-litetext-s0"

TOOL = {
    "name": "detect_objects",
    "title": "Detect objects",
    "type": "skill",
    "category": "Perception & Grounding",
    "summary": "SAM3-LiteText text-prompted detection on a few frames: the best box per keyword and frame, plus every instance found.",
    "description": (
        "Prompts SAM3-LiteText with each keyword on a sparse sample of frames and records, per frame "
        "and keyword, the highest-scoring box and the full list of instances with scores. Two "
        "uses: it validates that the names chosen for the task objects are findable at all, and "
        "it yields the seed boxes that tracking starts from. The number of confident instances "
        "per frame is a count instrument (four 'drawer' boxes is a four-drawer cabinet). Frames "
        "should be spread over the clip, because a part that opens is only visible while it is "
        "open. Without frame_indices it scans a fifth of the frames in frames_dir and never fewer "
        "than 8 (all of them when the folder holds 8 or fewer), evenly spaced from the first frame "
        "to the last. The scan "
        "records which frames it read (frame_indices, frame_selection)."
    ),
    "inputs": [
        {"name": "frames_dir", "type": "path", "description": "Folder of frame%05d.jpg."},
        {"name": "keywords", "type": "list[str]", "description": "Object names to detect, e.g. ['cup', 'bowl']."},
        {"name": "out_json", "type": "path", "description": "Where the scan is written."},
        {"name": "frame_indices", "type": "list[int] | None", "description": "Positions (0-based, in sorted file order) of the frames in frames_dir to scan. Default None: 20% of the frames but at least 8 (or all when there are fewer), evenly spaced including the first and the last."},
        {"name": "min_score", "type": "float", "description": "Detection confidence floor. Default 0.1."},
        {"name": "version", "type": "str", "description": "Registered text-segmentation backend (default: sam3-litetext-s0)."},
    ],
    "outputs": [
        {"name": "scan", "type": "dict",
         "description": "prompts, frame_indices (the frames scanned), frame_selection ('given' or the default rule), n_frames_in_dir, detections[frame][keyword] = {box [x0,y0,x1,y1], score, n_boxes, all_boxes, all_scores}; summary with frames_with_hit and best_score_per_keyword; model_identity records the actual checkpoint SHA256 and runtime."},
    ],
    "status": "tested",
    "entrypoint": "detect_objects",
    "requirements": ["transformers>=5.6,<5.14", "accelerate>=1.1", "torch", "Pillow"],
    "tags": ["SAM3-LiteText", "detection", "open vocabulary"],
    "used_in": ["Place Cup in Bowl", "Multi-Cup Grasping", "Place Toy in Drawer", "Giftbox Packing", "Knot the Rope", "Battery Insertion", "Drawing and Wiping", "Computer Installation", "Open the Bottle", "Scissor Cutting", "Biology Experiment"],
    "cli": "python -m tool_library.skills.detect_objects frames/ scan.json --keywords cup bowl",
}

class _InferenceProcessor:
    """Keep inference/autocast scoped to each call, including on exceptions."""
    def __init__(self, processor, torch, device, identity):
        self.processor, self.torch, self.device = processor, torch, device
        self.model_identity = identity

    def _call(self, name, *args, **kwargs):
        torch = self.torch
        precision = (torch.autocast("cuda", dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16)
                     if self.device == "cuda" else nullcontext())
        with torch.inference_mode(), precision:
            return getattr(self.processor, name)(*args, **kwargs)

    def set_image(self, *args, **kwargs):
        return self._call("set_image", *args, **kwargs)

    def set_text_prompt(self, *args, **kwargs):
        return self._call("set_text_prompt", *args, **kwargs)

    def segment(self, image, prompt):
        """Common text-segmentation API used by both perception skills and Stage 4."""
        state = self.set_image(image)
        return self.set_text_prompt(prompt=prompt, state=state)


class _Sam3LiteTextProcessor:
    """Adapt Transformers' batch API to DexAgent's set_image/set_text_prompt API."""
    def __init__(self, model, processor, torch, min_score, identity):
        self.model, self.processor, self.torch = model, processor, torch
        self.min_score = float(min_score)
        self.model_identity = identity

    @staticmethod
    def _identity(model_dir, torch, device):
        weights = sorted([*model_dir.glob("*.safetensors"), *model_dir.glob("*.bin")])
        if not weights:
            raise ValueError(f"SAM3-LiteText weights are missing from {model_dir}")
        manifest = []
        for path in weights:
            h = hashlib.sha256()
            with path.open("rb") as f:
                for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
                    h.update(block)
            manifest.append((path.name, h.hexdigest()))
        digest = hashlib.sha256(json.dumps(manifest, separators=(",", ":")).encode()).hexdigest()
        return {"version": SAM3_LITETEXT_VERSION, "repo_id": SAM3_LITETEXT_REPO,
                "checkpoint_sha256": digest, "checkpoint_files": manifest,
                "transformers_version": importlib.metadata.version("transformers"),
                "torch_version": torch.__version__, "device": str(device)}

    def set_image(self, image):
        return image

    def set_text_prompt(self, prompt, state):
        inputs = self.processor(images=state, text=prompt, return_tensors="pt").to(self.model.device)
        outputs = self.model(**inputs)
        results = self.processor.post_process_instance_segmentation(
            outputs, threshold=self.min_score, mask_threshold=0.5,
            target_sizes=inputs.get("original_sizes").tolist())[0]
        return {key: self.torch.as_tensor(value) for key, value in results.items()
                if key in {"masks", "boxes", "scores"}}


@lru_cache(maxsize=1)
def _load_sam3_components(model_dir: str, auto_model, auto_processor):
    """Keep one loaded 2 GB checkpoint per worker instead of reloading it per tool call."""
    import torch

    if torch.cuda.is_available():
        gpu = max(range(torch.cuda.device_count()), key=lambda i: torch.cuda.mem_get_info(i)[0])
        device = f"cuda:{gpu}"
    else:
        device = "cpu"
    if device.startswith("cuda:"):
        model = auto_model.from_pretrained(model_dir, device_map={"": device})
    else:
        model = auto_model.from_pretrained(model_dir).to(device)
    model.eval()
    processor = auto_processor.from_pretrained(model_dir)
    identity = _Sam3LiteTextProcessor._identity(Path(model_dir), torch, model.device)
    return model, processor, torch, identity, device


def load_sam3_litetext(min_score=0.1, version=SAM3_LITETEXT_VERSION):
    """Load the requested SAM3-LiteText checkpoint and record its exact weights."""
    if version not in {SAM3_LITETEXT_VERSION, "sam3"}:  # sam3 is a legacy API alias
        raise ValueError(f"SAM image detection/segmentation requires version={SAM3_LITETEXT_VERSION!r}")
    try:
        import torch
        from huggingface_hub import snapshot_download
        import transformers
    except ImportError as e:
        raise ImportError("SAM3-LiteText dependencies are missing in this interpreter; install transformers>=5.6,<5.14, "
                          "accelerate and torch in the configured SAM environment") from e
    source = os.environ.get("DEXAGENT_SAM3_LITETEXT_PATH")
    if source and Path(source).is_dir():
        model_dir = Path(source).resolve()
    else:
        model_dir = Path(snapshot_download(SAM3_LITETEXT_REPO, cache_dir=os.environ.get("HF_HOME"))).resolve()
    try:
        model, processor, torch, identity, device = _load_sam3_components(
            str(model_dir), transformers.AutoModel, transformers.AutoProcessor)
    except ImportError as e:
        raise ImportError("SAM3-LiteText requires transformers>=5.6,<5.14, accelerate and torch in the configured SAM environment") from e
    adapter = _Sam3LiteTextProcessor(model, processor, torch, min_score, identity)
    return _InferenceProcessor(adapter, torch, "cuda" if device.startswith("cuda") else "cpu", adapter.model_identity)


# Keep the old import name for downstream tools that call this loader directly.
load_sam3 = load_sam3_litetext


MIN_FRAMES = 8       # a part that is visible only while open needs more than one look, whatever the folder's size


def sample_indices(n_total, frac=0.2, min_frames=MIN_FRAMES):
    """frac of the frames, at least min_frames (all of them when there are fewer), evenly spaced from first to last."""
    n_total = int(n_total)
    if n_total <= 0:
        return []
    n = min(n_total, max(int(min_frames), int(round(frac * n_total))))
    return sorted({int(round(v)) for v in np.linspace(0, n_total - 1, n)})


def detect_objects(frames_dir, keywords, out_json, frame_indices=None, min_score=0.1,
                   version=SAM3_LITETEXT_VERSION, processor=None):
    from PIL import Image
    files = frame_files(frames_dir)
    assert files, f"no frames in {frames_dir}"
    keywords = [k.strip() for k in keywords if k.strip()]
    if frame_indices:
        idx = sorted({int(i) for i in frame_indices if 0 <= int(i) < len(files)})
        if not idx:
            raise ValueError(f"frame_indices {list(frame_indices)[:8]} are all outside the {len(files)} frames of {frames_dir}")
        selection = "given"
    else:
        idx = sample_indices(len(files))
        selection = f"default: {len(idx)} of {len(files)} frames evenly spaced (20%, at least {MIN_FRAMES})"
    if processor is None:
        from tool_library.skills.segmentation_backend import load_backend
        proc = load_backend(version, min_score=min_score)
    else:
        proc = processor
    dets = {}
    for fi in idx:
        image = Image.open(files[fi]).convert("RGB")
        state = proc.set_image(image) if not hasattr(proc, "segment") else None
        rec = {}
        for p in keywords:
            o = proc.segment(image, p) if hasattr(proc, "segment") else proc.set_text_prompt(prompt=p, state=state)
            def array(value):
                if value is None:
                    return None
                if hasattr(value, "detach"):
                    value = value.detach().float().cpu().numpy()
                return np.asarray(value)
            scores = array(o.get("scores"))
            boxes = array(o.get("boxes"))
            masks = array(o.get("masks"))
            if scores is None:
                scores = np.ones((len(boxes) if boxes is not None else len(masks) if masks is not None else 0,), float)
            scores = np.asarray(scores, float).reshape(-1)
            if scores.size == 0:
                continue
            if boxes is None and masks is not None:
                import cv2
                masks = masks.astype(bool)
                if masks.ndim == 2:
                    masks = masks[None]
                masks = masks.reshape(len(masks), -1, image.height, image.width).any(axis=1)
                derived = []
                for mask in masks:
                    ys, xs = np.nonzero(mask)
                    derived.append([xs.min(), ys.min(), xs.max() + 1, ys.max() + 1] if len(xs) else [0, 0, 0, 0])
                boxes = np.asarray(derived, float)
            if boxes is None:
                raise ValueError(f"segmentation backend {version!r} returned scores without boxes or masks")
            boxes = np.asarray(boxes, float).reshape(-1, 4)
            if len(boxes) != len(scores):
                raise ValueError(f"segmentation backend {version!r} returned {len(boxes)} boxes and {len(scores)} scores")
            m = int(np.argmax(scores))
            rec[p] = {"box": [float(v) for v in boxes[m]], "score": round(float(scores[m]), 4),
                      "n_boxes": int(scores.size),
                      "all_boxes": [[round(float(v), 1) for v in b] for b in boxes.tolist()],
                      "all_scores": [round(float(s), 3) for s in scores.tolist()]}
        dets[int(fi)] = rec
    hits = {p: sum(1 for r in dets.values() if p in r) for p in keywords}
    best = {p: max([r[p]["score"] for r in dets.values() if p in r] or [0.0]) for p in keywords}
    scan = {"prompts": keywords, "frame_indices": idx, "frame_selection": selection, "n_frames_in_dir": len(files), "min_score": min_score,
            "frames_dir": str(Path(frames_dir).resolve()), "detections": dets,
            "model_identity": getattr(proc, "model_identity", None),
            "summary": {"frames_with_hit": hits, "best_score_per_keyword": best,
                        "not_found": [p for p in keywords if hits[p] == 0]}}
    write_json(scan, out_json)
    return scan


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=TOOL["summary"])
    ap.add_argument("frames_dir")
    ap.add_argument("out_json")
    ap.add_argument("--keywords", nargs="+", required=True)
    ap.add_argument("--frames", nargs="*", type=int)
    ap.add_argument("--min-score", type=float, default=0.1)
    ap.add_argument("--version", default=SAM3_LITETEXT_VERSION)
    a = ap.parse_args(argv)
    s = detect_objects(a.frames_dir, a.keywords, a.out_json, a.frames or None, a.min_score, a.version)
    print(json.dumps(s["summary"], indent=1))


if __name__ == "__main__":
    sys.exit(main())
