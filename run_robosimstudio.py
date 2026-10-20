"""Run the first RoboSimStudio integration with DexAgent reports and recorded physics.

    python run_robosimstudio.py rigid
    python run_robosimstudio.py towel --gpu 2

These are backend acceptance examples, not a human-video reconstruction.
``sharpa`` imports the physical DexAgent rig and checks named actuator control.
Each attempt gets its own output directory.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import asdict, is_dataclass
from importlib.metadata import version
from pathlib import Path

from agent.run_reports import atomic_json, config_identity, finish_report, make_report, print_report, sha256_file

ROOT = Path(__file__).resolve().parent


def check(name, ok, observed=None, threshold=None, *, required=True, evidence=None):
    return {"id": name, "status": "passed" if ok else "failed", "required": required,
            "observed": observed, "threshold": threshold, "evidence": evidence}


def source_module(path):
    spec = importlib.util.spec_from_file_location("dexagent_rss_scene", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_tape(directory, particle_count, *, robot_actions=True):
    """Check the saved evidence independently of the in-memory recorder."""
    import numpy as np

    with np.load(directory / "steps.npz", allow_pickle=False) as z:
        steps = {k: z[k] for k in z.files}
    with np.load(directory / "privileged.npz", allow_pickle=False) as z:
        states = {k: z[k] for k in ("body_q", "particle_q") if k in z}
    times = steps.get("sim_time", np.zeros(0))
    checks = [check("recorded_time", len(times) > 1 and bool(np.all(np.diff(times) > 0)), len(times)),
              check("recorded_finite", all(bool(np.isfinite(a).all()) for a in [*steps.values(), *states.values()])),
              check("recorded_frame_counts", bool(states) and all(len(a) == len(times) for a in states.values()),
                    {k: len(a) for k, a in states.items()})]
    if robot_actions:
        checks.append(check("action_and_joint_frames", all(
            key in steps and steps[key].ndim == 2 and len(steps[key]) == len(times) and steps[key].shape[1] > 0
            for key in ("action", "joint_q", "joint_qd"))))
    else:
        checks.append(check("passive_asset_recording", "action" in steps and steps["action"].shape == (len(times), 0)))
    with np.load(directory / "capture.npz", allow_pickle=False) as z:
        capture_steps = z["step"].reshape(-1)
        capture_ok = len(capture_steps) == len(times) + 1 and bool(np.all(np.diff(capture_steps) == 1))
    checks.append(check("complete_control_capture", capture_ok, {"capture": len(capture_steps), "control": len(times)}))
    if particle_count:
        q = states.get("particle_q", np.zeros((0, 0, 3)))
        checks.append(check("complete_cloth_state", q.shape == (len(times), particle_count, 3), list(q.shape)))
    return checks, times, states


def render_tape(sim, episode_dir, out, fps, camera=None, hand_labels=None, min_hand_pixels=100):
    """Re-render recorded states at exact simulation timestamps, without physics."""
    import cv2
    import imageio.v2 as imageio
    import numpy as np
    from robosimstudio.core.cameras import CameraSpec
    from robosimstudio.core.render.offline import StateReplay

    with np.load(episode_dir / "steps.npz", allow_pickle=False) as z:
        times = z["sim_time"].astype(float)
    with np.load(episode_dir / "privileged.npz", allow_pickle=False) as z:
        arrays = {k: z[k] for k in ("body_q", "particle_q") if k in z}
    lighting = {}
    if camera is None:
        center = np.asarray(sim["towel"].particle_q.mean(axis=0) if "towel" in sim.names()
                            else sim["cube"].position, dtype=float)
        center[2] = float(sim.scene.handles.surface_z) + 0.03
        camera = CameraSpec(name="overview", width=640, height=480,
                            eye=tuple(center + [0.60, -0.45, 0.52]), look_at=tuple(center), fov_y=48)
    else:
        lighting = {"shadows": False, "background": (0.9, 0.9, 0.92),
                    "light_direction": (0.4, -0.4, -1), "fill_lights": (((-0.4, 0.4, -1), 0.6),)}
    if hand_labels is not None:
        lighting["shape_labels"] = hand_labels
    renderer = sim.make_renderer("tiled", cameras=[camera], **lighting)
    replay = StateReplay(sim.model, strict=True)
    targets = times[0] + np.arange(int(np.floor((times[-1] - times[0]) * fps)) + 1) / fps
    indices = np.searchsorted(times, targets, side="right") - 1
    video = out / "simulation.mp4"
    hand_pixels = {"left": [], "right": []} if hand_labels is not None else {}
    try:
        with imageio.get_writer(video, fps=fps, codec="libx264", macro_block_size=1) as writer:
            for frame, idx in enumerate(indices):
                state = replay.write({k: a[int(idx)] for k, a in arrays.items()})
                rendered = renderer.render(state)[camera.name]
                rgb = np.asarray(rendered["rgb"], dtype=np.uint8)
                for side, label in (("left", 1), ("right", 2)) if hand_pixels else ():
                    hand_pixels[side].append(int(np.count_nonzero(rendered["seg"] == label)))
                writer.append_data(rgb)
                if frame in {0, len(indices) // 2, len(indices) - 1}:
                    imageio.imwrite(out / f"preview_{frame:05d}.png", rgb)
    finally:
        renderer.close()
    atomic_json(out / "video_frame_map.json", {"fps": fps, "trajectory_indices": indices.tolist(),
                "video_times_s": (targets - targets[0]).tolist(), "simulation_times_s": times[indices].tolist(),
                "source": "recorded_simulation_state", "human_video_synchronization": "not_evaluated",
                "camera": asdict(camera)})
    cap = cv2.VideoCapture(str(video))
    decoded = 0
    try:
        actual_fps = cap.get(cv2.CAP_PROP_FPS)
        while True:
            ok, image = cap.read()
            if not ok:
                break
            if image.shape[:2] != (camera.height, camera.width):
                raise ValueError(f"bad video frame dimensions: {image.shape}")
            decoded += 1
    finally:
        cap.release()
    minimum = {side: min(counts) for side, counts in hand_pixels.items()}
    return check("rendered_video", decoded == len(indices) and abs(actual_fps - fps) < 0.01 and
                 all(count >= min_hand_pixels for count in minimum.values()),
                 {"decoded_frames": decoded, "expected_frames": len(indices), "fps": actual_fps,
                  "min_visible_hand_pixels": minimum}, {"min_hand_pixels": min_hand_pixels} if hand_pixels else None)


def run(args, out, report):
    import numpy as np
    import robosimstudio as rss
    import warp as wp
    from robosimstudio.record import EpisodeRecorder
    from robosimstudio.record.store import RunMeta
    from robosimstudio.specs import CaptureSpec, RecordSpec

    wp.init()
    if args.task == "reference":
        from agent.rss_episode import run_reference
        return run_reference(args, out, report)
    if args.task == "candidate":
        from agent.rss_candidate import run_candidate
        return run_candidate(args, out, report)
    commit = subprocess.check_output(["git", "-C", str(ROOT / "robosimstudio"), "rev-parse", "HEAD"], text=True).strip()
    report["model_identity"] = {"robosimstudio": {"path": str(ROOT / "robosimstudio"), "commit": commit}}
    report["metrics"].update({"versions": {n: version(n) for n in ("numpy", "mujoco", "newton", "warp-lang", "jax")},
                              "python": sys.executable, "gpu": wp.get_device("cuda:0").name if wp.is_cuda_available() else None,
                              "scope": "backend_acceptance_example", "embodiment": {
                                  "towel": "yam_parallel_gripper", "rigid": "panda", "sharpa": "yam_bimanual_sharpa_44_finger_joints",
                                  "grasp": "yam_bimanual_sharpa_44_finger_joints", "simfoundry": "none_passive_rigid_assets"}[args.task]})
    demo = bridge = None
    imported = import_camera = None
    checks = report["checks"]
    if args.task in {"sharpa", "grasp"}:
        from agent.rss_sharpa import SharpaBridge
        backend_source = ROOT / "agent/rss_sharpa.py"
        (out / "sharpa_backend_source.py").write_bytes(backend_source.read_bytes())
        report["inputs"]["backend_source"] = {"path": str(backend_source), "sha256": sha256_file(backend_source)}
        for name in ("rss_scene.py", "rss_grasp.py"):
            source = ROOT / "agent" / name
            (out / name).write_bytes(source.read_bytes())
        scene_source = None
        if args.task == "grasp":
            from agent.rss_grasp import create_task
            scene_source = create_task(out / "task")
        bridge = SharpaBridge(out, source=scene_source, physics_device=args.physics_device,
                              rig_translation=args.rig_translation)
        sim = bridge.sim
        report["metrics"].update(physics_device=bridge.physics_device, state_and_render_device=str(sim.model.device))
        if args.task == "grasp" and bridge.physics_device == "cpu":
            checks.append({"id": "gpu_rigid_contact_status", "status": "skipped", "required": False,
                           "evidence": "CPU rigid physics selected; GPU rigid contacts are evaluated in a separate acceptance run. GPU is used for rendering."})
        report["model_identity"]["sharpa_rig"] = {"path": str(out / "runtime_scene.xml"), "sha256": bridge.rig.scene_sha256}
        try:
            checks.extend(bridge.audit(check))
            atomic_json(out / "sharpa_manifest.json", bridge.manifest())
            atomic_json(out / "stage_report.json", report)
            if any(c["status"] != "passed" for c in checks if c.get("required", True)):
                raise RuntimeError("Sharpa import audit failed; motion was not started")
            bridge.start()
            q, v = bridge.joint_state()
            checks.append(check("calibrated_start_state", bool(np.allclose(q, bridge.home, atol=1e-6) and np.all(v == 0))))
            if checks[-1]["status"] != "passed":
                raise RuntimeError("Sharpa warm-up did not restore calibrated home; motion was not started")
        except BaseException:
            sim.close()
            raise
    elif args.task == "simfoundry":
        from agent.rss_simfoundry import build_import
        report["metrics"].update(scope="simfoundry_rigid_asset_validation", training_eligible=False,
                                 physics_device=args.physics_device)
        sim, imported, import_camera = build_import(args, out, report, check)
    elif args.task == "towel":
        source = ROOT / "robosimstudio/examples/08_demos/fold_the_towel/fold_the_towel.py"
        demo = source_module(source)
        report["inputs"]["scene_source"] = {"path": str(source), "sha256": sha256_file(source)}
        sim = demo.build()
        report["metrics"]["coupling"] = "one_way"
    else:
        scene = rss.Scene(cell="bench_075", physics=rss.MUJOCO_500HZ)
        scene.add(rss.box(name="cube", half=(0.025,) * 3, at=(0, 0, 0.2)))
        scene.add(rss.robot("panda"))
        sim = scene.build(control_mode="joint_position")
    session = None
    recorder = EpisodeRecorder(out / "episodes", RecordSpec(record_video=False, particle_limit=sim.model.particle_count + 1),
                               capture=CaptureSpec(enabled=True, contacts=False))
    try:
        atomic_json(out / "scene_manifest.json", {
            "schema": "dexagent.rss_scene.v1", "backend": "robosimstudio", "scope": report["metrics"]["scope"],
            "commit": commit, "physics": asdict(sim.request.physics), "pose_quaternion_order": "xyzw",
            "frame": "rss_world", "surface_z_m": float(sim.scene.handles.surface_z),
            "joint_labels": list(sim.model.joint_label), "joint_q_start": sim.model.joint_q_start.numpy().tolist(),
            "joint_qd_start": sim.model.joint_qd_start.numpy().tolist(),
            "entities": {name: asdict(sim[name].spec) for name in sim.names() if is_dataclass(sim[name].spec)},
            "action_semantics": "no robot commands; passive asset import validation" if imported else
                                "sharpa_manifest.json: joint_reference position targets; actual actuator controls saved separately" if bridge else
                                "solver joint_target_q, indexed by joint_q_start; not Sharpa commands",
            "source_scene_sha256": report["inputs"].get("scene_source", {}).get("sha256")})
        recorder.store.write_run_meta(RunMeta(run_name=out.name, embodiment=report["metrics"]["embodiment"],
                                            device=str(sim.model.device), provenance={"commit": commit, "scope": report["metrics"]["scope"]}))

        def record(action=None):
            if not sim.state_is_finite():
                raise RuntimeError("simulation state became non-finite")
            if action is None:
                if bridge:
                    raise ValueError("Sharpa recording requires the joint_reference action")
                action = np.zeros(0) if imported else sim.control.joint_target_q.numpy().copy()
            proprio = {"joint_q": sim.state.joint_q.numpy(), "joint_qd": sim.state.joint_qd.numpy()}
            if bridge:
                proprio["actuator_ctrl"] = sim.control.mujoco.ctrl.numpy()[bridge.ctrl_indices]
            recorder.step(sim, action, {"proprio": proprio}, 0.0, {})

        if demo:
            from robosimstudio_studio.session import StudioConfig, StudioSession
            session = StudioSession(sim, config=StudioConfig(port=0, panels="drive", speed="max", live=False, out=str(out / "studio")))
            session.begin_run()
            session.add_routines({"Fold the towel": demo.routine})
            panel = session.panels[-1]
            hz = float(sim.request.physics.control_hz)
            for _ in range(5):
                session.tick()
            recorder.begin(sim.episode, sim)
            panel.requested = "Fold the towel"
            started = sim.step_count
            last_progress = time.monotonic()
            for step in range(round(60 * hz)):
                if session.tick():
                    record()
                if panel.gen is None and sim.step_count > started:
                    break
                if time.monotonic() - last_progress >= 15:
                    print(f"[towel] simulated {sim.sim_time:.1f}s, recording {step + 1} steps", flush=True)
                    last_progress = time.monotonic()
            metrics = demo.fold_measures(sim)
            report["metrics"].update(metrics)
            limits = args.thresholds
            checks.extend([check("routine_completed", panel.gen is None and "done" in str(panel._status.value)),
                           check("fold_extent", metrics["fold_extent"] <= limits["max_fold_extent"],
                                 metrics["fold_extent"], limits["max_fold_extent"]),
                           check("fold_score", metrics["fold_score"] >= limits["min_fold_score"],
                                 metrics["fold_score"], limits["min_fold_score"]),
                           check("settle_speed", metrics["settle_speed"] < limits["max_settle_speed_mps"],
                                 metrics["settle_speed"], limits["max_settle_speed_mps"]),
                           check("released", session.arms["left"].gripper_command == 0.0)])
            checks.extend([{"id": "reaction_forces", "required": False, "status": "warning",
                            "evidence": "upstream example uses one-way cloth coupling"},
                           {"id": "real_material_accuracy", "required": False, "status": "skipped",
                            "evidence": "example uses a tuned 3 kPa towel core; no real towel measurement supplied"}])
        elif imported:
            from agent.rss_simfoundry import validate_import
            import_checks, metrics = validate_import(sim, imported, recorder, record, args.simfoundry_limits, check)
            checks.extend(import_checks)
            report["metrics"].update(metrics)
        elif args.task == "grasp":
            from agent.rss_grasp import run_task
            grasp_checks, metrics = run_task(bridge, recorder, record, out, args.grasp_limits, check, no_grip=args.no_grip)
            checks.extend(grasp_checks)
            report["metrics"].update(metrics)
        elif bridge:
            checks.extend(run_sharpa_motion(bridge, recorder, record, out, args.sharpa_limits))
            report["metrics"].update(json.loads((out / "sharpa_motion_metrics.json").read_text()))
        else:
            recorder.begin(sim.episode, sim)
            for _ in range(150):
                sim.step()
                record()
            z = float(sim["cube"].position[2])
            checks.append(check("cube_support", abs(z - 0.775) < 0.003, z, "z=0.775 +/-0.003 m"))
        checks.append(check("simulation_finite", sim.state_is_finite()))
        success = all(c["status"] == "passed" for c in checks if c.get("required", True))
        row = recorder.end(sim, {"success": success, "metrics": report["metrics"],
                                "close_kind": "success" if success else "failed"})
        recorder.store.append_result(row)
        recorder.store.write_run_meta(RunMeta(run_name=out.name, embodiment=report["metrics"]["embodiment"],
                                            device=str(sim.model.device), episodes=1, successes=int(success),
                                            provenance={"commit": commit, "scope": report["metrics"]["scope"]}))
        recorder.close()
        checks.append(check("recorder", not recorder.failures, recorder.failures))
        episode = out / "episodes/0000"
        tape_checks, times, _ = validate_tape(episode, sim.model.particle_count, robot_actions=not bool(imported))
        checks.extend(tape_checks)
        report["metrics"].update({"recorded_frames": len(times), "particle_count": sim.model.particle_count,
                                  "simulation_duration_s": float(times[-1] - times[0])})
        if args.render:
            hand_labels = None
            if bridge:
                hand_labels = {}
                for shape, body in enumerate(sim.model.shape_body.numpy()):
                    name = sim.model.body_label[int(body)].rsplit("/", 1)[-1] if body >= 0 else ""
                    hand_labels[shape] = 1 if name.startswith("robot_left_left_") else 2 if name.startswith("robot_right_right_") else 0
            checks.append(render_tape(sim, episode, out, args.fps, bridge.camera("workspace") if bridge else import_camera,
                                      hand_labels, args.sharpa_limits["min_hand_pixels"]))
            report["metrics"]["renderer"] = "tiled; diagnostic collision-geometry rendering"
        else:
            checks.append({"id": "rendered_video", "required": False, "status": "skipped", "evidence": "--no-render"})
        artifacts = [str(p) for p in out.rglob("*") if p.is_file() and p.name != "stage_report.json"]
        atomic_json(out / "artifact_hashes.json", {str(Path(p).relative_to(out)): sha256_file(p) for p in artifacts})
        finish_report(report, checks=checks, artifacts=[*artifacts, str(out / "artifact_hashes.json")])
    finally:
        recorder.close()
        if session:
            session.teardown()
        sim.close()


def run_sharpa_motion(bridge, recorder, record, out, limits):
    """Small motion with a separate CPU controller and local raw-control audits."""
    import mujoco
    import numpy as np

    sim, m = bridge.sim, bridge.source
    home = bridge.home
    delta = np.zeros_like(home)
    for j, name in enumerate(bridge.joint_names):
        if "_arm_dof_joint1" in name:
            delta[j] = 0.04 if "robot_left_" in name else -0.04
        elif name.endswith(("_PIP", "_DIP", "_MCP_FE")):
            delta[j] = 0.20
    target = np.clip(home + delta, *m.jnt_range.T)
    cpu, local, raw_replay = (mujoco.MjData(m) for _ in range(3))
    for data in (cpu, local, raw_replay):
        data.qpos[:] = home
        mujoco.mj_forward(m, data)
    hz = sim.request.physics.control_hz
    trajectory, controls, targets, cpu_states, replay_errors, local_errors, raw_errors, contact_counts = ([] for _ in range(8))
    recorder.begin(sim.episode, sim)
    last_progress = time.monotonic()
    for step in range(round(2.5 * hz)):
        t = (step + 1) / hz
        fraction = np.interp(t, [0, 0.3, 1.0, 1.4, 2.1, 2.5], [0, 0, 1, 1, 0, 0])
        fraction = fraction * fraction * (3 - 2 * fraction)
        goal = home + fraction * (target - home)
        before_q, before_v = bridge.joint_state()
        local.qpos[:] = before_q
        local.qvel[:] = before_v
        local.qacc_warmstart[:] = 0
        action = bridge.position_controls(goal)
        cpu.ctrl[:] = bridge.position_controls(goal, qpos=cpu.qpos, qvel=cpu.qvel)
        local.ctrl[:] = raw_replay.ctrl[:] = action
        sim.step(goal, mode="joint_reference")
        if not sim.state_is_finite():
            raise RuntimeError("Sharpa state became non-finite")
        for _ in range(sim.request.physics.substeps):
            for data in (cpu, local, raw_replay):
                mujoco.mj_step(m, data)
        q, _ = bridge.joint_state()
        replay_errors.append(float(abs(q - cpu.qpos).max()))
        local_errors.append(float(abs(q - local.qpos).max()))
        raw_errors.append(float(abs(q - raw_replay.qpos).max()))
        cpu_states.append(cpu.qpos.copy())
        contact_counts.append(cpu.ncon)
        trajectory.append(q.copy()); controls.append(action); targets.append(goal)
        record(goal)
        if time.monotonic() - last_progress >= 15:
            print(f"[sharpa] simulated {sim.sim_time:.2f}/2.5s", flush=True)
            last_progress = time.monotonic()
    q = np.asarray(trajectory)
    targets = np.asarray(targets)
    arm = np.asarray(["_arm_" in n for n in bridge.joint_names])
    finger = ~arm
    moved = abs(q - home).max(axis=0)
    active = abs(delta) > 0
    metrics = {"max_arm_tracking_error_rad": float(abs(q[:, arm] - targets[:, arm]).max()),
               "max_finger_tracking_error_rad": float(abs(q[:, finger] - targets[:, finger]).max()),
               "max_final_error_rad": float(abs(q[-1] - home).max()),
               "max_cpu_replay_error_rad": max(replay_errors), "max_source_contacts": max(contact_counts),
               "max_local_dynamics_error_rad": max(local_errors), "max_open_loop_torque_replay_error_rad": max(raw_errors),
               "min_commanded_joint_motion_rad": float(moved[active].min())}
    atomic_json(out / "sharpa_motion_metrics.json", metrics)
    np.savez_compressed(out / "sharpa_named_trajectory.npz", qpos=q, targets=targets, controls=controls,
                        cpu_replay_qpos=cpu_states, local_dynamics_error_rad=local_errors,
                        open_loop_torque_error_rad=raw_errors,
                        joint_names=bridge.joint_names, actuator_names=bridge.actuator_names,
                        time_s=np.arange(1, len(q) + 1) / hz)
    return [check("arm_tracking", metrics["max_arm_tracking_error_rad"] < limits["max_arm_error_rad"],
                  metrics["max_arm_tracking_error_rad"], limits["max_arm_error_rad"]),
            check("finger_tracking", metrics["max_finger_tracking_error_rad"] < limits["max_finger_error_rad"],
                  metrics["max_finger_tracking_error_rad"], limits["max_finger_error_rad"]),
            check("commanded_joints_moved", metrics["min_commanded_joint_motion_rad"] > limits["min_motion_rad"],
                  metrics["min_commanded_joint_motion_rad"], limits["min_motion_rad"]),
            check("returned_to_home", metrics["max_final_error_rad"] < limits["max_final_error_rad"],
                  metrics["max_final_error_rad"], limits["max_final_error_rad"]),
            check("independent_cpu_replay", metrics["max_cpu_replay_error_rad"] < limits["max_cpu_replay_error_rad"],
                  metrics["max_cpu_replay_error_rad"], limits["max_cpu_replay_error_rad"]),
            check("local_raw_control_dynamics", metrics["max_local_dynamics_error_rad"] < limits["max_local_error_rad"],
                  metrics["max_local_dynamics_error_rad"], limits["max_local_error_rad"]),
            {"id": "open_loop_torque_replay", "required": False,
             "status": "passed" if max(raw_errors) < limits["max_cpu_replay_error_rad"] else "warning",
             "observed": max(raw_errors), "threshold": limits["max_cpu_replay_error_rad"],
             "evidence": "raw torque replay without feedback; recorded policy actions are joint references replayed with the controller"},
            {"id": "sharpa_contact_task", "required": False, "status": "skipped",
             "evidence": "this checks import and free-space actuation; grasping cloth and task scenes are not evaluated"}]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=("rigid", "towel", "sharpa", "grasp", "reference", "simfoundry", "candidate"))
    parser.add_argument("--scene-request", type=Path, help="immutable MJCF candidate creation or retry request")
    parser.add_argument("--simfoundry-stage8", type=Path, help="Stage 8 output containing canonical_mesh/ and info/")
    parser.add_argument("--simfoundry-stage11", type=Path, help="Stage 11 output containing scene_objects_info.json and objects/")
    parser.add_argument("--simfoundry-stage12", type=Path, help="Stage 12 output containing pb_scene_poses.json")
    parser.add_argument("--simfoundry-pose-info", type=Path, help="explicit Stage 8 info_interactive... directory if selected upstream")
    parser.add_argument("--simfoundry-collision", choices=("stage11", "convex_hull", "bounding_box"), default="stage11")
    parser.add_argument("--reference-run", type=Path, help="existing DexAgent run with Stage 1–3 evidence")
    parser.add_argument("--reference-control", choices=("feedback", "raw"), default="feedback",
                        help="reference feedforward plus bounded feedback, or diagnostic open-loop raw replay")
    parser.add_argument("--no-grip", action="store_true", help="negative control for grasp; must fail the grasp gates")
    parser.add_argument("--physics-device", choices=("cpu", "gpu"), help="rigid physics device; grasp defaults to the configured CPU profile")
    parser.add_argument("--rig-translation", nargs=3, type=float, default=(0.0, 0.0, 0.0), metavar=("X", "Y", "Z"),
                        help="sharpa/grasp: translate the complete source rig and its extra floor into RSS world [m]")
    parser.add_argument("--gpu", default=os.environ.get("CUDA_VISIBLE_DEVICES", "2"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-render", dest="render", action="store_false")
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--config", type=Path, default=ROOT / "dexagent.yaml")
    args = parser.parse_args(argv)
    if not all(math.isfinite(value) for value in args.rig_translation):
        parser.error("--rig-translation requires finite metre values")
    if args.task not in {"sharpa", "grasp"} and any(args.rig_translation):
        parser.error("--rig-translation is currently supported for sharpa/grasp only")
    if args.task == "candidate" and args.scene_request is None:
        parser.error("candidate requires --scene-request")
    if args.task == "reference" and args.reference_run is None:
        parser.error("reference requires --reference-run")
    if args.task == "simfoundry" and not all((args.simfoundry_stage8, args.simfoundry_stage11, args.simfoundry_stage12)):
        parser.error("simfoundry requires --simfoundry-stage8, --simfoundry-stage11 and --simfoundry-stage12")
    if args.fps < 1:
        parser.error("fps must be positive")
    from agent.config import load_config
    cfg = load_config(args.config)
    configured = (cfg.get("environments") or {}).get("pipeline")
    if configured and Path(configured).resolve() != Path(sys.executable).resolve():
        os.execv(configured, [configured, str(Path(__file__).resolve()), *(sys.argv[1:] if argv is None else argv)])
    args.thresholds = {"max_fold_extent": 0.6, "min_fold_score": 0.85, "max_settle_speed_mps": 0.02,
                       **(cfg.get("evaluation", {}).get("robosimstudio") or {})}
    args.sharpa_limits = {"max_arm_error_rad": 0.05, "max_finger_error_rad": 0.08, "min_motion_rad": 0.02,
                         "max_final_error_rad": 0.025, "max_cpu_replay_error_rad": 0.025, "max_local_error_rad": 5e-5,
                         "min_hand_pixels": 100,
                         **args.thresholds.get("sharpa", {})}
    args.grasp_limits = {"min_lift_m": 0.08, "max_arm_error_rad": 0.08, "max_penetration_m": 0.004,
                         "support_height_error_m": 0.006, "max_placement_error_m": 0.025,
                         "max_slip_m": 0.015, "min_contact_fraction": 0.9, "max_release_force_n": 0.05,
                         "max_settle_speed_mps": 0.02, "max_cpu_object_error_m": 0.03,
                         **args.thresholds.get("grasp", {})}
    args.physics_device = args.physics_device or ("cpu" if args.task in {"simfoundry", "candidate"} else
                        args.grasp_limits.get("physics_device", "cpu") if args.task in {"grasp", "reference"} else "gpu")
    args.simfoundry_limits = {"seconds": 3., "rest_window_s": .5, "max_drift_m": .01,
                             "max_rotation_rad": .05, "max_speed_mps": .02, "max_angular_speed_rad_s": .05,
                             "max_penetration_m": .004, **args.thresholds.get("simfoundry", {})}
    args.candidate_limits = {"seconds": 3., "rest_window_s": .5, "max_drift_m": .01,
                             "max_rotation_rad": .05, "max_rest_speed_mps": .02,
                             "max_rest_angular_speed_rad_s": .05, "max_penetration_m": .004,
                             "max_robot_error_rad": .05, **args.thresholds.get("candidate", {})}
    if args.task == "candidate":
        for name, value in args.candidate_limits.items():
            if not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                parser.error(f"evaluation.robosimstudio.candidate.{name} must be positive and finite")
        if args.candidate_limits["rest_window_s"] > args.candidate_limits["seconds"]:
            parser.error("candidate rest_window_s must not exceed seconds")
    if args.task == "simfoundry":
        for name, value in args.simfoundry_limits.items():
            if not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                parser.error(f"evaluation.robosimstudio.simfoundry.{name} must be positive and finite")
        if args.simfoundry_limits["rest_window_s"] > args.simfoundry_limits["seconds"]:
            parser.error("simfoundry rest_window_s must not exceed seconds")
    args.reference_limits = {"max_arm_error_rad": .025, "max_finger_error_rad": .08, "max_object_error_m": .01,
                             "feedback_omega_rad_s": 48., "min_silhouette_iou": .85, **args.thresholds.get("reference", {})}
    # All controls are set before importing GPU libraries.
    os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu
    os.environ.setdefault("MUJOCO_GL", "egl")
    if (cfg.get("models") or {}).get("hf_home"):
        os.environ["HF_HOME"] = cfg["models"]["hf_home"]
    os.environ.setdefault("JAX_PLATFORMS", "cpu")
    os.environ.setdefault("XLA_PYTHON_CLIENT_PREALLOCATE", "false")
    os.environ.setdefault("WARP_CACHE_PATH", str(ROOT / "runs/cache/warp"))
    out = (args.output or ROOT / "runs" / f"rss_{args.task}_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}").resolve()
    if args.task == "simfoundry":
        for source in (args.simfoundry_stage8, args.simfoundry_stage11, args.simfoundry_stage12, args.simfoundry_pose_info):
            if source is not None and (out.is_relative_to(source.resolve()) or source.resolve().is_relative_to(out)):
                parser.error("RSS output and SimFoundry input directories must not overlap")
    out.mkdir(parents=True, exist_ok=True)
    path = out / "stage_report.json"
    if path.exists():
        parser.error(f"output already contains a run: {out}; choose a fresh directory")
    report = make_report(run=out.name, profile="backend_eval", stage="robosimstudio", step=args.task)
    (out / "bridge_source.py").write_bytes(Path(__file__).read_bytes())
    report["config_sha256"] = config_identity(cfg)["effective_sha256"]
    report["inputs"] = {"bridge_source_sha256": sha256_file(__file__), "thresholds": args.thresholds}
    atomic_json(path, report)
    started = time.monotonic()
    try:
        run(args, out, report)
    except BaseException as exc:
        finish_report(report, failure=f"{type(exc).__name__}: {exc}",
                      next_action=f"inspect this report and rerun {args.task} into a fresh output directory")
        import traceback
        traceback.print_exc()
    finally:
        report["elapsed_s"] = round(time.monotonic() - started, 3)
        atomic_json(path, report)
        atomic_json(out / "run_summary.json", {"status": "passed_with_warnings" if report["status"] == "warning" else report["status"],
                    "scope": report["metrics"].get("scope", "backend_acceptance_example"), "full_pipeline_complete": False,
                    "report": str(path), "metrics": report["metrics"]})
        print_report(report, path)
    return 1 if report["status"] in {"failed", "blocked", "running"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
