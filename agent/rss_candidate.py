"""Validate a frozen task layout in RSS with the physical robot held at home."""

from __future__ import annotations

from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

from agent.run_reports import atomic_json, finish_report, sha256_file
from agent.scene_candidate import freeze_candidate, verify_snapshot


def run_candidate(args, out, report):
    import mujoco
    from agent.rss_sharpa import SharpaBridge
    from robosimstudio.record import EpisodeRecorder
    from robosimstudio.record.store import RunMeta
    from robosimstudio.specs import CaptureSpec, RecordSpec
    from run_robosimstudio import check, render_tape, validate_tape

    request = json.loads(args.scene_request.read_text())
    candidate = freeze_candidate(request, out / "candidate")
    identity = candidate["candidate_sha256"]
    report["inputs"]["candidate"] = {
        "sha256": identity,
        "path": str(out / "candidate/manifest.json"),
    }
    report["metrics"].update(
        scope="scene_candidate_validation",
        candidate_sha256=identity,
        training_eligible=False,
        task_success_evaluated=False,
        success_semantics="passive_layout_validation_only",
        physics_device=args.physics_device,
        python=sys.executable,
        versions={n: version(n) for n in ("mujoco", "newton", "warp-lang")},
    )
    repo = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(
        ["git", "-C", str(repo / "robosimstudio"), "rev-parse", "HEAD"], text=True
    ).strip()
    report["model_identity"] = {
        "robosimstudio": {"path": str(repo / "robosimstudio"), "commit": commit}
    }
    for name in (
        "rss_candidate.py",
        "scene_candidate.py",
        "rss_sharpa.py",
        "rss_scene.py",
    ):
        source = Path(__file__).with_name(name)
        (out / name).write_bytes(source.read_bytes())
        report["inputs"][name] = {"sha256": sha256_file(source)}
    atomic_json(out / "stage_report.json", report)
    bridge = SharpaBridge(
        out, source=out / "candidate/scene.xml", physics_device=args.physics_device
    )
    sim = bridge.sim
    recorder = EpisodeRecorder(
        out / "episodes",
        RecordSpec(record_video=False),
        capture=CaptureSpec(enabled=True, contacts=False),
    )
    checks, limits = report["checks"], args.candidate_limits
    try:
        checks.extend(bridge.audit(check))
        if any(c["status"] != "passed" for c in checks if c.get("required", True)):
            raise RuntimeError("candidate import audit failed; physics was not started")
        bridge.start()
        sim.control_mode = "joint_reference"
        checks.append(
            check(
                "calibrated_start_state",
                bool(
                    np.allclose(
                        sim.state.joint_q.numpy(),
                        bridge.source_q_to_import(bridge.initial_qpos),
                        atol=1e-6,
                        rtol=0,
                    )
                    and np.all(sim.state.joint_qd.numpy() == 0)
                ),
            )
        )
        if checks[-1]["status"] != "passed":
            raise RuntimeError("candidate initial state was changed during warm-up")
        manifest = bridge.manifest()
        manifest.update(
            candidate_sha256=identity,
            task_success_evaluated=False,
            policy_dataset_ready=False,
        )
        atomic_json(out / "sharpa_manifest.json", manifest)
        objects = candidate["objects"]
        bodies = [bridge.body_indices[item["name"]] for item in objects]
        initial = np.asarray(
            [
                item["position_m"]
                + item["quaternion_wxyz"][1:]
                + item["quaternion_wxyz"][:1]
                for item in objects
            ]
        )
        # Include child collision shapes of a free root in penetration checks.
        target_names = set()
        m = bridge.source
        roots = {m.body(item["name"]).id for item in objects}
        for bid in range(1, m.nbody):
            parent = bid
            while parent and parent not in roots:
                parent = int(m.body_parentid[parent])
            if parent in roots:
                target_names.add(m.body(bid).name)
        target_bodies = {bridge.body_indices[name] for name in target_names}
        target_shapes = np.isin(sim.model.shape_body.numpy(), list(target_bodies))
        source_state = mujoco.MjData(m)
        source_state.qpos[:] = bridge.initial_qpos
        mujoco.mj_forward(m, source_state)
        initial_overlap = max(
            (
                max(0.0, -float(c.dist))
                for c in source_state.contact
                if m.body(int(m.geom_bodyid[c.geom1])).name in target_names
                or m.body(int(m.geom_bodyid[c.geom2])).name in target_names
            ),
            default=0.0,
        )
        checks.append(
            check(
                "source_initial_object_penetration",
                initial_overlap <= limits["max_penetration_m"],
                initial_overlap,
                limits["max_penetration_m"],
            )
        )
        n = len(objects)
        max_drift, max_rotation = np.zeros(n), np.zeros(n)
        max_penetration, max_arm_error = 0.0, 0.0
        speeds = []
        hz = float(sim.request.physics.control_hz)
        recorder.store.write_run_meta(
            RunMeta(
                run_name=out.name,
                embodiment="yam_bimanual_sharpa_44_finger_joints",
                device=str(sim.model.device),
                provenance={
                    "scope": "scene_candidate_validation",
                    "candidate_sha256": identity,
                },
            )
        )
        recorder.begin(sim.episode, sim)
        for _ in range(max(2, round(limits["seconds"] * hz))):
            sim.step(bridge.home)
            if not sim.state_is_finite():
                raise RuntimeError("candidate physics became non-finite")
            poses = sim.state.body_q.numpy()[bodies].astype(float)
            velocities = sim.state.body_qd.numpy()[bodies]
            max_drift = np.maximum(
                max_drift, np.linalg.norm(poses[:, :3] - initial[:, :3], axis=1)
            )
            quat = poses[:, 3:] / np.linalg.norm(poses[:, 3:], axis=1, keepdims=True)
            original = initial[:, 3:] / np.linalg.norm(
                initial[:, 3:], axis=1, keepdims=True
            )
            max_rotation = np.maximum(
                max_rotation,
                2 * np.arccos(np.clip(abs((quat * original).sum(axis=1)), 0, 1)),
            )
            # RSS/Newton body velocities are [linear xyz, angular xyz].
            speeds.append(
                np.stack(
                    (
                        np.linalg.norm(velocities[:, :3], axis=1),
                        np.linalg.norm(velocities[:, 3:], axis=1),
                    ),
                    axis=1,
                )
            )
            q, v = bridge.joint_state()
            max_arm_error = max(max_arm_error, float(abs(q - bridge.home).max()))
            sim.contacts.update()
            overlaps = sim.contacts.penetrations()
            if len(overlaps):
                relevant = (
                    target_shapes[overlaps[:, 0].astype(int)]
                    | target_shapes[overlaps[:, 1].astype(int)]
                )
                max_penetration = max(
                    max_penetration, float(overlaps[relevant, 2].max(initial=0))
                )
            recorder.step(
                sim,
                bridge.home.copy(),
                {
                    "proprio": {
                        "joint_q": q,
                        "joint_qd": v,
                        "actuator_ctrl": sim.control.mujoco.ctrl.numpy()[
                            bridge.ctrl_indices
                        ],
                    }
                },
                0.0,
                {},
            )
        tail = np.asarray(speeds[-max(1, round(limits["rest_window_s"] * hz)) :]).max(
            axis=0
        )
        measured = {}
        for i, item in enumerate(objects):
            values = {
                "drift_m": float(max_drift[i]),
                "rotation_rad": float(max_rotation[i]),
                "rest_speed_mps": float(tail[i, 0]),
                "rest_angular_speed_rad_s": float(tail[i, 1]),
            }
            measured[item["name"]] = {
                **values,
                "initial_pose_xyzw": initial[i].tolist(),
                "final_pose_xyzw": sim.state.body_q.numpy()[bodies[i]].tolist(),
            }
            for metric, value in values.items():
                checks.append(
                    check(
                        f"{item['name']}.{metric}",
                        value <= limits[f"max_{metric}"],
                        value,
                        limits[f"max_{metric}"],
                    )
                )
        checks += [
            check(
                "sampled_object_penetration",
                max_penetration <= limits["max_penetration_m"],
                max_penetration,
                limits["max_penetration_m"],
            ),
            check(
                "robot_hold_error",
                max_arm_error <= limits["max_robot_error_rad"],
                max_arm_error,
                limits["max_robot_error_rad"],
            ),
        ]
        validated = all(
            c["status"] == "passed" for c in checks if c.get("required", True)
        )
        report["metrics"].update(
            objects=measured,
            thresholds=limits,
            max_penetration_m=max_penetration,
            sampling_interval_s=1 / hz,
            robot_hold_error_rad=max_arm_error,
        )
        # This is a scene acceptance recording, not a successful task demonstration.
        row = recorder.end(
            sim,
            {
                "success": False,
                "close_kind": "scene_validation",
                "metrics": {**report["metrics"], "scene_validation_passed": validated},
            },
        )
        recorder.store.append_result(row)
        recorder.store.write_run_meta(
            RunMeta(
                run_name=out.name,
                embodiment="yam_bimanual_sharpa_44_finger_joints",
                device=str(sim.model.device),
                episodes=1,
                successes=0,
                provenance={
                    "scope": "scene_candidate_validation",
                    "candidate_sha256": identity,
                },
            )
        )
        recorder.close()
        checks.append(check("recorder", not recorder.failures, recorder.failures))
        tape_checks, times, _ = validate_tape(out / "episodes/0000", 0)
        checks.extend(tape_checks)
        report["metrics"].update(
            recorded_frames=len(times),
            simulation_duration_s=float(times[-1] - times[0]),
        )
        if args.render:
            checks.append(
                render_tape(
                    sim, out / "episodes/0000", out, args.fps, bridge.camera("ego")
                )
            )
        else:
            checks.append(
                {
                    "id": "rendered_video",
                    "required": False,
                    "status": "skipped",
                    "evidence": "--no-render",
                }
            )
        checks.append(
            check(
                "candidate_snapshot_unchanged",
                verify_snapshot(out / "candidate")["candidate_sha256"] == identity,
            )
        )
        artifacts = [
            str(p)
            for p in out.rglob("*")
            if p.is_file() and p.name != "stage_report.json"
        ]
        atomic_json(
            out / "artifact_hashes.json",
            {str(Path(p).relative_to(out)): sha256_file(p) for p in artifacts},
        )
        finish_report(
            report,
            checks=checks,
            artifacts=[*artifacts, str(out / "artifact_hashes.json")],
        )
    finally:
        recorder.close()
        sim.close()
