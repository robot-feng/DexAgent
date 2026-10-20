"""Retarget fingertip motion onto a robot hand: per-finger IK, per frame, smoothed.

    from tool_library.skills.retarget_hand_motion import retarget_hand_motion
    res = retarget_hand_motion("hand_scene.xml", hand_root="robot_right_right_hand_C_MC",
            fingertips={"thumb": "robot_right_right_thumb_DP", "index": "robot_right_right_index_DP", ...},
            targets={"thumb": tips_T_3, "index": tips_T_3, ...},   # in the hand-root frame
            scale=1.0, fps=30)
    res["qpos"], res["joint_names"], res["residual_mm"]

Human fingertip positions (from a hand mesh or keypoints, expressed in the
wrist frame) are the part of a demonstration that transfers to a robot hand
of a different size: scale them by the ratio of hand lengths, then solve
each finger's joints so its tip reaches the target, warm-started from the
previous frame, clamped to the joint limits, and smoothed. Fingertip bodies
can carry a local tip point. Fingers whose chain cannot reach a target
report the residual instead of failing.
"""
from __future__ import annotations

TOOL = {
    "name": "retarget_hand_motion",
    "title": "Retarget hand motion",
    "type": "skill",
    "category": "Trajectory & Execution",
    "summary": "Fingertip targets in the hand frame -> robot finger joint trajectory by per-finger limit-aware IK with warm starts and smoothing.",
    "description": (
        "For each named finger it builds the chain of joints between the hand root body and "
        "the fingertip body, converts the per-frame target (hand-root frame, optionally scaled "
        "about the root and offset) to world using the root's pose, and solves position-only "
        "damped least squares IK with the previous frame as the start and a pull toward the "
        "open posture in the null space. Joint values are clamped to the model's limits, "
        "smoothed with a zero-phase filter and rate-limited to max_rate_rad_s. Optional "
        "timestamps_s supplies measured sample times; irregular times disable the equal-sample "
        "smoothing. Residuals describe the final returned trajectory after rate limiting. Returns the "
        "finger joint trajectory in qpos order, the joint names, per-finger residuals in "
        "millimetres and the frames where a finger could not reach. Works with any hand "
        "whose fingers are hinge/slide chains under one root body; the wrist pose itself is "
        "handled by the arm (solve_arm_ik / attach_arms)."
    ),
    "inputs": [
        {"name": "scene", "type": "str | mujoco.MjModel", "description": "Scene with the robot hand."},
        {"name": "hand_root", "type": "str", "description": "Root body of the hand; targets are in its frame."},
        {"name": "fingertips", "type": "dict finger -> body name", "description": "Fingertip bodies."},
        {"name": "targets", "type": "dict finger -> ndarray [T, 3]", "description": "Tip positions in the hand-root frame."},
        {"name": "tip_points", "type": "dict finger -> [3] | None", "description": "Tip point in each fingertip body frame; default the body origin."},
        {"name": "scale, offset", "type": "float, [3]", "description": "Applied to targets about the root: p' = scale * p + offset."},
        {"name": "fps, max_rate_rad_s, smooth_window", "type": "float, float, int", "description": "Defaults 30, 6.0, 7."},
        {"name": "timestamps_s", "type": "ndarray [T] | None", "description": "Finite, strictly increasing sample times in seconds; overrides fps for rate limits. Irregular sampling disables equal-sample smoothing."},
        {"name": "qpos_base", "type": "ndarray | None", "description": "Scene state the hand root pose is read from; default qpos0."},
    ],
    "outputs": [
        {"name": "result", "type": "dict", "description": "qpos [T, n_finger_joints], qadr, joint_names, final-state residual_mm {finger: max_mm, mean_mm}, unreachable {finger: frames}, ok, timestamps_s, time_source and smoothing. fps is null when measured times are supplied."},
    ],
    "requirements": ["mujoco>=3.2", "numpy", "scipy"],
    "simulators": ["MuJoCo"],
    "tags": ["retargeting", "hand", "fingers", "ik"],
    "used_in": ["Place Cup in Bowl", "Multi-Cup Grasping", "Place Toy in Drawer", "Giftbox Packing", "Knot the Rope", "Battery Insertion", "Drawing and Wiping", "Computer Installation", "Open the Bottle", "Scissor Cutting", "Biology Experiment"],
    "status": "tested",
    "entrypoint": "retarget_hand_motion",
}

import numpy as np

from tool_library.common import trajectory as TJ


