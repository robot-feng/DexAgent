"""A reproducible physical pick, carry, place and release task for the full rig."""
from __future__ import annotations

import json
import time
from pathlib import Path
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from agent.run_reports import atomic_json, finish_report, make_report, print_report, sha256_file
from tool_library.common import hand as H
from tool_library.common.rigid_robot import compile_rigid
from tool_library.skills.solve_arm_ik import solve_arm_ik

ROOT = Path(__file__).resolve().parents[1]


def create_task(output, *, xy=(0.22, 0.35), lift_m=0.10, carry_m=0.06, mass_kg=0.12):
    """Make a primitive task scene and a kinematic prior, never a welded object.

    The checked-in hand-only grasp is a seed. Arm reach, contact, lift and release
    must be evaluated afresh on the complete physical bench.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    prior_path = ROOT / "tool_library/tests/fixtures/grasp_robust/box_right_pinch3.json"
    prior = json.loads(prior_path.read_text())
    rig = compile_rigid(ROOT / "setup/scene.xml")
    m = rig.model
    q0 = m.key_qpos[0].copy()
    joints = [f"robot_right_arm_dof_joint{i}" for i in range(1, 7)]
    arm_q = np.asarray([m.jnt_qposadr[m.joint(n).id] for n in joints])
    # Add objects to the proposal before compiling its physical tree, so the
    # rigid compiler also builds arm-object pairs, not only hand-object pairs.
    root = ET.parse(ROOT / "setup/scene.xml").getroot()
    for asset in root.findall("./asset/*"):
        if asset.get("file"):
            asset.set("file", str((ROOT / "setup" / asset.get("file")).resolve()))
    cube = ET.SubElement(root.find("worldbody"), "body", name="task_cube", pos=f"{xy[0]} {xy[1]} 0.025")
    ET.SubElement(cube, "freejoint", name="task_cube_free")
    ET.SubElement(cube, "geom", name="task_cube_geom", type="box", size="0.025 0.02 0.025",
                  mass=str(mass_kg), friction="1 0.005 0.0001", rgba="0.8 0.35 0.12 1", contype="1", conaffinity="1")

    p = np.asarray(prior["pos"]) + [*xy, 0]
    rotation = H.euler_to_mat(prior["euler"])
    # Cartesian waypoints are solved on the real fixed arm-to-hand tree.
    points = [p + [0, 0, 0.06], p, p, p + [0, 0, lift_m], p + [0, 0, lift_m],
              p + [carry_m, 0, lift_m], p + [carry_m, 0, 0], p + [carry_m, 0, 0], p + [carry_m, 0, 0],
              p + [carry_m, 0, 0.08], p + [carry_m, 0, 0.08]]
    # A dexterous hand's wrist trajectory needs enough time to stay inside the
    # measured joint tracking gate; a fast Cartesian reach can exceed the motor
    # controller's torque-limited response even when its IK is valid.
    durations = [0.5, 1.5, 0.8, 2.8, 1.0, 1.2, 2.4, 0.4, 0.8, 1.6, 0.6]
    phases = ["settle", "approach", "close", "lift", "hold", "carry", "place", "place_settle", "release", "retreat", "final_settle"]
    rig_hand = H.HandRig.build(m, "robot_right_", "right")
    open_fingers = prior["finger_targets_open"]
    closed = H.press_targets(rig_hand, prior["finger_targets"], prior["grip_fingers"],
                             depth_rad=prior["press_depth_rad"], squeezed=prior.get("finger_targets_squeezed"))
    open_q, closed_q = q0.copy(), q0.copy()
    for short, value in open_fingers.items():
        open_q[rig_hand.finger_qpos[short]] = value
    for short, value in closed.items():
        closed_q[rig_hand.finger_qpos[short]] = value
    positions, finger_states, labels, times = [], [], [], []
    t = 0.0
    previous_p = points[0]
    previous_grip = 0.0
    for name, end, duration in zip(phases, points, durations, strict=True):
        grip = 1.0 if name in {"close", "lift", "hold", "carry", "place", "place_settle"} else 0.0
        for k in range(max(1, round(duration * 25))):
            u = (k + 1) / max(1, round(duration * 25))
            u = u * u * (3 - 2 * u)
            positions.append(previous_p + u * (end - previous_p))
            fraction = previous_grip + u * (grip - previous_grip)
            finger_states.append(open_q + fraction * (closed_q - open_q))
            t += duration / max(1, round(duration * 25))
            times.append(t); labels.append(name)
        previous_p, previous_grip = end, grip
    ik = solve_arm_ik(m, "robot_right_right_base_yaw", positions, rotation, joints=joints,
                      q0=q0[arm_q], qpos_base=q0, pos_tol_m=0.001, rot_tol_deg=0.5, iters=160, retries=8)
    if not ik["ok"]:
        raise ValueError(f"grasp arm IK failed: {ik['worst']}")
    targets = np.asarray(finger_states)
    targets[:, arm_q] = ik["qpos"]
    start = targets[0]
    for key in root.findall("./keyframe/key"):
        initial = rig.to_proposal_qpos(start) if key.get("name") == "home" else np.fromstring(key.get("qpos", ""), sep=" ")
        key.set("qpos", " ".join(str(float(x)) for x in np.r_[initial, xy, 0.025, 1, 0, 0, 0]))
        key.attrib.pop("ctrl", None)
    path = output / "task_scene.xml"
    path.write_text(ET.tostring(root, encoding="unicode"))
    np.savez_compressed(output / "task_plan.npz", times=times, targets=targets, phase=labels)
    atomic_json(output / "task_definition.json", {
        "schema": "dexagent.rss_grasp_task.v1", "kind": "primitive_rigid_pick_carry_place",
        "grasp_prior": {"path": str(prior_path), "sha256": sha256_file(prior_path), "reused_as": "kinematic_seed_only"},
        "object": {"body": "task_cube", "free_joint": "task_cube_free", "mass_kg": mass_kg,
                   "half_m": [0.025, 0.02, 0.025], "xy": list(xy)},
        "lift_m": lift_m, "carry_m": carry_m, "side": "right", "phases": dict(zip(phases, durations)),
        "ik_max_position_error_m": float(max(ik["pos_err_m"])), "ik_max_rotation_error_deg": float(max(ik["rot_err_deg"])),
        "object_drive": "free dynamics; no weld, mocap, applied object force or runtime state writes",
        "initial_robot_pose": "open pregrasp solved on the physical arm", "scene": str(path)})
    return path


def plan_at(plan, t):
    times, targets = plan["times"], plan["targets"]
    q = np.asarray([np.interp(t, times, targets[:, j]) for j in range(56)])
    i = min(int(np.searchsorted(times, t)), len(times) - 1)
    return q, str(plan["phase"][i])


def shape_groups(bridge):
    """Physics and rendering share the imported body identities."""
    groups = {name: [] for name in ("object", "hand", "table", *H.FINGERS)}
    for shape, body in enumerate(bridge.sim.model.shape_body.numpy()):
        name = bridge.sim.model.body_label[int(body)].rsplit("/", 1)[-1] if body >= 0 else ""
        if name == "task_cube":
            groups["object"].append(shape)
        if name == "table":
            groups["table"].append(shape)
        if name.startswith("robot_right_right_"):
            groups["hand"].append(shape)
            for finger in H.FINGERS:
                if f"_{finger}_" in name:
                    groups[finger].append(shape)
    return groups


def contact_sample(bridge, groups, t, phase, goal):
    from scipy.spatial.transform import Rotation

    sim = bridge.sim
    probe = sim.contacts
    if probe is None:
        raise RuntimeError("native contact probe is required for grasp evaluation")
    probe.update(sim.step_count)
    pose = sim.pipeline.snapshot.body_q
    obj = np.asarray(pose[bridge.body_indices["task_cube"]], float)
    hand = np.asarray(pose[bridge.body_indices["robot_right_right_base_yaw"]], float)
    finger_forces = np.asarray([probe.pairwise_force(groups["object"], groups[f]) for f in H.FINGERS])
    strengths = np.linalg.norm(finger_forces, axis=1)
    active = strengths > 0.02
    opposed = any(np.dot(finger_forces[a], finger_forces[b]) < -0.2 * strengths[a] * strengths[b]
                  for a in range(5) for b in range(a + 1, 5) if active[a] and active[b])
    overlaps = probe.penetrations()
    depths = [depth for a, b, depth in overlaps if int(a) in groups["object"] or int(b) in groups["object"]]
    q, _ = bridge.joint_state()
    arm = np.asarray(["_arm_" in n for n in bridge.joint_names])
    force = probe.pairwise_force(groups["object"], groups["hand"])
    return {"time_s": t, "phase": phase, "object_pose": obj, "hand_pose": hand,
            "hand_relative_object": Rotation.from_quat(hand[3:]).inv().apply(obj[:3] - hand[:3]),
            "hand_force_n": force, "finger_count": int(active.sum()), "opposed": bool(opposed),
            "table_contact": bool(np.linalg.norm(probe.pairwise_force(groups["object"], groups["table"])) > 0.05),
            "penetration_m": float(max(depths, default=0)), "arm_error_rad": float(abs(q[arm] - goal[arm]).max())}


def phase_checks(name, samples, definition, limits, check):
    """Score measured physics; neither commanded targets nor a lifted render count."""
    data = {key: np.asarray([s[key] for s in samples]) for key in samples[0] if key != "phase"}
    tail = slice(max(0, len(samples) - max(2, round(len(samples) * 0.25))), None)
    xyz = data["object_pose"][:, :3]
    end = xyz[tail].mean(axis=0)
    z0 = definition["object"]["half_m"][2]
    lifted = z0 + limits["min_lift_m"]
    checks = [check("arm_tracking", float(data["arm_error_rad"].max()) < limits["max_arm_error_rad"],
                    float(data["arm_error_rad"].max()), limits["max_arm_error_rad"]),
              check("object_penetration", float(data["penetration_m"].max()) < limits["max_penetration_m"],
                    float(data["penetration_m"].max()), limits["max_penetration_m"])]
    if name in {"settle", "final_settle", "place_settle", "release"}:
        checks.append(check("object_supported", abs(float(end[2]) - z0) < limits["support_height_error_m"] and
                            float(data["table_contact"][tail].mean()) >= 0.8,
                            {"height_m": float(end[2]), "table_contact_fraction": float(data["table_contact"][tail].mean())}))
    if name == "approach":
        drift = float(np.linalg.norm(end[:2] - definition["object"]["xy"]))
        checks.append(check("object_not_pushed_away", drift < limits["max_placement_error_m"], drift))
    if name == "close":
        fraction = float(((data["finger_count"][tail] >= 2) & data["opposed"][tail]).mean())
        checks.append(check("opposed_finger_contact", fraction >= limits["min_contact_fraction"], fraction))
    if name in {"lift", "hold", "carry"}:
        values = xyz[tail, 2] if name == "lift" else xyz[:, 2]
        checks.append(check("object_actually_lifted", float(values.min()) >= lifted, float(values.min() - z0), limits["min_lift_m"]))
        unsupported = float((~data["table_contact"][tail if name == "lift" else slice(None)]).mean())
        checks.append(check("no_table_support", unsupported >= 0.95, unsupported))
    if name in {"hold", "carry"}:
        grip = float(((data["finger_count"] >= 2) & data["opposed"]).mean())
        slip = float(np.linalg.norm(data["hand_relative_object"] - data["hand_relative_object"][0], axis=1).max())
        checks += [check("grip_maintained", grip >= limits["min_contact_fraction"], grip),
                   check("relative_slip", slip <= limits["max_slip_m"], slip, limits["max_slip_m"])]
    if name == "hold":
        weight = definition["object"]["mass_kg"] * 9.81
        force = float(data["hand_force_n"][:, 2].mean())
        checks.append(check("hand_supports_weight", abs(force - weight) <= weight * 0.6, force, {"expected_n": weight}))
    if name in {"carry", "final_settle"}:
        wanted = np.asarray(definition["object"]["xy"]) + [definition["carry_m"], 0]
        error = float(np.linalg.norm(end[:2] - wanted))
        checks.append(check("object_reached_destination", error < limits["max_placement_error_m"], error, limits["max_placement_error_m"]))
    if name in {"release", "final_settle"}:
        force = np.linalg.norm(data["hand_force_n"][tail], axis=1)
        checks.append(check("hand_released_object", float(force.max()) < limits["max_release_force_n"], float(force.max()), limits["max_release_force_n"]))
    if name == "retreat":
        moved = float(data["hand_pose"][-1, 2] - data["hand_pose"][0, 2])
        drift = float(np.linalg.norm(xyz[-1] - xyz[0]))
        checks += [check("hand_withdrew", moved > 0.05, moved), check("object_not_dragged", drift < limits["max_slip_m"], drift)]
    if name == "final_settle" and len(samples) > 2:
        velocity = np.diff(xyz, axis=0) / np.diff(data["time_s"])[:, None]
        speed = float(np.linalg.norm(velocity[tail], axis=1).max())
        checks.append(check("object_settled", speed < limits["max_settle_speed_mps"], speed, limits["max_settle_speed_mps"]))
    return checks


def run_task(bridge, recorder, record, output, limits, check, *, no_grip=False):
    """Execute the plan through bounded motors; stop at the first failed phase."""
    out = Path(output)
    sim, m = bridge.sim, bridge.source
    definition = json.loads((out / "task/task_definition.json").read_text())
    with np.load(out / "task/task_plan.npz", allow_pickle=False) as archive:
        plan = {k: archive[k] for k in archive.files}
    open_reference = plan["targets"][0]
    phases = list(definition["phases"])
    reports = {name: make_report(run=out.name, profile="backend_eval", stage="grasp", step=name, status="blocked",
                                next_action="await the preceding phase") for name in phases}
    for name, report in reports.items():
        atomic_json(out / f"phases/{name}.json", report)
    samples, commands, states, actions, replay_positions, replay_errors = [], [], [], [], [], []
    cpu = mujoco.MjData(m)
    cpu.qpos[:] = bridge.initial_qpos
    mujoco.mj_forward(m, cpu)
    obj = m.body("task_cube").id
    groups = shape_groups(bridge)
    hz = float(sim.request.physics.control_hz)
    recorder.begin(sim.episode, sim)
    current, begin, phase_started = None, 0, time.monotonic()
    last_progress = time.monotonic()
    completed = []

    def close_phase():
        checks = phase_checks(current, samples[begin:], definition, limits, check)
        report = reports[current]
        finish_report(report, checks=checks, elapsed_s=time.monotonic() - phase_started,
                      artifacts=[str(out / "grasp_trajectory.npz")])
        atomic_json(out / f"phases/{current}.json", report)
        print_report(report, out / f"phases/{current}.json")
        completed.append(current)
        return report["status"] == "passed"

    try:
        for i in range(round(float(plan["times"][-1]) * hz)):
            t = (i + 1) / hz
            goal, phase = plan_at(plan, t)
            if phase != current:
                if current is not None and not close_phase():
                    break
                current, begin, phase_started = phase, len(samples), time.monotonic()
                reports[current]["status"] = "running"
                reports[current]["next_action"] = None
                atomic_json(out / f"phases/{current}.json", reports[current])
            if no_grip:
                for j, name in enumerate(bridge.joint_names):
                    if name.startswith("robot_right_right_"):
                        goal[j] = open_reference[j]
            sim.step(goal, mode="joint_reference")
            if not sim.state_is_finite():
                np.savez_compressed(out / "diverged_state.npz", joint_q=sim.state.joint_q.numpy(),
                                    joint_qd=sim.state.joint_qd.numpy(), body_q=sim.state.body_q.numpy(),
                                    actuator_ctrl=sim.control.mujoco.ctrl.numpy(), goal=goal,
                                    mjw_qpos=sim.pipeline.solver.mjw_data.qpos.numpy(),
                                    mjw_qvel=sim.pipeline.solver.mjw_data.qvel.numpy())
                raise RuntimeError(f"non-finite state during {phase}")
            cpu.ctrl[:] = bridge.position_controls(goal, qpos=cpu.qpos, qvel=cpu.qvel)
            for _ in range(sim.request.physics.substeps):
                mujoco.mj_step(m, cpu)
            sample = contact_sample(bridge, groups, t, phase, goal)
            samples.append(sample)
            q, _ = bridge.joint_state()
            actions.append(goal.copy()); states.append(q.copy())
            commands.append(sim.control.mujoco.ctrl.numpy()[bridge.ctrl_indices].copy())
            replay_positions.append(cpu.xpos[obj].copy())
            replay_errors.append(float(np.linalg.norm(cpu.xpos[obj] - sample["object_pose"][:3])))
            record(goal)
            if time.monotonic() - last_progress > 15:
                print(f"[grasp] {phase}, simulated {t:.2f}/{plan['times'][-1]:.2f}s; object z={sample['object_pose'][2]:.3f}m", flush=True)
                last_progress = time.monotonic()
        else:
            close_phase()
    except BaseException as exc:
        if current:
            finish_report(reports[current], failure=f"{type(exc).__name__}: {exc}")
            atomic_json(out / f"phases/{current}.json", reports[current])
        raise
    finally:
        if samples:
            tape = {key: np.asarray([s[key] for s in samples]) for key in samples[0]}
            np.savez_compressed(out / "grasp_trajectory.npz", **tape, targets=actions, qpos=states,
                                actuator_ctrl=commands, cpu_object_position=replay_positions, cpu_object_error_m=replay_errors,
                                joint_names=bridge.joint_names, actuator_names=bridge.actuator_names)

    checks = [check(f"phase_{name}", reports[name]["status"] == "passed", reports[name]["status"],
                    evidence=str(out / f"phases/{name}.json")) for name in phases]
    negative_rejected = None
    if no_grip:
        negative_rejected = (reports["close"]["status"] == "failed" and
                             all(reports[name]["status"] == "blocked" for name in phases[phases.index("close") + 1:]))
        checks.append(check("negative_control_rejected", negative_rejected,
                            {"enabled": True, "close_phase": reports["close"]["status"],
                             "downstream": {name: reports[name]["status"] for name in phases[phases.index("close") + 1:]}}))
    else:
        checks.append({"id": "negative_control_rejected", "status": "skipped", "required": False,
                       "observed": {"enabled": False}, "evidence": "run with --no-grip to evaluate the negative control"})
    free = m.joint("task_cube_free").id
    bound = any(int(m.eq_type[e]) in (int(mujoco.mjtEq.mjEQ_CONNECT), int(mujoco.mjtEq.mjEQ_WELD)) and
                obj in (int(m.eq_obj1id[e]), int(m.eq_obj2id[e])) for e in range(m.neq))
    checks.append(check("unactuated_free_object", m.jnt_type[free] == mujoco.mjtJoint.mjJNT_FREE and
                        m.body_mocapid[obj] < 0 and free not in m.actuator_trnid[:, 0] and not bound))
    max_replay = max(replay_errors, default=float("inf"))
    checks.append(check("independent_object_replay", max_replay <= limits["max_cpu_object_error_m"],
                        max_replay, limits["max_cpu_object_error_m"]))
    metrics = {"completed_phases": completed, "all_phases_completed": all(reports[n]["status"] == "passed" for n in phases),
               "no_grip_negative_control": no_grip, "negative_control_rejected": negative_rejected,
               "recorded_frames": len(samples),
               "max_object_lift_m": max(float(s["object_pose"][2]) - 0.025 for s in samples),
               "max_cpu_object_error_m": max_replay,
               "max_object_penetration_m": max(float(s["penetration_m"]) for s in samples)}
    atomic_json(out / "grasp_metrics.json", metrics)
    return checks, metrics
