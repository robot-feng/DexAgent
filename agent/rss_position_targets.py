"""Route RSS position targets to audited, imported native position actuators."""
from __future__ import annotations

import mujoco
import newton
import numpy as np


class CpuPositionTargetOutlet:
    """A narrow control outlet for unit-gear scalar position servos.

    Imported MJCF servos use CTRL_DIRECT. RSS IK/gripper controllers instead
    write joint_target_q. Copy only explicitly selected actuator channels at
    pre_solve; do not replace their source gains, force caps or constraints.
    Labels are exact model.mujoco.actuator_label values, not suffix guesses.
    Motor, velocity, tendon, coupled and nonzero-reference servos are rejected.
    This outlet is single-world CPU only and must be rebuilt with the model.
    """

    def __init__(self, model, solver, actuator_labels):
        if str(model.device) != "cpu" or not solver.use_mujoco_cpu:
            raise ValueError("position outlet requires the native CPU solver")
        if solver.model is not model:
            raise ValueError("position outlet model must belong to the selected solver")
        self.model, self.solver = model, solver
        self.native_model = m = solver.mj_model
        joints = solver.mjc_jnt_to_newton_jnt.numpy()
        dofs = solver.mjc_jnt_to_newton_dof.numpy()
        if joints.shape != (1, m.njnt):
            raise ValueError("position outlet requires exactly one native world")
        labels = list(model.mujoco.actuator_label)
        requested = list(actuator_labels)
        if not requested or len(set(requested)) != len(requested):
            raise ValueError("supply distinct, nonempty actuator labels")
        sources = solver.mjc_actuator_ctrl_source.numpy()
        controls = solver.mjc_actuator_to_newton_idx.numpy()
        starts = model.joint_target_q_start.numpy()
        types = model.joint_type.numpy()
        imported_dofs = model.mujoco.actuator_trnid.numpy()[:, 0]
        mapping = []
        for label in requested:
            if labels.count(label) != 1:
                raise ValueError(f"actuator label must match exactly once: {label}")
            slot = labels.index(label)
            candidates = np.flatnonzero((sources == 1) & (controls == slot))
            if len(candidates) != 1:
                raise ValueError(f"actuator is not uniquely CTRL_DIRECT: {label}")
            a = int(candidates[0])
            if m.actuator_trntype[a] != int(mujoco.mjtTrn.mjTRN_JOINT):
                raise ValueError(f"actuator must target a scalar joint: {label}")
            j = int(m.actuator_trnid[a, 0])
            nj = int(joints[0, j])
            if (nj < 0 or types[nj] not in (newton.JointType.REVOLUTE, newton.JointType.PRISMATIC)
                    or m.jnt_type[j] not in (int(mujoco.mjtJoint.mjJNT_HINGE), int(mujoco.mjtJoint.mjJNT_SLIDE))
                    or imported_dofs[slot] != dofs[0, j]):
                raise ValueError(f"actuator target is not a mapped scalar Newton joint: {label}")
            shared = (m.actuator_trntype == int(mujoco.mjtTrn.mjTRN_JOINT)) & (m.actuator_trnid[:, 0] == j)
            if np.count_nonzero(shared) != 1:
                raise ValueError(f"multiple actuators target the same joint: {label}")
            kp = float(m.actuator_gainprm[a, 0])
            bias = m.actuator_biasprm[a]
            if not (
                m.actuator_dyntype[a] == int(mujoco.mjtDyn.mjDYN_NONE)
                and m.actuator_gaintype[a] == int(mujoco.mjtGain.mjGAIN_FIXED)
                and m.actuator_biastype[a] == int(mujoco.mjtBias.mjBIAS_AFFINE)
                and np.array_equal(m.actuator_gear[a], [1, 0, 0, 0, 0, 0])
                and np.isfinite(kp) and kp > 0
                and np.all(m.actuator_gainprm[a, 1:] == 0)
                and np.isfinite(bias).all() and bias[0] == 0 and bias[2] <= 0
                and np.isclose(bias[1], -kp, rtol=1e-7, atol=0)
                and np.all(bias[3:] == 0)
                and m.qpos0[m.jnt_qposadr[j]] == 0
            ):
                raise ValueError(f"actuator is not an unshifted unit-gear position servo: {label}")
            mapping.append({"actuator_label": label, "native_actuator": a,
                            "native_joint": j, "newton_joint": nj,
                            "target_slot": int(starts[nj]), "ctrl_slot": slot,
                            "unit": "rad" if types[nj] == newton.JointType.REVOLUTE else "m"})
        self.mapping = tuple(mapping)
        self.target_slots = np.asarray([row["target_slot"] for row in mapping])
        self.ctrl_slots = np.asarray([row["ctrl_slot"] for row in mapping])
        self.native_actuators = np.asarray([row["native_actuator"] for row in mapping])

    def apply(self, control):
        """Copy validated commands; other actuator channels remain untouched."""
        if self.solver.mj_model is not self.native_model:
            raise RuntimeError("native model was replaced; rebuild the position outlet")
        targets = control.joint_target_q.numpy()[self.target_slots]
        if not np.isfinite(targets).all():
            raise ValueError("position targets must be finite")
        m, a = self.native_model, self.native_actuators
        bounds = m.actuator_ctrlrange[a]
        limited = m.actuator_ctrllimited[a].astype(bool)
        if np.any(limited & ((targets < bounds[:, 0]) | (targets > bounds[:, 1]))):
            raise ValueError("position target exceeds source actuator control range")
        commands = control.mujoco.ctrl.numpy().copy()
        commands[self.ctrl_slots] = targets
        control.mujoco.ctrl.assign(commands)
