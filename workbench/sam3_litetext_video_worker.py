"""Run LiteText prompts through the native SAM3 bidirectional video tracker."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _convert_litetext_weights(weights_dir: Path, text_encoder) -> tuple[Path, str]:
    """Map the cached Transformers LiteText-S0 encoder to the native SAM3 API."""
    import torch
    from safetensors import safe_open

    source = weights_dir / "model.safetensors"
    source_hash = _sha256(source)
    cache = weights_dir / "native_mobileclip_s0_ctx77.pt"
    sidecar = weights_dir / "native_mobileclip_s0_ctx77.json"
    if cache.is_file() and sidecar.is_file():
        try:
            cached = json.loads(sidecar.read_text())
        except (OSError, json.JSONDecodeError):
            cached = {}
        if cached.get("source_sha256") == source_hash and cached.get("mapper") == 1:
            state = torch.load(cache, map_location="cpu", weights_only=True)
            text_encoder.load_state_dict(state, strict=True)
            return cache, source_hash

    converted = {}
    with safe_open(source, framework="pt", device="cpu") as safetensors:
        def get(name):
            return safetensors.get_tensor(name)

        def put(target, original):
            converted[target] = get(original)

        put("encoder.embedding_layer.weight", "text_encoder.embeddings.token_embedding.weight")
        put("encoder.positional_embedding.pos_embed.pos_embed",
            "text_encoder.embeddings.position_embedding.position_embedding")
        put("encoder.projection_layer", "text_encoder.projection.weight")
        put("encoder.final_layer_norm.weight", "text_encoder.final_layer_norm.weight")
        put("encoder.final_layer_norm.bias", "text_encoder.final_layer_norm.bias")
        put("projector.weight", "text_projection.weight")
        put("projector.bias", "text_projection.bias")

        for index in (0, 5):
            source_prefix = f"text_encoder.layers.{index}."
            target_prefix = f"encoder.transformer.{index}."
            for suffix in ("layer_scale", "token_mixer.layer_scale"):
                put(target_prefix + suffix, source_prefix + suffix)
            batch_norms = (
                ("token_mixer.reference_batchnorm.", "token_mixer.norm.rbr_skip."),
                ("token_mixer.mixer.batchnorm_skip.", "token_mixer.mixer.rbr_skip."),
                ("token_mixer.mixer.batchnorm_conv.", "token_mixer.mixer.rbr_conv.0.bn."),
                ("conv_feed_forward.depthwise_batchnorm.", "convffn.conv.bn."),
            )
            for source_name, target_name in batch_norms:
                for suffix in ("weight", "bias", "running_mean", "running_var", "num_batches_tracked"):
                    put(target_prefix + target_name + suffix, source_prefix + source_name + suffix)
            put(target_prefix + "token_mixer.mixer.rbr_conv.0.conv.weight",
                source_prefix + "token_mixer.mixer.conv.weight")
            put(target_prefix + "convffn.conv.conv.weight",
                source_prefix + "conv_feed_forward.depthwise_conv.weight")
            for layer in ("fc1", "fc2"):
                for suffix in ("weight", "bias"):
                    put(target_prefix + f"convffn.{layer}.{suffix}",
                        source_prefix + f"conv_feed_forward.mlp.{layer}.{suffix}")

        for index in range(1, 5):
            source_prefix = f"text_encoder.layers.{index}."
            target_prefix = f"encoder.transformer.{index}."
            for suffix in ("weight", "bias"):
                put(target_prefix + "pre_norm_mha.0." + suffix,
                    source_prefix + "layer_norm1." + suffix)
                put(target_prefix + "pre_norm_ffn.0." + suffix,
                    source_prefix + "layer_norm2." + suffix)
                put(target_prefix + "pre_norm_mha.1.out_proj." + suffix,
                    source_prefix + "self_attn.out_proj." + suffix)
                put(target_prefix + "pre_norm_ffn.1." + suffix,
                    source_prefix + "mlp.fc1." + suffix)
                put(target_prefix + "pre_norm_ffn.4." + suffix,
                    source_prefix + "mlp.fc2." + suffix)
                converted[target_prefix + "pre_norm_mha.1.qkv_proj." + suffix] = torch.cat(
                    [get(source_prefix + f"self_attn.{part}_proj.{suffix}") for part in ("q", "k", "v")],
                    dim=0,
                )

    text_encoder.load_state_dict(converted, strict=True)
    temporary = cache.with_suffix(".pt.tmp")
    torch.save(converted, temporary)
    temporary.replace(cache)
    sidecar.write_text(json.dumps({"mapper": 1, "source_sha256": source_hash}, indent=2))
    return cache, source_hash


def _model_revision(source_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root.parent), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def run(args) -> None:
    import numpy as np
    import torch
    from sam3.model.sam3_video_predictor import Sam3VideoPredictor
    from sam3.model_builder import _create_student_text_encoder

    video = Path(args.video).resolve()
    weights_dir = Path(args.weights_dir).resolve()
    source_root = Path(args.source_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    prompts = json.loads(args.prompts)
    if not isinstance(prompts, list) or not prompts or any(not isinstance(name, str) or not name.strip() for name in prompts):
        raise ValueError("SAM3 LiteText 视频跟踪需要至少一个非空对象提示词")

    device = "cpu"
    if torch.cuda.is_available():
        device_index = max(
            range(torch.cuda.device_count()),
            key=lambda index: torch.cuda.mem_get_info(index)[0],
        )
        torch.cuda.set_device(device_index)
        device = f"cuda:{device_index}"

    print("加载本机 SAM3 视频 checkpoint…", flush=True)
    predictor = Sam3VideoPredictor(checkpoint_path=args.video_checkpoint)
    try:
        bpe_path = source_root / "assets/bpe_simple_vocab_16e6.txt.gz"
        text_encoder = _create_student_text_encoder(
            str(bpe_path), "MobileCLIP-S0", context_length=77
        )
        native_cache, litetext_hash = _convert_litetext_weights(weights_dir, text_encoder)
        text_encoder.set_context_length(16)
        text_encoder = text_encoder.to(device).eval()
        predictor.model.detector.backbone.language_backbone = text_encoder
        predictor.model.eval()
        print(f"SAM3 LiteText-S0-16 已接入原生视频跟踪器，设备 {device}", flush=True)

        union = np.zeros((args.num_frames, args.height, args.width), dtype=np.uint8)
        canonical_masks = np.zeros((len(prompts), args.height, args.width), dtype=np.uint8)
        seed_indices = {}
        seed_scores = {}
        candidates = np.linspace(
            0,
            max(args.num_frames - 1, 0),
            min(args.num_frames, 16),
            dtype=int,
        ).tolist()
        candidates = sorted(
            set(candidates + [max(0, min(args.keyframe_idx, args.num_frames - 1))]),
            key=lambda index: (abs(index - args.keyframe_idx), index),
        )

        for prompt_index, name in enumerate(prompts):
            session_id = predictor.handle_request(
                {"type": "start_session", "resource_path": str(video)}
            )["session_id"]
            try:
                seed_index = None
                for candidate in candidates:
                    response = predictor.handle_request(
                        {
                            "type": "add_prompt",
                            "session_id": session_id,
                            "frame_index": candidate,
                            "text": name,
                        }
                    )
                    outputs = response["outputs"]
                    masks = np.asarray(outputs.get("out_binary_masks", []), dtype=bool)
                    if masks.ndim == 2:
                        masks = masks[None]
                    if masks.ndim == 3 and len(masks) and masks.any():
                        seed_index = candidate
                        seed_scores[name] = float(np.asarray(outputs.get("out_probs", [0.0])).max())
                        seed_mask = np.any(masks, axis=0)
                        break
                if seed_index is None:
                    raise ValueError(
                        f"SAM3 LiteText 在 {len(candidates)} 个候选帧都没有找到“{name}”；请调整对象名称。"
                    )

                seed_indices[name] = seed_index
                union[seed_index] = np.maximum(union[seed_index], seed_mask.astype(np.uint8) * 255)
                if seed_index == args.keyframe_idx:
                    canonical_masks[prompt_index] = seed_mask.astype(np.uint8)
                tracked_frames = set()
                for result in predictor.handle_stream_request(
                    {
                        "type": "propagate_in_video",
                        "session_id": session_id,
                        "propagation_direction": "both",
                        "start_frame_index": seed_index,
                    }
                ):
                    frame_index = int(result["frame_index"])
                    if not 0 <= frame_index < args.num_frames:
                        continue
                    tracked = np.asarray(
                        result["outputs"].get("out_binary_masks", []), dtype=bool
                    )
                    merged = np.any(tracked, axis=0) if tracked.ndim == 3 and len(tracked) else np.zeros(
                        (args.height, args.width), dtype=bool
                    )
                    if merged.shape != (args.height, args.width):
                        import cv2

                        merged = cv2.resize(
                            merged.astype(np.uint8),
                            (args.width, args.height),
                            interpolation=cv2.INTER_NEAREST,
                        ).astype(bool)
                    union[frame_index] = np.maximum(
                        union[frame_index], merged.astype(np.uint8) * 255
                    )
                    if frame_index == args.keyframe_idx:
                        canonical_masks[prompt_index] = merged.astype(np.uint8)
                    tracked_frames.add(frame_index)
                if not canonical_masks[prompt_index].any() and seed_index == args.keyframe_idx:
                    canonical_masks[prompt_index] = seed_mask.astype(np.uint8)
                print(
                    f"{name}: seed={seed_index}, score={seed_scores[name]:.3f}, "
                    f"tracked={len(tracked_frames)} frames",
                    flush=True,
                )
            finally:
                predictor.handle_request({"type": "close_session", "session_id": session_id})

        np.save(output_dir / "union.npy", union)
        np.save(output_dir / "canonical_masks.npy", canonical_masks)
        metadata = {
            "objects": prompts,
            "seed_frame_indices": seed_indices,
            "seed_scores": seed_scores,
            "segmentation_identity": {
                "model": "SAM3-LiteText-S0-16",
                "repo_id": "local:sam3-litetext-s0",
                "checkpoint_sha256": litetext_hash,
                "native_text_cache": str(native_cache),
            },
            "video_checkpoint_sha256": _sha256(Path(args.video_checkpoint)),
            "video_tracker": "SAM3 native bidirectional video tracker",
            "video_source_revision": _model_revision(source_root),
            "device": device,
            "torch_version": torch.__version__,
            "cuda_version": torch.version.cuda,
        }
        (output_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2)
        )
    finally:
        predictor.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True)
    parser.add_argument("--keyframe-idx", required=True, type=int)
    parser.add_argument("--num-frames", required=True, type=int)
    parser.add_argument("--height", required=True, type=int)
    parser.add_argument("--width", required=True, type=int)
    parser.add_argument("--prompts", required=True)
    parser.add_argument("--weights-dir", required=True)
    parser.add_argument("--video-checkpoint", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--output-dir", required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
