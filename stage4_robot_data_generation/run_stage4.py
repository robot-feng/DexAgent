"""DexAgent Stage 4: robot data generation, end to end.

    python run_stage4.py <config.json>                    # all steps
    python run_stage4.py <config.json> --steps render,composite

Steps, in order:
    randomize   draw initial states, adapt the trajectory, keep what passes the checks
    plate       erase the human and the objects from the human video (SAM 3 + VOID)
    export      write Blender bundles for every sample and camera view
    render      path-trace the robot and objects (Blender Cycles)
    composite   paste the renders onto the clean plate and encode the videos
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import uuid
import subprocess
import sys
import time
from pathlib import Path

from stage4.config import load_config

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from agent.run_reports import atomic_json, config_identity, finish_report, make_report, print_report, sha256_file, status_for_checks
from stage4.reporting import DEPENDENCIES, artifact_manifest, descendants, digest, input_identity, reusable

STEPS = ["randomize", "plate", "export", "render", "composite"]
HERE = Path(__file__).resolve().parent


def render(cfg, log=print):
    """Render every view of every sample, a few chunk processes at a time."""
    from stage4.export import current_samples, stage_views
    from stage4.render_launch import render_command
    rc = cfg["render"]
    chunks = int(rc["chunks"])
    parallel = int(rc.get("parallel", 1))
    if chunks < 1 or parallel < 1:
        raise ValueError("render.chunks and render.parallel must be positive")
    samples = current_samples(cfg)
    assert samples, "nothing to render: run the randomize step first"
    # only the bundles of the samples and views of this run (never a folder left by an earlier, larger run)
    views = [cfg.out / "render" / s.name / v for s in samples for v in stage_views(cfg)]
    missing = [f"{v.parent.name}/{v.name}" for v in views if not (v / "scene.json").exists()]
    assert not missing, f"no render bundle for {missing[:6]}: run the export step after randomize"
    # A selected render is a new attempt. Blender otherwise skips existing frames,
    # including files whose old provenance failed recovery validation.
    for view in views:
        shutil.rmtree(view / "frames", ignore_errors=True)
    jobs = [(v, i) for v in views for i in range(chunks)]
    running, t0, failed = [], time.time(), []
    chunk_report_path = cfg.out / "render" / "render_chunks.json"
    chunk_records = {f"{v.parent.name}/{v.name}/chunk_{i}": {"status": "pending", "log": str(v / f"render_{i}.log")}
                     for v, i in jobs}
    atomic_json(chunk_report_path, {"status": "running", "chunks": chunk_records})
    log(f"[render] {len(views)} views x {chunks} chunks, {parallel} processes at a time")
    try:
        while jobs or running:
            while jobs and len(running) < parallel:
                v, i = jobs.pop(0)
                lf = open(v / f"render_{i}.log", "w")
                try:
                    p = subprocess.Popen(render_command(v, f"{i}/{chunks}"), cwd=HERE, stdout=lf,
                                     stderr=subprocess.STDOUT, start_new_session=True)
                except BaseException:
                    lf.close()
                    raise
                running.append((p, v, i, lf))
                key = f"{v.parent.name}/{v.name}/chunk_{i}"
                chunk_records[key].update(status="running", started_at=time.strftime("%Y-%m-%d %H:%M:%S"))
                atomic_json(chunk_report_path, {"status": "running", "chunks": chunk_records})
            time.sleep(1.0)
            for item in list(running):
                p, v, i, lf = item
                if p.poll() is None:
                    continue
                lf.close()
                running.remove(item)
                key = f"{v.parent.name}/{v.name}/chunk_{i}"
                chunk_records[key].update(status="passed" if p.returncode == 0 else "failed",
                                          exit_code=int(p.returncode), elapsed_s=round(time.time() - t0, 3))
                atomic_json(chunk_report_path, {"status": "running", "chunks": chunk_records})
                if p.returncode != 0:
                    failed.append(f"{v} chunk {i} (see {v / f'render_{i}.log'})")
                elif i == chunks - 1:
                    log(f"[render] {v.parent.name}/{v.name} done ({time.time() - t0:.0f}s elapsed)")
    except BaseException as exc:
        for process, view, chunk, handle in running:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process, view, chunk, handle in running:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            finally:
                handle.close()
            chunk_records[f"{view.parent.name}/{view.name}/chunk_{chunk}"].update(
                status="interrupted", exit_code=process.returncode)
        for record in chunk_records.values():
            if record["status"] == "pending":
                record["status"] = "cancelled"
        atomic_json(chunk_report_path, {"status": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                                       "chunks": chunk_records, "failure": f"{type(exc).__name__}: {exc}"})
        raise
    if failed:
        atomic_json(chunk_report_path, {"status": "failed", "chunks": chunk_records, "failures": failed,
                                       "elapsed_s": round(time.time() - t0, 3)})
        raise RuntimeError("render failed:\n  " + "\n  ".join(failed))
    for v in views:
        n = json.loads((v / "scene.json").read_text())["n_frames"]
        have = len(list((v / "frames").glob("f*.png")))
        assert have == n, f"{v}: {have} of {n} frames rendered"
    atomic_json(chunk_report_path, {"status": "passed", "chunks": chunk_records,
                                   "elapsed_s": round(time.time() - t0, 3)})
    log(f"[render] all frames rendered in {time.time() - t0:.0f}s")


def _check(name, ok, *, required=True, observed=None, evidence=None, status=None):
    return {"id": name, "required": required,
            "status": status or ("passed" if ok else "failed"),
            "observed": observed, "evidence": evidence}


def _samples(cfg):
    from stage4.export import current_samples
    return current_samples(cfg)


def _validate_randomize(cfg):
    path = cfg.out / "randomize.json"
    summary = json.loads(path.read_text())
    samples = _samples(cfg)
    checks = [
        _check("requested_sample_count", summary.get("accepted", 0) >= summary.get("wanted", 1),
               observed={"accepted": summary.get("accepted"), "wanted": summary.get("wanted")}),
        _check("physics_replay_enabled", summary.get("physics_replay") is True,
               observed=summary.get("physics_replay")),
        _check("sample_reports", bool(samples) and all((s / "sample.json").is_file() for s in samples),
               observed={"sample_dirs": [s.name for s in samples]}),
    ]
    invalid = []
    for sample in samples:
        try:
            rec = json.loads((sample / "sample.json").read_text())
            checks_record = rec.get("checks") or {}
            if (rec.get("trajectory_source") != "physics_replay" or checks_record.get("ok") is not True
                    or (checks_record.get("physics") or {}).get("ok") is not True):
                invalid.append(sample.name)
            trajectory = sample / "trajectory.npz"
            if not trajectory.is_file():
                invalid.append(sample.name + ":missing trajectory.npz")
            elif sha256_file(trajectory) != rec.get("trajectory_sha256"):
                invalid.append(sample.name + ":trajectory hash mismatch")
            else:
                import numpy as np
                with np.load(trajectory, allow_pickle=False) as z:
                    qpos = z["qpos"]
                    names = z["joint_names"]
                    labels = z["labels"]
                    fps = float(z["fps"])
                    valid = (qpos.ndim == 2 and qpos.shape[0] > 0 and qpos.shape[1] == len(names)
                             and len(labels) == len(qpos) and np.isfinite(qpos).all() and fps > 0)
                    if not valid:
                        invalid.append(sample.name + ":trajectory schema, fps, frame labels, joint names, or finite values invalid")
        except (OSError, ValueError):
            invalid.append(sample.name + ":invalid sample.json")
    checks.append(_check("accepted_samples_replayed", not invalid,
                         observed={"samples": len(samples)}, evidence=invalid or None))
    checks.append(_check("original_episode_replay", not cfg.get("include_original") or
                         any(s.name == "original" for s in samples),
                         observed={"required": bool(cfg.get("include_original"))}))
    summary["acceptance_rate"] = round(summary.get("accepted", 0) / max(1, summary.get("attempts", 0)), 4)
    from collections import Counter
    summary["failure_reasons"] = dict(Counter(reason for row in summary.get("rows", [])
                                                for reason in [*(row.get("failed") or []),
                                                               *(row.get("physics_failed") or [])]))
    return checks, summary, [str(path), *[str(s / "sample.json") for s in samples]]


def _write_plate_contact_sheet(cfg):
    import cv2
    import numpy as np
    root = cfg.out / "plate"
    plate = cv2.imread(str(root / "plate.png"))
    frames = sorted((root / "frames").glob("frame*.png"))
    try:
        meta = json.loads((root / "meta.json").read_text())
    except (OSError, ValueError):
        meta = {}
    masks_dir = Path(meta.get("masks_dir") or (root / "masks"))
    if plate is None or not frames:
        return None
    candidates = sorted(set((0, len(frames) // 2, len(frames) - 1)))
    tile_h, tile_w = min(240, plate.shape[0]), min(320, plate.shape[1])
    rows = []
    for i in candidates:
        frame = cv2.imread(str(frames[i]))
        mask = cv2.imread(str(masks_dir / frames[i].name), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            continue
        if mask is not None:
            if mask.shape != frame.shape[:2]:
                mask = cv2.resize(mask, frame.shape[1::-1], interpolation=cv2.INTER_NEAREST)
            overlay = frame.copy()
            overlay[mask > 0] = (0.35 * overlay[mask > 0] + 0.65 * np.array([0, 220, 255])).astype(np.uint8)
        else:
            overlay = frame
        row = [cv2.resize(im, (tile_w, tile_h)) for im in (overlay, plate)]
        rows.append(np.concatenate(row, axis=1))
    if not rows:
        return None
    dest = root / "contact_sheet.jpg"
    cv2.imwrite(str(dest), np.concatenate(rows, axis=0), [cv2.IMWRITE_JPEG_QUALITY, 90])
    return dest


def _validate_plate(cfg):
    import cv2
    import numpy as np
    root = cfg.out / "plate"
    meta_path, plate_path = root / "meta.json", root / "plate.png"
    meta = json.loads(meta_path.read_text())
    plate = cv2.imread(str(plate_path))
    checks = [
        _check("plate_image_readable", plate is not None, evidence=str(plate_path)),
        _check("plate_dimensions", plate is not None and tuple(plate.shape[1::-1]) ==
               (int(cfg["cameras"]["width"]), int(cfg["cameras"]["height"])),
               observed=None if plate is None else {"width": plate.shape[1], "height": plate.shape[0]}),
    ]
    areas, empty, mask_shapes, pair_ious = [], [], [], []
    masks_path = Path(meta.get("masks_dir") or (root / "masks"))
    indices = meta.get("indices") or []
    if meta.get("source") == "user clean_plate":
        checks.append(_check("mask_generation", True, required=False, status="skipped",
                             observed="using user supplied clean plate"))
    else:
        mask_files = sorted(masks_path.glob("frame*.png"))
        checks.append(_check("mask_frame_count", bool(indices) and len(mask_files) == len(indices),
                             observed={"masks": len(mask_files), "indices": len(indices)}))
        invalid_indices = (len(indices) != len(set(indices)) or
                           any(int(i) < 0 or int(i) >= int(meta.get("n_source_frames", 0)) for i in indices))
        if invalid_indices:
            empty.append("invalid input frame indices")
        previous = None
        for file in mask_files:
            mask = cv2.imread(str(file), cv2.IMREAD_GRAYSCALE)
            if mask is None:
                empty.append(file.name + ":unreadable")
                continue
            if tuple(mask.shape[::-1]) != (int(cfg["cameras"]["width"]), int(cfg["cameras"]["height"])):
                empty.append(file.name + ":wrong dimensions")
            area = float(np.mean(mask > 0))
            areas.append(area)
            mask_shapes.append(tuple(mask.shape[::-1]))
            if area <= 0:
                empty.append(file.name)
            if previous is not None:
                union = np.logical_or(previous, mask > 0).sum()
                pair_ious.append(float(np.logical_and(previous, mask > 0).sum() / union) if union else 1.0)
            previous = mask > 0
        checks.append(_check("mask_nonempty", bool(mask_files) and not empty,
                             observed={"empty_frames": len(empty), "total": len(mask_files)}, evidence=empty or None))
        checks.append(_check("mask_coverage_measured", bool(areas), required=False,
                             status="passed" if areas else "warning",
                             observed={"mean": float(np.mean(areas)) if areas else None,
                                       "min": min(areas) if areas else None, "max": max(areas) if areas else None}))
        checks.append(_check("cross_frame_mask_change_measured", bool(pair_ious), required=False,
                             status="passed" if pair_ious else "warning",
                             observed={"adjacent_iou_mean": float(np.mean(pair_ious)) if pair_ious else None,
                                       "adjacent_iou_min": min(pair_ious) if pair_ious else None,
                                       "mask_dimensions": sorted({f"{w}x{h}" for w, h in mask_shapes})}))
    preview = _write_plate_contact_sheet(cfg)
    artifacts = [str(meta_path), str(plate_path)]
    if (root / "preview.mp4").is_file():
        artifacts.append(str(root / "preview.mp4"))
    if preview:
        artifacts.append(str(preview))
    metrics = {k: meta.get(k) for k in ("n_source_frames", "source_fps", "erase_coverage", "never_uncovered",
                                         "segmentation_backend", "segmentation_model_identity",
                                         "inpaint_backend", "inpaint_model", "indices")}
    inpaint_model = meta.get("inpaint_model")
    if inpaint_model and Path(inpaint_model).is_file():
        metrics["inpaint_model_sha256"] = sha256_file(inpaint_model)
    if areas:
        metrics["mask_area_fraction"] = {"mean": float(np.mean(areas)), "min": min(areas), "max": max(areas)}
    return checks, metrics, artifacts


def _validate_export(cfg):
    import hashlib
    import numpy as np
    root = cfg.out / "render"
    samples = _samples(cfg)
    views = ["ego", *cfg.get("cameras", {}).get("views", {}).keys()]
    issues = []
    frame_counts = {}
    for sample in samples:
        transform_path = root / sample.name / "transforms.npz"
        if not transform_path.is_file():
            issues.append(f"{sample.name}: missing transforms.npz")
            continue
        try:
            with np.load(transform_path, allow_pickle=False) as data:
                transforms = data["T"]
                if not np.isfinite(transforms).all():
                    issues.append(f"{sample.name}: nonfinite transforms")
                frame_counts[sample.name] = int(transforms.shape[0])
            for view in views:
                scene_path = root / sample.name / view / "scene.json"
                if not scene_path.is_file():
                    issues.append(f"{sample.name}/{view}: missing scene.json")
                    continue
                scene = json.loads(scene_path.read_text())
                camera = scene.get("camera") or {}
                missing_assets = []
                for geom in scene.get("geoms") or []:
                    for field, digest_field in (("obj", "obj_sha1"), ("texture", "texture_sha1")):
                        asset = geom.get(field)
                        if not asset:
                            continue
                        asset_path = Path(asset)
                        if not asset_path.is_file():
                            missing_assets.append(f"{asset}: missing")
                        elif geom.get(digest_field):
                            digest = hashlib.sha1(asset_path.read_bytes()).hexdigest()
                            if digest != geom[digest_field]:
                                missing_assets.append(f"{asset}: {digest_field} mismatch")
                if missing_assets:
                    issues.append(f"{sample.name}/{view}: invalid assets {missing_assets[:8]}")
                if int(scene.get("n_frames", -1)) != frame_counts[sample.name]:
                    issues.append(f"{sample.name}/{view}: scene/transform frame count mismatch")
                mapping = scene.get("trajectory_mapping") or {}
                stride = int(mapping.get("render_stride", 0))
                render_frames = int(mapping.get("render_frames", -1))
                source_frames = int(mapping.get("source_frames", 0))
                expected_last = (render_frames - 1) * stride
                if (stride < 1 or render_frames != frame_counts[sample.name] or source_frames < 1
                        or expected_last >= source_frames or mapping.get("last_source_frame") != expected_last
                        or mapping.get("first_source_frame") != 0
                        or mapping.get("source_frame_for_render_frame") != "render_index * render_stride"
                        or mapping.get("human_video_time_aligned") is not False):
                    issues.append(f"{sample.name}/{view}: invalid trajectory frame mapping")
                source_fps = float(mapping.get("source_fps", 0))
                render_fps = float(mapping.get("render_fps", 0))
                if source_fps <= 0 or render_fps <= 0 or not np.isclose(render_fps, source_fps / max(stride, 1), rtol=1e-5):
                    issues.append(f"{sample.name}/{view}: invalid source/render fps mapping")
                if not all(np.isfinite(float(camera.get(k, float("nan")))) for k in ("fx", "fy", "cx", "cy")):
                    issues.append(f"{sample.name}/{view}: invalid camera intrinsics")
                if view != "ego" and len(scene.get("camera_track") or []) != scene.get("n_frames"):
                    issues.append(f"{sample.name}/{view}: camera track length mismatch")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append(f"{sample.name}: {type(exc).__name__}: {exc}")
    checks = [_check("exported_scenes_and_transforms", bool(samples) and not issues,
                     observed={"samples": len(samples), "views": views, "frames": frame_counts}, evidence=issues or None)]
    return checks, {"samples": len(samples), "views": views, "frame_counts": frame_counts}, [str(root)]


def _write_render_contact_sheet(cfg):
    import cv2
    import numpy as np
    from stage4.export import current_samples, stage_views
    samples, views = current_samples(cfg), stage_views(cfg)
    selected = samples[:2]
    tiles = []
    for sample in selected:
        for view in views:
            vdir = cfg.out / "render" / sample.name / view
            frames = sorted((vdir / "frames").glob("f*.png"))
            if not frames:
                continue
            for index in sorted(set((0, len(frames) // 2, len(frames) - 1))):
                image = cv2.imread(str(frames[index]))
                if image is None:
                    continue
                image = cv2.resize(image, (320, 240))
                cv2.putText(image, f"{sample.name}/{view} frame {index}", (8, 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
                tiles.append(image)
    if not tiles:
        return None
    cols = min(3, len(tiles))
    rows = []
    for offset in range(0, len(tiles), cols):
        row = tiles[offset:offset + cols]
        while len(row) < cols:
            row.append(np.zeros_like(tiles[0]))
        rows.append(np.concatenate(row, axis=1))
    dest = cfg.out / "render" / "contact_sheet.jpg"
    cv2.imwrite(str(dest), np.concatenate(rows, axis=0), [cv2.IMWRITE_JPEG_QUALITY, 90])
    return dest


def _validate_render(cfg):
    import cv2
    from stage4.export import current_samples, stage_views
    root = cfg.out / "render"
    issues, counts = [], {}
    for sample in current_samples(cfg):
        for view in stage_views(cfg):
            vdir = root / sample.name / view
            try:
                scene = json.loads((vdir / "scene.json").read_text())
                expected = int(scene["n_frames"])
                frames = sorted((vdir / "frames").glob("f*.png"))
                counts[f"{sample.name}/{view}"] = len(frames)
                if len(frames) != expected:
                    issues.append(f"{sample.name}/{view}: {len(frames)}/{expected} frames")
                for frame in frames:
                    image = cv2.imread(str(frame))
                    if image is None or image.shape[:2] != (int(scene["height"]), int(scene["width"])):
                        issues.append(f"{sample.name}/{view}: unreadable/wrong-sized {frame.name}")
            except (OSError, ValueError, KeyError) as exc:
                issues.append(f"{sample.name}/{view}: {type(exc).__name__}: {exc}")
    checks = [_check("render_frame_count_and_decode", bool(counts) and not issues,
                     observed=counts, evidence=issues or None)]
    chunks_path = root / "render_chunks.json"
    chunk_report = json.loads(chunks_path.read_text()) if chunks_path.is_file() else {}
    chunk_records = chunk_report.get("chunks") or {}
    from stage4.export import current_samples, stage_views
    expected_chunks = int(cfg["render"]["chunks"]) * len(current_samples(cfg)) * len(stage_views(cfg))
    chunks_ok = chunk_report.get("status") == "passed" and len(chunk_records) == expected_chunks and bool(chunk_records) and all(
        rec.get("status") == "passed" and rec.get("exit_code") == 0 and Path(rec.get("log", "")).is_file()
        for rec in chunk_records.values())
    checks.append(_check("render_chunk_exit_codes_and_logs", chunks_ok,
                         observed={"status": chunk_report.get("status"), "chunks": chunk_records},
                         evidence=None if chunks_ok else "one or more render chunks have no passing exit record and log"))
    report = cfg.out / "render" / "render_device_report.json"
    devices = []
    for log_path in root.glob("*/*/render_*.log"):
        try:
            for line in log_path.read_text(errors="replace").splitlines():
                if "cycles device:" in line.lower():
                    devices.append(line.strip())
        except OSError:
            pass
    metrics = {"frame_counts": counts, "device_lines": sorted(set(devices)),
               "render_chunks": chunk_records, "render_elapsed_s": chunk_report.get("elapsed_s")}
    contact = _write_render_contact_sheet(cfg)
    artifacts = [str(root), str(chunks_path), *([str(report)] if report.exists() else [])]
    if contact:
        artifacts.append(str(contact))
    return checks, metrics, artifacts


def _validate_composite(cfg):
    import cv2
    import shutil
    import subprocess
    from stage4.export import current_samples, stage_views
    root = cfg.out / "render"
    expected_samples = current_samples(cfg)
    views = stage_views(cfg)
    issues, records = [], {}
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        issues.append("ffprobe executable is unavailable")
    for sample in expected_samples:
        record_path = sample / "composite.json"
        try:
            record = json.loads(record_path.read_text())
            for view in views:
                path = sample / f"{view}.mp4"
                cap = cv2.VideoCapture(str(path))
                ok = cap.isOpened()
                n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if ok else 0
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) if ok else 0
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) if ok else 0
                fps = float(cap.get(cv2.CAP_PROP_FPS)) if ok else 0.0
                got, frame = cap.read() if ok else (False, None)
                decoded = int(got)
                while got:
                    got, _ = cap.read()
                    decoded += int(got)
                cap.release()
                mapping = record.get("trajectory_mapping") or {}
                if not ok or not decoded or n != mapping.get("render_frames") or decoded != n or w <= 0 or h <= 0 or fps <= 0:
                    issues.append(f"{sample.name}/{view}: invalid video metadata or frame mapping")
                scene_path = root / sample.name / view / "scene.json"
                if scene_path.is_file():
                    scene = json.loads(scene_path.read_text())
                    if w != int(scene.get("width", -1)) or h != int(scene.get("height", -1)):
                        issues.append(f"{sample.name}/{view}: composite dimensions differ from exported scene")
                source_frames = int(mapping.get("source_frames", 0))
                stride = int(mapping.get("render_stride", 0))
                expected_last = (int(mapping.get("render_frames", 0)) - 1) * stride
                expected_fps = float(mapping.get("render_fps", 0))
                if (source_frames < 1 or stride < 1 or expected_last >= source_frames
                        or mapping.get("last_source_frame") != expected_last
                        or mapping.get("first_source_frame") != 0
                        or mapping.get("human_video_time_aligned") is not False
                        or expected_fps <= 0 or abs(fps - expected_fps) > max(0.02, expected_fps * 0.01)):
                    issues.append(f"{sample.name}/{view}: trajectory index/fps mapping is inconsistent")
                probe = {}
                if ffprobe:
                    result = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                                             "-show_entries", "format=duration:stream=width,height,r_frame_rate,nb_frames",
                                             "-of", "json", str(path)], capture_output=True, text=True, timeout=20)
                    if result.returncode:
                        issues.append(f"{sample.name}/{view}: ffprobe failed: {result.stderr[-500:]}")
                    else:
                        streams = json.loads(result.stdout).get("streams") or []
                        probe = streams[0] if streams else {}
                        if not probe:
                            issues.append(f"{sample.name}/{view}: ffprobe found no video stream")
                        else:
                            if int(probe.get("width", -1)) != w or int(probe.get("height", -1)) != h:
                                issues.append(f"{sample.name}/{view}: ffprobe dimensions differ from decoder")
                            frame_count = probe.get("nb_frames", "N/A")
                            if str(frame_count).isdigit() and int(frame_count) != decoded:
                                issues.append(f"{sample.name}/{view}: ffprobe frame count differs from decoder")
                            numerator, _, denominator = str(probe.get("r_frame_rate", "0/1")).partition("/")
                            probe_fps = float(numerator) / max(float(denominator or 1), 1e-12)
                            if abs(probe_fps - fps) > max(0.02, fps * 0.01):
                                issues.append(f"{sample.name}/{view}: ffprobe frame rate differs from decoder")
                            duration = float((json.loads(result.stdout).get("format") or {}).get("duration", 0))
                            expected_duration = n / fps if fps > 0 else 0
                            if duration <= 0 or abs(duration - expected_duration) > max(0.05, 1.0 / max(fps, 1.0)):
                                issues.append(f"{sample.name}/{view}: ffprobe duration differs from decoded frame count")
                records[f"{sample.name}/{view}"] = {"frames": n, "decoded_frames": decoded,
                    "width": w, "height": h, "fps": fps,
                    "duration_s": round(n / fps, 4) if fps > 0 else None, "ffprobe": probe}
        except (OSError, ValueError, TypeError) as exc:
            issues.append(f"{sample.name}: {type(exc).__name__}: {exc}")
    checks = [_check("composite_videos_and_trajectory_mapping", bool(expected_samples) and not issues,
                     observed={"videos": records, "samples": len(expected_samples)}, evidence=issues or None)]
    artifacts = [str(s / "composite.json") for s in expected_samples if (s / "composite.json").is_file()]
    artifacts.extend(str(s / "preview.mp4") for s in expected_samples if (s / "preview.mp4").is_file())
    return checks, {"videos": records, "human_comparison_alignment": "visual_resampling_only; not time synchronized"}, artifacts


VALIDATORS = {
    "randomize": _validate_randomize,
    "plate": _validate_plate,
    "export": _validate_export,
    "render": _validate_render,
    "composite": _validate_composite,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", help="path to the Stage 4 config (JSON)")
    action = ap.add_mutually_exclusive_group()
    action.add_argument("--steps", default="all", help=f"comma-separated subset of: {', '.join(STEPS)}")
    action.add_argument("--approve-review", choices=("plate", "render"),
                        help="record an explicit human visual approval, without regenerating artifacts")
    ap.add_argument("--reviewer", help="identity of the person who inspected the artifacts")
    ap.add_argument("--review-artifact-sha256", help="artifact_sha256 from the reviewed step report")
    ap.add_argument("--review-note", default="")
    args = ap.parse_args()
    steps = STEPS if args.steps == "all" else [s.strip() for s in args.steps.split(",")]
    if not steps or any(s not in STEPS for s in steps):
        ap.error(f"--steps must be a nonempty subset of {STEPS}")
    if args.approve_review and (not args.reviewer or not args.reviewer.strip() or not args.review_artifact_sha256):
        ap.error("--approve-review requires --reviewer and --review-artifact-sha256")
    invocation = os.environ.get("DEXAGENT_STAGE4_INVOCATION_ID") or uuid.uuid4().hex
    t0 = time.time()
    try:
        cfg = load_config(args.config)
    except BaseException as exc:
        # Even configuration/input errors must replace a previous successful run summary.
        source = Path(args.config).resolve()
        try:
            raw = json.loads(source.read_text())
        except (OSError, ValueError):
            raw = {}
        output = source.parent / raw.get("output_dir", "outputs")
        report_path = output / "run_report.json"
        if report_path.is_file():
            history = output / "reports" / "history" / invocation / "run_report.json"
            history.parent.mkdir(parents=True, exist_ok=True)
            history.write_bytes(report_path.read_bytes())
        status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        atomic_json(report_path, {"schema": "dexagent.stage4_run_report.v1", "invocation_id": invocation,
                                 "status": status, "release_ready": False, "steps": {},
                                 "failure": f"{type(exc).__name__}: {exc}"})
        raise SystemExit(130 if status == "interrupted" else 1) from exc
    cfg.out.mkdir(parents=True, exist_ok=True)
    report_dir = cfg.out / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    profile = str(cfg.get("evaluation_profile", "pipeline_eval"))
    selected = [] if args.approve_review else [s for s in STEPS if s in steps]
    config = config_identity(cfg)
    run_report_path = cfg.out / "run_report.json"
    step_reports = {}
    identity = None
    inputs = {}

    def log(msg):
        print(msg, flush=True)

    def archive(path):
        if path.is_file():
            old = report_dir / "history" / invocation / path.name
            old.parent.mkdir(parents=True, exist_ok=True)
            if not old.exists():
                old.write_bytes(path.read_bytes())

    def save(step):
        path = report_dir / f"{step}.json"
        step_reports[step]["report_path"] = str(path)
        atomic_json(path, step_reports[step])

    def persist(status="running", failure=None):
        complete = all(step_reports.get(s, {}).get("status") in {"passed", "warning"} for s in STEPS)
        pending = any(r.get("profile_review_pending") for r in step_reports.values())
        atomic_json(run_report_path, {
            "schema": "dexagent.stage4_run_report.v1", "run": str(cfg.get("name", "sample")),
            "invocation_id": invocation, "identity_sha256": identity,
            "config_sha256": config["effective_sha256"], "effective_config": config["effective_config"],
            "source_config_sha256": sha256_file(args.config), "inputs": inputs,
            "profile": profile, "status": status, "failure": failure,
            "release_ready": profile == "dataset_release" and complete and not pending and
                             status in {"passed", "passed_with_warnings"},
            "steps_requested": selected, "steps": step_reports,
            "elapsed_s": round(time.time() - t0, 3), "output_dir": str(cfg.out)})

    def new_report(step, status="skipped", reason=None):
        return dict(make_report(run=str(cfg.get("name", "sample")), profile=profile, stage="stage4",
                                step=step, status=status, inputs=inputs,
                                config_sha256=config["effective_sha256"], next_action=reason),
                    identity_sha256=identity, invocation_id=invocation, attempt_id=uuid.uuid4().hex)

    def apply_review(step):
        report = step_reports[step]
        if profile != "dataset_release" or step not in {"plate", "render"}:
            report["profile_review_pending"] = False
            return
        approval_path = report_dir / "reviews" / f"{step}.json"
        try:
            approval = json.loads(approval_path.read_text())
        except (OSError, ValueError):
            approval = {}
        approved = (approval.get("approved") is True and bool(approval.get("reviewer")) and
                    all(approval.get(k) == report.get(k) for k in
                        ("identity_sha256", "artifact_sha256", "attempt_id")))
        report["profile_review_pending"] = not approved
        report["checks"] = [c for c in report.get("checks", []) if c.get("id") != "visual_quality_review"]
        report["checks"].append(_check("visual_quality_review", approved, required=False,
                                       status="passed" if approved else "warning",
                                       observed=approval if approved else "pending explicit human inspection",
                                       evidence=str(approval_path) if approved else "review the contact sheet and full artifacts"))
        report["status"] = status_for_checks(report["checks"])
        report["next_action"] = None if approved else (
            f"After visual inspection: --approve-review {step} --reviewer NAME "
            f"--review-artifact-sha256 {report.get('artifact_sha256')}")

    archive(run_report_path)
    previous_signal = signal.getsignal(signal.SIGTERM)
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"received signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    active = None
    try:
        inputs = input_identity(cfg)
        identity = digest({"config": config["effective_sha256"], "inputs": inputs})
        for step in STEPS:
            path = report_dir / f"{step}.json"
            archive(path)
            try:
                report = json.loads(path.read_text())
                valid = reusable(cfg, step, report, identity)
            except (OSError, ValueError, TypeError):
                report, valid = {}, False
            if valid:
                step_reports[step] = report
                apply_review(step)
            else:
                step_reports[step] = new_report(step, "stale" if report else "skipped",
                    "run this step; no successful report with matching configuration, inputs, and artifacts")
            # Even a valid artifact cannot outlive an invalid dependency or a rerun of it.
            for dependency in DEPENDENCIES[step]:
                upstream = step_reports[dependency]
                recorded = report.get("dependency_attempts", {}).get(dependency)
                if upstream.get("status") not in {"passed", "warning"} or recorded != upstream.get("attempt_id"):
                    step_reports[step] = new_report(step, "stale", f"dependency {dependency} is not current")
            save(step)
        persist()
        log(f"[stage4] {cfg.get('name', 'sample')}: steps {selected} -> {cfg.out}")
        if args.approve_review:
            step = args.approve_review
            report = step_reports[step]
            if profile != "dataset_release":
                raise ValueError("human release approval requires evaluation_profile=dataset_release")
            if not reusable(cfg, step, report, identity):
                raise ValueError(f"{step}: no current verified artifacts to approve")
            if report.get("artifact_sha256") != args.review_artifact_sha256:
                raise ValueError("reviewed artifact hash differs from current artifacts; inspect the current report")
            atomic_json(report_dir / "reviews" / f"{step}.json", {
                "approved": True, "reviewer": args.reviewer.strip(), "note": args.review_note,
                "approved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                **{k: report[k] for k in ("identity_sha256", "artifact_sha256", "attempt_id")}})
            apply_review(step)
            save(step)
        for step in selected:
            active = step
            # Invalidate downstream reports before mutating any artifacts, including interrupted reruns.
            for later in descendants(step):
                step_reports[later] = new_report(later, "stale", f"rerun after {step}")
                save(later)
            report = new_report(step, "running")
            step_reports[step] = report
            save(step)
            persist()
            ts = time.time()
            missing = [d for d in DEPENDENCIES[step]
                       if not reusable(cfg, d, step_reports[d], identity)]
            if missing:
                raise ValueError(f"{step} requires current verified steps {missing}; run them first using --steps")
            report["dependency_attempts"] = {d: step_reports[d]["attempt_id"] for d in DEPENDENCIES[step]}
            log(f"[stage4] starting {step}; report: {report_dir / (step + '.json')}")
            if step == "render":
                render(cfg, log)
            else:
                import importlib
                importlib.import_module(f"stage4.{step}").run(cfg, log)
            checks, metrics, artifacts = VALIDATORS[step](cfg)
            finish_report(report, checks=checks, metrics=metrics, artifacts=artifacts, elapsed_s=time.time() - ts)
            if report["status"] == "failed":
                raise RuntimeError(f"required checks failed in Stage 4 step {step}")
            report["artifact_hashes"] = artifact_manifest(cfg, step, artifacts)
            if not report["artifact_hashes"]:
                raise RuntimeError(f"{step} produced no files for artifact identity")
            report["artifact_sha256"] = digest(report["artifact_hashes"])
            if step == "plate":
                report["model_identity"] = {"segmentation_backend": metrics.get("segmentation_backend"),
                    "segmentation": metrics.get("segmentation_model_identity"),
                    "inpainting": {"backend": metrics.get("inpaint_backend"), "path": metrics.get("inpaint_model"),
                                   "sha256": metrics.get("inpaint_model_sha256")}}
            apply_review(step)
            save(step)
            persist()
            print_report(report, report["report_path"], log=log)
            active = None
        incomplete = any(r.get("status") not in {"passed", "warning"} for r in step_reports.values())
        warning = any(r.get("status") == "warning" for r in step_reports.values())
        overall = "partial" if incomplete else "passed_with_warnings" if warning else "passed"
        persist(overall)
        log(f"[stage4] {overall.upper()} in {time.time() - t0:.0f}s -> {run_report_path}")
    except BaseException as exc:
        failure = f"{type(exc).__name__}: {exc}"
        status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        if active:
            report = step_reports[active]
            finish_report(report, failure=failure)
            report["status"] = status
            save(active)
            for later in selected[selected.index(active) + 1:]:
                step_reports[later] = new_report(later, "blocked", f"blocked by {active}: {failure}")
                save(later)
        persist(status, failure)
        log(f"[stage4] {status.upper()}: {failure}; report: {run_report_path}")
        raise SystemExit(130 if status == "interrupted" else 1) from exc
    finally:
        signal.signal(signal.SIGTERM, previous_signal)


if __name__ == "__main__":
    main()
