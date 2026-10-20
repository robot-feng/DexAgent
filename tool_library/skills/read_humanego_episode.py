"""Import processed HumanEgo observations without inventing scene registration."""
from __future__ import annotations

TOOL = {
    "name": "read_humanego_episode",
    "title": "Read HumanEgo demonstration",
    "type": "skill",
    "category": "Perception & Grounding",
    "summary": "Read processed HumanEgo RGB/camera/hands with measured timestamps; optionally bind an explicit rig registration.",
    "description": "Preserves Aria MPS21 skeleton identity, original source frame IDs, nanosecond timestamps and source records. Missing hands remain invalid. A source-world demonstration is not a robot Episode. camera.json is emitted only with an explicit world-from-rig registration; no table plane or scene alignment is inferred.",
    "inputs": [
        {"name": "session", "type": "path", "description": "Local session containing preprocess/all_data and aria_cam_rgb_config.json."},
        {"name": "out_dir", "type": "path", "description": "New output directory; existing outputs are never overwritten."},
        {"name": "frames", "type": "list[int] | None", "description": "Explicit source frame subset; None requires the complete declared camera sequence."},
        {"name": "registration", "type": "path | None", "description": "JSON R_world_rig, origin_world and nonempty source description; no implicit identity registration."},
    ],
    "outputs": [
        {"name": "frames", "type": "list[path]", "description": "Raw rectified RGB images, preserving source frame numbers."},
        {"name": "episode", "type": "dict", "description": "Demo provenance, time convention, missingness, subset and registration status."},
        {"name": "camera", "type": "dict | None", "description": "Existing rigcam contract only when registration was supplied."},
    ],
    "status": "tested",
    "entrypoint": "read_humanego_episode",
    "requirements": ["numpy", "opencv-python", "scipy"],
    "tags": ["humanego", "camera", "demonstration", "hands"],
    "used_in": [],
    "cli": "python -m tool_library.skills.read_humanego_episode SESSION OUTPUT --frames 413",
}

import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import numpy as np

from tool_library.common import rigcam as RC

ARIA_NAMES = ("thumb_tip", "index_tip", "middle_tip", "ring_tip", "pinky_tip", "wrist",
              "thumb_mcp", "thumb_ip", "index_mcp", "index_pip", "index_dip",
              "middle_mcp", "middle_pip", "middle_dip", "ring_mcp", "ring_pip",
              "ring_dip", "pinky_mcp", "pinky_pip", "pinky_dip", "palm_center")


def rigid(value, name):
    t = np.asarray(value, dtype=float)
    if (t.shape != (4, 4) or not np.isfinite(t).all()
            or not np.allclose(t[3], [0, 0, 0, 1], atol=1e-6, rtol=0)
            or not np.allclose(t[:3, :3].T @ t[:3, :3], np.eye(3), atol=1e-5, rtol=0)
            or not np.isclose(np.linalg.det(t[:3, :3]), 1, atol=1e-5, rtol=0)):
        raise ValueError(f"{name} must be a finite proper rigid transform in metres")
    return t


def integer(value, name):
    if type(value) is not int or value < 0 or value > np.iinfo(np.int64).max:
        raise ValueError(f"{name} must be a nonnegative int64")
    return value


