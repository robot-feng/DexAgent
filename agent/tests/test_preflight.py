"""Preflight must stop before model work when inputs or provider settings are invalid."""
import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2

from agent.preflight import preflight
from agent.run_reports import sha256_file


class _Video:
    def __init__(self, readable=True, decode=True, raise_on_read=False):
        self.readable = readable
        self.decode = decode
        self.raise_on_read = raise_on_read
        self.release_count = 0

    def isOpened(self):
        return self.readable

    def get(self, prop):
        values = {cv2.CAP_PROP_FRAME_COUNT: 12, cv2.CAP_PROP_FRAME_WIDTH: 64,
                  cv2.CAP_PROP_FRAME_HEIGHT: 48, cv2.CAP_PROP_FPS: 24}
        return values.get(prop, 0)

    def read(self):
        if self.raise_on_read:
            raise RuntimeError("fixture decoder failure")
        frame = SimpleNamespace(shape=(48, 64, 3)) if self.decode else None
        return self.decode, frame

    def release(self):
        self.release_count += 1


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dexagent_preflight_")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.config = root / "config.yaml"
        self.config.write_text("fixture: true\n")
        self.video = root / "video.mp4"
        self.video.write_bytes(b"fixture video bytes")
        self.model_dir = root / "sam3-litetext"
        self.model_dir.mkdir()
        (self.model_dir / "model.safetensors").write_bytes(b"fixture model weights")
        self.cfg = {
            "_config_path": str(self.config),
            "run": {"name": "fixture", "output_dir": str(root / "runs")},
            "agent": {"provider": "external", "transport_dir": str(root / "transport")},
            "models": {"hf_home": str(root / "hf"), "segmentation_backend": "sam3-litetext-s0",
                       "sam3_litetext_dir": str(self.model_dir)},
            "inputs": {"video": str(self.video)},
            "environments": {"pipeline": sys.executable, "sam3": sys.executable},
        }
        self.info = {"python": sys.executable, "versions": {k: "1.0" for k in
                      ("torch", "mujoco", "transformers", "accelerate", "opencv-python", "numpy", "PyYAML",
                       "bpy", "scipy", "trimesh", "diffusers", "safetensors")},
                     "cuda": {"available": True, "devices": ["fixture GPU"]}}

    def _run(self, *, readable=True, decode=True, stages=None, capture=None):
        capture = capture or _Video(readable, decode=decode)
        with patch("agent.preflight._under_data", return_value=True), \
             patch("agent.preflight._probe_python", return_value=self.info), \
             patch("agent.preflight.shutil.which", return_value="/usr/bin/nvidia-smi"), \
             patch("agent.preflight.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="0, GPU, 8 GiB, 16 GiB", stderr="")), \
             patch("cv2.VideoCapture", return_value=capture):
            return preflight(self.cfg, stages or ["stage3"], "pipeline_eval")

    def test_valid_environment_and_video_pass(self):
        report = self._run()
        self.assertEqual(report["status"], "passed")
        self.assertEqual(next(c for c in report["checks"] if c["id"] == "input_video_readable")["status"], "passed")
        self.assertEqual(report["metrics"]["video"]["frames"], 12)
        self.assertTrue(report["metrics"]["video"]["first_frame_decoded"])

    def test_video_must_decode_first_frame_and_release_capture(self):
        capture = _Video(decode=False)
        report = self._run(capture=capture)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(capture.release_count, 1)

    def test_video_capture_released_when_decode_raises(self):
        capture = _Video(raise_on_read=True)
        report = self._run(capture=capture)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(capture.release_count, 1)

    def test_effective_config_identity_is_reported(self):
        report = self._run()
        config = report["inputs"]["config"]
        source_sha256 = config["sha256"]
        effective_sha256 = config["effective_sha256"]
        self.assertEqual(source_sha256, sha256_file(self.config))
        self.assertEqual(report["config_sha256"], effective_sha256)
        self.assertNotIn("_config_path", config["effective_config"])

        self.cfg["run"]["name"] = "different effective config"
        changed = self._run()["inputs"]["config"]
        self.assertEqual(changed["sha256"], source_sha256)
        self.assertNotEqual(changed["effective_sha256"], effective_sha256)

    def test_shared_interpreter_is_probed_once_for_each_role(self):
        self.info["versions"]["bpy"] = None
        probe = Mock(return_value=self.info)
        capture = _Video()
        with patch("agent.preflight._under_data", return_value=True), \
             patch("agent.preflight._probe_python", new=probe), \
             patch("agent.preflight.shutil.which", return_value="/usr/bin/nvidia-smi"), \
             patch("agent.preflight.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="GPU", stderr="")), \
             patch("cv2.VideoCapture", return_value=capture):
            report = preflight(self.cfg, ["stage3", "stage4"], "pipeline_eval")
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(set(report["metrics"]["interpreters"]), {"pipeline", "sam3", "stage4"})
        role_checks = {c["id"]: c for c in report["checks"]}
        self.assertEqual(role_checks["pipeline_environment"]["status"], "passed")
        self.assertEqual(role_checks["sam3_environment"]["status"], "passed")
        self.assertEqual(role_checks["stage4_environment"]["status"], "failed")
        self.assertIn("bpy", role_checks["stage4_environment"]["evidence"])

    def test_api_provider_fails_closed(self):
        self.cfg["agent"]["provider"] = "anthropic"
        report = self._run()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(next(c for c in report["checks"] if c["id"] == "no_model_api")["status"], "failed")

    def test_unreadable_video_fails_closed(self):
        report = self._run(readable=False)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(next(c for c in report["checks"] if c["id"] == "input_video_readable")["status"], "failed")

    def test_missing_configured_interpreter_fails_closed(self):
        self.cfg["environments"]["pipeline"] = "/missing/python"
        report = self._run()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(next(c for c in report["checks"] if c["id"] == "pipeline_python")["status"], "failed")

    def test_stage4_requires_and_hashes_the_selected_lama_checkpoint(self):
        checkpoint = Path(self.tmp.name) / "lama.pt"
        checkpoint.write_bytes(b"fixture torchscript")
        self.cfg["models"]["lama_checkpoint"] = str(checkpoint)
        self.cfg["stage4"] = {"inpaint_backend": "lama"}
        with patch("agent.preflight._under_data", return_value=True), \
             patch("agent.preflight._probe_python", return_value=self.info), \
             patch("agent.preflight.shutil.which", return_value="/usr/bin/nvidia-smi"), \
             patch("agent.preflight.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="GPU", stderr="")), \
             patch("cv2.VideoCapture", return_value=_Video()):
            report = preflight(self.cfg, ["stage4"], "dataset_release")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["model_identity"]["inpainting"]["backend"], "lama")
        self.assertEqual(len(report["model_identity"]["inpainting"]["sha256"]), 64)

    def test_stage4_checks_renderer_and_physics_packages(self):
        checkpoint = Path(self.tmp.name) / "lama.pt"
        checkpoint.write_bytes(b"fixture torchscript")
        self.cfg["models"]["lama_checkpoint"] = str(checkpoint)
        self.cfg["stage4"] = {"inpaint_backend": "lama"}
        self.info["versions"]["bpy"] = None
        report = self._run(stages=["stage4"])
        self.assertEqual(report["status"], "failed")
        environment = next(c for c in report["checks"] if c["id"] == "stage4_environment")
        self.assertIn("bpy", environment["evidence"])


if __name__ == "__main__":
    unittest.main()
