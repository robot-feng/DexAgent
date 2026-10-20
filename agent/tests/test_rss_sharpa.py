"""Named control errors must be rejected before commands reach the GPU."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mujoco
import numpy as np

from agent.rss_sharpa import (JointReferenceControl, SharpaBridge, expand_mjcf_solref_shorthand,
                              inertia_tensors, named_indices)
from tool_library.common.rigid_robot import compile_rigid


class Array:
    def __init__(self, values):
        self.values = np.asarray(values).copy()

    def numpy(self):
        return self.values.copy()

    def assign(self, values):
        self.values[:] = values


class SharpaControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rig = compile_rigid(Path(__file__).resolve().parents[2] / "setup/scene.xml")

    def setUp(self):
        self.bridge = b = object.__new__(SharpaBridge)
        b.configure_source(self.rig)
        m = b.source
        b.actuator_names = [m.actuator(i).name for i in range(m.nu)]
        b.joint_names = [m.joint(i).name for i in range(m.njnt)]
        b.q_indices = b.v_indices = np.arange(m.nq)
        # An import may reorder the actuator vector independently of joints.
        b.ctrl_indices = np.arange(m.nu)[::-1]
        b.reference = mujoco.MjData(m)
        self.home = m.key_qpos[0].copy()
        self.state = SimpleNamespace(joint_q=Array(self.home), joint_qd=Array(np.zeros(m.nv)))
        b.sim = SimpleNamespace(state=self.state, model=SimpleNamespace(joint_q=Array(self.home)))

    def test_name_resolution_rejects_ambiguity(self):
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            named_indices(["a/joint", "b/joint"])
        self.assertEqual(named_indices(["a/fixed", "b/fixed", "c/actual"], {"actual"}), {"actual": 2})

    def test_mjcf_solref_shorthand_expands_to_mujoco_compiled_defaults(self):
        xml, changed = expand_mjcf_solref_shorthand(
            '<mujoco><default><geom solref="0.02"/></default>'
            '<worldbody><geom name="floor" solref="0.005 0.8"/></worldbody></mujoco>')
        self.assertEqual(changed, 1)
        self.assertIn('solref="0.02 1"', xml)
        self.assertIn('solref="0.005 0.8"', xml)

    def test_missing_unknown_or_extra_names_are_rejected(self):
        controls = dict(zip(self.bridge.actuator_names, np.zeros(56)))
        del controls[self.bridge.actuator_names[0]]
        controls["unknown_joint"] = 0
        with self.assertRaisesRegex(ValueError, "every named actuator"):
            self.bridge.validate_controls(controls)

    def test_wrong_shape_and_nonfinite_controls_are_rejected(self):
        for values in (np.zeros(55), np.zeros((56, 1)), np.full(56, np.nan), np.full(56, np.inf)):
            with self.subTest(shape=values.shape), self.assertRaises(ValueError):
                self.bridge.validate_controls(values)

    def test_source_torque_bounds_are_enforced(self):
        values = self.bridge.position_controls(self.home)
        motor = self.bridge.actuator_names.index("robot_left_arm_dof_joint1_motor")
        values[motor] = 29
        with self.assertRaisesRegex(ValueError, "control range"):
            self.bridge.validate_controls(values)

    def test_named_actuators_map_to_import_order(self):
        values = self.bridge.position_controls(self.home)
        control = SimpleNamespace(mujoco=SimpleNamespace(ctrl=Array(np.zeros(56))))
        mapping = dict(zip(self.bridge.actuator_names, values, strict=True))
        self.bridge.apply(mapping, control, self.state)
        np.testing.assert_allclose(control.mujoco.ctrl.numpy()[self.bridge.ctrl_indices], values)

    def test_pd_does_not_convert_torque_motors_to_position_servos(self):
        values = self.bridge.position_controls(self.home)
        m = self.bridge.source
        d = mujoco.MjData(m)
        d.qpos[:] = self.home
        mujoco.mj_forward(m, d)
        for a, name in enumerate(self.bridge.actuator_names):
            j = int(m.actuator_trnid[a, 0])
            if name.endswith("_motor"):
                self.assertAlmostEqual(values[a], np.clip(d.qfrc_bias[j], *m.actuator_ctrlrange[a]))
            else:
                self.assertEqual(values[a], self.home[j])

    def test_joint_targets_cannot_cross_hardware_limits(self):
        bad = self.home.copy()
        bad[0] = self.bridge.source.jnt_range[0, 1] + 0.1
        with self.assertRaisesRegex(ValueError, "source limits"):
            self.bridge.position_controls(bad)

    def test_hold_clips_soft_stop_overshoot_without_teleporting_state(self):
        self.state.joint_q.values[0] = self.bridge.source.jnt_range[0, 0] - 1e-4
        before = self.state.joint_q.numpy()
        commands = self.bridge.hold_action(self.state)
        self.bridge.validate_controls(commands)
        np.testing.assert_array_equal(self.state.joint_q.numpy(), before)

    def test_reference_action_preserves_targets_and_torque_bounds(self):
        control = SimpleNamespace(mujoco=SimpleNamespace(ctrl=Array(np.zeros(56))), joint_target_q=Array(np.zeros(56)))
        self.bridge.sim.model.joint_coord_count = 56
        space = JointReferenceControl(self.bridge)
        space.apply(self.home, control, self.state)
        np.testing.assert_array_equal(control.joint_target_q.numpy(), self.home.astype(np.float32))
        values = control.mujoco.ctrl.numpy()[self.bridge.ctrl_indices]
        self.bridge.validate_controls(values)
        self.assertEqual(space.action_dim, 56)

    def test_feedback_replay_uses_supplied_state(self):
        changed = self.home.copy()
        changed[0] += 0.01
        controls = self.bridge.position_controls(self.home, qpos=changed, qvel=np.zeros(56))
        self.assertGreater(float(abs(controls - self.bridge.position_controls(self.home)).max()), 0.01)

    def test_inertia_tensors_preserve_principal_moments(self):
        tensors = inertia_tensors(self.bridge.source)
        for j, tensor in enumerate(tensors):
            np.testing.assert_allclose(np.linalg.eigvalsh(tensor), np.sort(self.bridge.source.body_inertia[j]), atol=1e-12)

    def test_camera_converts_opengl_to_vision_axes(self):
        self.bridge.home = self.home
        camera = self.bridge.camera()
        m = self.bridge.source
        d = mujoco.MjData(m)
        d.qpos[:] = self.home
        mujoco.mj_forward(m, d)
        c = m.camera("ego").id
        expected = d.cam_xmat[c].reshape(3, 3) @ np.diag([1, -1, -1])
        np.testing.assert_allclose(camera.world_matrix[:3, :3], expected, atol=1e-12)
        np.testing.assert_allclose(camera.world_matrix[:3, 3], d.cam_xpos[c], atol=1e-12)

    def test_translated_cameras_preserve_orientation_and_relative_projection(self):
        baseline = {name: self.bridge.camera(name) for name in ('ego', 'workspace')}
        self.bridge.rig_translation = (.4, -.7, 1.)
        for name, camera in baseline.items():
            translated = self.bridge.camera(name)
            np.testing.assert_allclose(np.asarray(translated.eye) - camera.eye, self.bridge.rig_translation)
            np.testing.assert_allclose(np.asarray(translated.look_at) - camera.look_at, self.bridge.rig_translation)
            np.testing.assert_allclose(translated.world_matrix[:3, :3], camera.world_matrix[:3, :3], atol=1e-12)

    def test_world_pose_readback_does_not_mutate_recorded_arrays(self):
        self.bridge.rig_translation = (.4, -.7, 1.)
        poses = np.array([[.5, -.5, 1.3, 0, 0, 0, 1], [.7, -.1, 1.8, 0, 0, 1, 0]])
        before = poses.copy()
        source = self.bridge.world_pose_to_rig(poses)
        np.testing.assert_allclose(source[:, :3], [[.1, .2, .3], [.3, .6, .8]])
        np.testing.assert_array_equal(source[:, 3:], before[:, 3:])
        np.testing.assert_array_equal(poses, before)

    def test_invalid_translation_is_rejected_before_source_loading(self):
        for translation in ([0, 0], [[0, 0, 0]], [0, np.nan, 0], [0, 0, np.inf]):
            with self.subTest(translation=translation), self.assertRaisesRegex(ValueError, "three finite metre"):
                SharpaBridge("/unused", source="/missing.xml", rig_translation=translation)

    def test_missing_robot_is_rejected_before_import(self):
        fake = SimpleNamespace(model=SimpleNamespace(nq=7, nv=6, nu=0, njnt=0))
        with self.assertRaisesRegex(ValueError, "56 physical robot"):
            self.bridge.configure_source(fake)


if __name__ == "__main__":
    unittest.main()