def retarget_hand_motion(scene, hand_root, fingertips, targets, tip_points=None, scale=1.0, offset=(0, 0, 0),
                         fps=30.0, max_rate_rad_s=6.0, smooth_window=7, qpos_base=None, reach_tol_m=0.004,
                         timestamps_s=None):
    if not targets:
        raise ValueError("targets must contain at least one fingertip track")
    T = len(next(iter(targets.values())))
    if T == 0:
        raise ValueError("targets must contain at least one sample")
    if not np.isfinite(max_rate_rad_s) or max_rate_rad_s < 0:
        raise ValueError("max_rate_rad_s must be finite and nonnegative")
    if timestamps_s is None:
        if not np.isfinite(fps) or fps <= 0:
            raise ValueError("fps must be finite and positive without timestamps_s")
        times = np.arange(T, dtype=float) / fps
        dt = np.full(T - 1, 1.0 / fps)
        uniform = True
    else:
        times = np.asarray(timestamps_s)
        if (times.shape != (T,) or not np.issubdtype(times.dtype, np.number)
                or np.iscomplexobj(times) or not np.isfinite(times).all()):
            raise ValueError("timestamps_s must be a finite numeric vector with one time per sample")
        times = times.astype(float)
        with np.errstate(over="ignore", invalid="ignore"):
            dt = np.diff(times)
        if not np.isfinite(dt).all() or np.any(dt <= 0):
            raise ValueError("timestamps_s must be strictly increasing with finite intervals")
        uniform = len(dt) < 2 or np.allclose(dt, dt[0], rtol=1e-6, atol=1e-12)
    import mujoco
    model = TJ.load_model(scene)
    root = TJ.body_id(model, hand_root)
    under = TJ.subtree(model, root)
    data = mujoco.MjData(model)
    if qpos_base is not None:
        data.qpos[:] = qpos_base
    base_qpos = data.qpos.copy()
    mujoco.mj_kinematics(model, data)
    p_root, R_root = data.xpos[root].copy(), data.xmat[root].reshape(3, 3).copy()
    chains = {}
    for finger, body in fingertips.items():
        tip = TJ.body_id(model, body)
        joints = [j for j in TJ.chain_joints(model, tip) if int(model.jnt_bodyid[j]) in under and int(model.jnt_bodyid[j]) != root]
        if not joints:
            raise ValueError(f"finger '{finger}': no joints between {hand_root} and {body}")
        chains[finger] = TJ.Chain(model, tip, joints)
    cols = sorted({int(a) for ch in chains.values() for a in ch.qadr})
    Q = np.zeros((T, len(cols)))
    col_of = {a: i for i, a in enumerate(cols)}
    target_world, tip_local = {}, {}
    for finger, ch in chains.items():
        tp = None if not tip_points or finger not in tip_points else np.asarray(tip_points[finger], float)
        tgt = np.asarray(targets[finger], float) * scale + np.asarray(offset, float)
        if tgt.shape != (T, 3) or not np.isfinite(tgt).all():
            raise ValueError(f"targets for '{finger}' must be finite and have shape ({T}, 3)")
        target_world[finger] = tgt @ R_root.T + p_root
        tip_local[finger] = tp
        rest = np.where(ch.limited, np.clip(np.zeros(len(ch.qadr)), ch.lo, ch.hi), 0.0)
        for t in range(T):
            pw = target_world[finger][t]
            e, _ = ch.solve(data, pw, None, point=tp, rest=rest, iters=60, damping=2e-3)
            if e > reach_tol_m:                 # retry from the open posture
                q_prev = data.qpos[ch.qadr].copy()
                data.qpos[ch.qadr] = rest
                e2, _ = ch.solve(data, pw, None, point=tp, rest=rest, iters=60, damping=2e-3)
                if e2 >= e:
                    data.qpos[ch.qadr] = q_prev
                    ch.solve(data, pw, None, point=tp, rest=rest, iters=20, damping=2e-3)
            for a in ch.qadr:
                Q[t, col_of[int(a)]] = data.qpos[a]
    if uniform:
        Q = TJ.savgol(Q, smooth_window, 2)
        smoothing = "savgol" if T > smooth_window else "not_applied_short_track"
    else:
        smoothing = "disabled_nonuniform_timestamps"
    # Each transition has its own physical duration, including the backward pass.
    limits = max_rate_rad_s * dt
    for i in range(1, T):
        Q[i] = np.clip(Q[i], Q[i - 1] - limits[i - 1], Q[i - 1] + limits[i - 1])
    for i in range(T - 2, -1, -1):
        Q[i] = np.clip(Q[i], Q[i + 1] - limits[i], Q[i + 1] + limits[i])
    lo = np.array([model.jnt_range[j][0] for j in range(model.njnt) if int(model.jnt_qposadr[j]) in col_of])
    hi = np.array([model.jnt_range[j][1] for j in range(model.njnt) if int(model.jnt_qposadr[j]) in col_of])
    lim = np.array([bool(model.jnt_limited[j]) for j in range(model.njnt) if int(model.jnt_qposadr[j]) in col_of])
    Q = np.where(lim, np.clip(Q, lo, hi), Q)
    # Smoothing and rate limits can move otherwise reachable IK solutions away
    # from their targets. Evaluate the actual returned states, not the raw IK.
    errors = {finger: np.zeros(T) for finger in chains}
    for t in range(T):
        data.qpos[:] = base_qpos
        data.qpos[cols] = Q[t]
        mujoco.mj_kinematics(model, data)
        for finger, ch in chains.items():
            point, _ = ch.pose(data, tip_local[finger])
            errors[finger][t] = np.linalg.norm(point - target_world[finger][t])
    residual, unreachable = {}, {}
    for finger, err in errors.items():
        residual[finger] = {"max_mm": round(1000 * float(err.max()), 2), "mean_mm": round(1000 * float(err.mean()), 2)}
        bad = np.flatnonzero(err > reach_tol_m)
        if len(bad):
            unreachable[finger] = bad.tolist()
    names = [model.joint(j).name for j in range(model.njnt) if int(model.jnt_qposadr[j]) in col_of]
    return {"qpos": Q, "qadr": cols, "joint_names": names, "residual_mm": residual, "unreachable": unreachable,
            "ok": not unreachable, "fps": fps if timestamps_s is None else None,
            "timestamps_s": times, "time_source": "fps" if timestamps_s is None else "timestamps_s",
            "smoothing": smoothing}