def read_humanego_episode(session, out_dir, frames=None, registration=None):
    import cv2

    source, out = Path(session).resolve(), Path(out_dir).resolve()
    if out.exists() or out.is_relative_to(source) or source.is_relative_to(out):
        raise ValueError("output must be new and separate from the source session")
    provenance = {}

    def read(path):
        if not path.resolve().is_relative_to(source):
            raise ValueError("source file escapes the selected session")
        raw = path.read_bytes()
        provenance[str(path.relative_to(source))] = hashlib.sha256(raw).hexdigest()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object: {path.name}")
        return value

    config = read(source / "preprocess/aria_cam_rgb_config.json")
    total = integer(config["total_frames"], "total_frames")
    first_ts = integer(config["first_ts"], "first_ts")
    if total == 0:
        raise ValueError("empty source sequence")
    ids = list(range(total)) if frames is None else list(frames)
    if not ids or any(type(i) is not int or i < 0 or i >= total for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("frames must be unique source IDs within total_frames")
    ids.sort()
    registration_data, transform = None, None
    if registration is not None:
        raw = Path(registration).read_bytes()
        registration_data = json.loads(raw)
        if not isinstance(registration_data.get("source"), str) or not registration_data["source"].strip():
            raise ValueError("registration requires a source description")
        transform = np.eye(4)
        transform[:3, :3] = np.asarray(registration_data["R_world_rig"], float).reshape(3, 3)
        transform[:3, 3] = np.asarray(registration_data["origin_world"], float).reshape(3)
        rigid(transform, "world_from_rig")
        registration_data = {**registration_data, "sha256": hashlib.sha256(raw).hexdigest()}

    records, images, cameras, stamps, intrinsics = [], [], [], [], []
    points = np.full((len(ids), 2, 21, 3), np.nan)
    pixels = np.full((len(ids), 2, 21, 2), np.nan)
    confidence = np.full((len(ids), 2), np.nan)
    valid = np.zeros((len(ids), 2), dtype=bool)
    for row, idx in enumerate(ids):
        directory = source / "preprocess/all_data" / f"{idx:05d}"
        cam = read(directory / "aria_cam_rgb.json")
        if cam["idx"] != idx or type(cam["idx"]) is not int:
            raise ValueError(f"camera frame identity mismatch: {idx}")
        stamp = integer(cam["ts"], "camera ts")
        if stamp < first_ts or (stamps and stamp <= stamps[-1]):
            raise ValueError("capture timestamps must increase from first_ts")
        t = rigid(cam["c2w"], "camera c2w")
        k = np.asarray(cam["k"], float)
        if (k.shape != (3, 3) or not np.isfinite(k).all() or k[0, 0] <= 0 or k[1, 1] <= 0
                or not np.allclose(k[2], [0, 0, 1]) or abs(k[0, 1]) > 1e-8 or abs(k[1, 0]) > 1e-8):
            raise ValueError("unsupported camera intrinsics")
        distortion = np.asarray(cam["d"], float)
        if not np.isfinite(distortion).all() or np.any(distortion != 0):
            raise ValueError("expected rectified RGB with zero distortion")
        image_path = directory / "rgb.png"  # Never follow stale paths embedded in source JSON.
        if not image_path.resolve().is_relative_to(source):
            raise ValueError("RGB escapes the selected session")
        raw = image_path.read_bytes()
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (cam["h"], cam["w"]):
            raise ValueError(f"missing or mismatched raw RGB at source frame {idx}")
        if records and (cam["w"], cam["h"]) != (records[0]["camera"]["w"], records[0]["camera"]["h"]):
            raise ValueError("changing image dimensions require a different camera stream")
        provenance[str(image_path.relative_to(source))] = hashlib.sha256(raw).hexdigest()
        hands = read(directory / "aria_hands.json") if (directory / "aria_hands.json").exists() else None
        training = read(directory / "training_data.json") if (directory / "training_data.json").exists() else None
        for name, data in (("hands", hands), ("training", training.get("metadata") if training is not None else None)):
            if data is not None and (integer(data["idx"], f"{name} idx") != idx
                                     or integer(data["ts"], f"{name} ts") != stamp):
                raise ValueError(f"{name} frame/time mismatch: {idx}")
        if training is not None:
            meta = training["metadata"]
            if not np.allclose(rigid(meta["c2w"], "training c2w"), t, atol=1e-6, rtol=0):
                raise ValueError("training and camera poses disagree")
            if not np.allclose(meta["k"], k, atol=1e-6, rtol=0) or (meta["w"], meta["h"]) != (cam["w"], cam["h"]):
                raise ValueError("training and camera calibration disagree")
        for side_idx, side in enumerate(("hand_l", "hand_r")):
            hand = (hands or {}).get(side)
            if hand is None:
                continue
            xyz, uv = np.asarray(hand["kpts_3d"], float), np.asarray(hand["kpts_2d"], float)
            score = float(hand["confidence"])
            if (xyz.shape != (21, 3) or uv.shape != (21, 2) or not np.isfinite(xyz).all()
                    or not np.isfinite(uv).all() or not np.isfinite(score) or not 0 <= score <= 1):
                raise ValueError("invalid Aria MPS21 hand record")
            if not np.allclose(rigid(hand["c2w"], "hand c2w"), t, atol=1e-6, rtol=0):
                raise ValueError("hand and camera coordinate transforms disagree")
            points[row, side_idx] = xyz @ t[:3, :3].T + t[:3, 3]
            pixels[row, side_idx], confidence[row, side_idx] = uv, score
            valid[row, side_idx] = True
        records.append({"camera": cam, "hands": hands, "training": training})
        images.append(image_path)
        cameras.append(t)
        stamps.append(stamp)
        intrinsics.append(k)
    if transform is not None and not np.allclose(intrinsics, intrinsics[0], atol=1e-6, rtol=0):
        raise ValueError("rigcam currently requires fixed intrinsics; source observations may still be imported without registration")
    arrays = dict(frame_idx=np.asarray(ids, dtype=np.int64), timestamps_ns=np.asarray(stamps, dtype=np.int64),
                  timestamps_s=(np.asarray(stamps, dtype=np.int64) - first_ts) / 1e9,
                  camera_world=np.asarray(cameras), intrinsics=np.asarray(intrinsics),
                  hand_world_xyz=points, hand_pixels=pixels, hand_valid=valid, hand_confidence=confidence,
                  hand_names=np.asarray(ARIA_NAMES), hand_sides=np.asarray(["left", "right"]),
                  skeleton=np.asarray("aria_mps21"))
    camera = None
    if transform is not None:
        basis, origin = transform[:3, :3], transform[:3, 3]
        arrays.update(R_world_rig=basis, origin_world=origin, hand_rig_xyz=(points - origin) @ basis)
        cam, k = records[0]["camera"], intrinsics[0]
        camera = RC.camera_dict(cam["w"], cam["h"], k[0, 0], k[1, 1], k[0, 2], k[1, 2],
                                basis.T @ cameras[0][:3, :3], basis.T @ (cameras[0][:3, 3] - origin),
                                {"source": "humanego", "camera_motion": "moving", "reference_frame": ids[0],
                                 "trajectory_npz": "observations.npz"})
    frame_paths = [str(out / f"frame{i:05d}.png") for i in ids]
    episode = {
        "source": "humanego_processed", "source_session": str(source),
        "n_frames": len(ids), "source_n_frames": total, "source_subset": ids != list(range(total)),
        "frames": frame_paths, "source_frame_indices": {Path(p).name: i for p, i in zip(frame_paths, ids)},
        "source_first_timestamp_ns": first_ts, "time_source": "Aria corrected capture timestamps; nanoseconds",
        "sensor_synchronization_verified": False,
        "skeleton": "aria_mps21", "hand_sides": ["left", "right"],
        "hand_valid_counts": valid.sum(axis=0).tolist(),
        "registration": registration_data, "registration_status": "supplied_unverified" if transform is not None else "not_supplied",
        "camera_json": str(out / "camera.json") if camera else None,
        "observations_npz": str(out / "observations.npz"),
        "task_success_evaluated": False, "training_eligible": False,
        "object_pose_source": "source training labels: static initialization / heuristic grasp lock; not measured tracking",
        "source_sha256": provenance,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".humanego-", dir=out.parent))
    try:
        np.savez_compressed(temporary / "observations.npz", **arrays)
        for path, target in zip(images, frame_paths):
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != provenance[str(path.relative_to(source))]:
                raise ValueError("source RGB changed during import")
            (temporary / Path(target).name).write_bytes(raw)
        (temporary / "source_records.json").write_text(json.dumps(records, ensure_ascii=False, allow_nan=False))
        (temporary / "episode.json").write_text(json.dumps(episode, indent=2, ensure_ascii=False, allow_nan=False))
        if camera:
            RC.save(camera, temporary / "camera.json")
            RC.camera_poses(temporary / "camera.json", ids)
        temporary.rename(out)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {"frames": frame_paths, "episode": episode,
            "camera": RC.load(out / "camera.json") if camera else None}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=TOOL["summary"])
    parser.add_argument("session")
    parser.add_argument("out_dir")
    parser.add_argument("--frames", type=int, nargs="+")
    parser.add_argument("--registration")
    args = parser.parse_args()
    result = read_humanego_episode(args.session, args.out_dir, args.frames, args.registration)
    print(json.dumps(result["episode"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
