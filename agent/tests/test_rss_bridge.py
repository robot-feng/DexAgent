"""Recorded evidence and Blender launch failures must remain visible."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from run_robosimstudio import validate_tape
from stage4_robot_data_generation.stage4.render_launch import render_command


class BridgeTests(unittest.TestCase):
    def test_truncated_or_subsampled_cloth_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            np.savez(root / "steps.npz", sim_time=[0.0, 0.1, 0.2], action=np.zeros((3, 2)))
            np.savez(root / "capture.npz", step=[0, 1, 2, 3])
            np.savez(root / "privileged.npz", body_q=np.zeros((2, 1, 7)), particle_q=np.zeros((3, 4, 3)))
            checks, _, _ = validate_tape(root, particle_count=8)
            failed = {c["id"] for c in checks if c["status"] == "failed"}
            self.assertIn("recorded_frame_counts", failed)
            self.assertIn("complete_cloth_state", failed)

    def test_nonmonotonic_time_and_nan_state_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            np.savez(root / "steps.npz", sim_time=[0.0, 0.0], action=np.zeros((2, 2)))
            np.savez(root / "capture.npz", step=[0, 1, 2])
            q = np.zeros((2, 1, 7)); q[1, 0, 0] = np.nan
            np.savez(root / "privileged.npz", body_q=q)
            checks, _, _ = validate_tape(root, particle_count=0)
            failed = {c["id"] for c in checks if c["status"] == "failed"}
            self.assertIn("recorded_time", failed)
            self.assertIn("recorded_finite", failed)

    def test_missing_control_steps_in_capture_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            np.savez(root / "steps.npz", sim_time=[0.1, 0.2], action=np.zeros((2, 2)),
                     joint_q=np.zeros((2, 2)), joint_qd=np.zeros((2, 2)))
            np.savez(root / "privileged.npz", body_q=np.zeros((2, 1, 7)))
            np.savez(root / "capture.npz", step=[0, 2, 3])
            checks, _, _ = validate_tape(root, particle_count=0)
            failed = {c["id"] for c in checks if c["status"] == "failed"}
            self.assertEqual(failed, {"complete_control_capture"})

    def test_explicit_missing_blender_fails(self):
        with patch.dict(os.environ, {"DEXAGENT_BLENDER": "/missing/blender"}):
            with self.assertRaises(FileNotFoundError):
                render_command("/tmp/view", "0/1")

    def test_blender_arguments_preserve_paths_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="blender test ") as tmp:
            executable = Path(tmp) / "blender"
            executable.touch(); executable.chmod(0o700)
            with patch.dict(os.environ, {"DEXAGENT_BLENDER": str(executable)}):
                cmd = render_command(Path(tmp) / "a view", "1/3")
            self.assertEqual(cmd[0], str(executable))
            self.assertEqual(cmd[-3:], ["--", str(Path(tmp) / "a view"), "1/3"])
            self.assertEqual(cmd[cmd.index("--python-exit-code") + 1], "1")


if __name__ == "__main__":
    unittest.main()
