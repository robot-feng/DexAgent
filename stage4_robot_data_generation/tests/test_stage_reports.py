"""Stage 4 report lifecycle, fail-closed gates, and selected-step recovery."""
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

PKG = Path(__file__).resolve().parents[1]
ROOT = PKG.parent
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT))

import run_stage4 as S4  # noqa: E402


class FakeConfig(dict):
    def __init__(self, root, **values):
        super().__init__(values)
        self.root = Path(root)
        self.out = self.root / "outputs"

    def path(self, key):
        return self.path_of(self.get(key))

    def path_of(self, value):
        path = Path(value)
        return path if path.is_absolute() else self.root / path


class Stage4ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dexagent_stage4_report_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("trajectory.npz", "human.mp4"):
            (self.root / name).write_text("fixture")
        (self.root / "scene.xml").write_text("<mujoco/>")
        self.task = self.root / "stage4.json"
        self.task.write_text("{}")
        self.cfg = FakeConfig(self.root, name="fixture", evaluation_profile="pipeline_eval",
                              scene="scene.xml", trajectory="trajectory.npz", human_video="human.mp4")

    def test_plate_contact_sheet_uses_external_masks_and_resizes_them(self):
        import cv2
        import numpy as np

        root = self.cfg.out / "plate"
        frames, masks = root / "frames", self.root / "external_masks"
        frames.mkdir(parents=True)
        masks.mkdir()
        cv2.imwrite(str(root / "plate.png"), np.zeros((100, 100, 3), np.uint8))
        cv2.imwrite(str(frames / "frame00000.png"), np.zeros((100, 100, 3), np.uint8))
        mask = np.zeros((50, 50), np.uint8)
        mask[:, :25] = 255
        cv2.imwrite(str(masks / "frame00000.png"), mask)
        (root / "meta.json").write_text(json.dumps({"masks_dir": str(masks)}))

        preview = S4._write_plate_contact_sheet(self.cfg)

        self.assertIsNotNone(preview)
        sheet = cv2.imread(str(preview))
        self.assertGreater(int(sheet[50, 25].max()), 80)  # mask overlay is visible
        self.assertLess(float(sheet[50, 75].mean()), 30)  # unmasked part stays black

    def run_steps(self, steps="all", fail_randomize=False, interrupt=None, review=None):
        called = []
        modules = {}
        for name in S4.STEPS:
            module = types.ModuleType("stage4." + name)
            def run(cfg, log, step=name):
                called.append(step)
                if step == interrupt:
                    raise KeyboardInterrupt("fixture interrupt")
            module.run = run
            modules["stage4." + name] = module
        validators = {}
        for name in S4.STEPS:
            def validate(cfg, step=name):
                failed = fail_randomize and step == "randomize"
                artifact = cfg.out / {"randomize": "samples/s1/trajectory.npz", "plate": "plate/plate.png",
                    "export": "render/s1/ego/scene.json", "render": "render/s1/ego/frames/f000.png",
                    "composite": "samples/s1/ego.mp4"}[step]
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text("fixture")
                return ([S4._check(step + "_check", not failed,
                                   evidence="fixture failure" if failed else None)], {}, [str(artifact)])
            validators[name] = validate
        stage4_pkg = sys.modules["stage4"]
        with ExitStack() as stack:
            stack.enter_context(patch.object(S4, "load_config", return_value=self.cfg))
            stack.enter_context(patch.object(S4, "VALIDATORS", validators))
            stack.enter_context(patch.object(S4, "render", side_effect=lambda cfg, log: called.append("render")))
            stack.enter_context(patch.dict(sys.modules, modules))
            for name, module in modules.items():
                stack.enter_context(patch.object(stage4_pkg, name.split(".")[-1], module, create=True))
            args = ["--steps", steps] if not review else review
            stack.enter_context(patch.object(sys, "argv", ["run_stage4.py", str(self.task), *args]))
            try:
                S4.main()
            except SystemExit as exc:
                return called, exc.code
        return called, 0

    def test_full_run_writes_each_step_report(self):
        called, code = self.run_steps()
        final = json.loads((self.cfg.out / "run_report.json").read_text())
        self.assertEqual(code, 0)
        self.assertEqual(called, S4.STEPS)
        self.assertEqual(final["status"], "passed")
        self.assertFalse(final["release_ready"])
        for step in S4.STEPS:
            report = json.loads((self.cfg.out / "reports" / f"{step}.json").read_text())
            self.assertEqual(report["status"], "passed")
            self.assertIn("report_path", report)

    def test_required_failure_blocks_later_steps(self):
        called, code = self.run_steps(fail_randomize=True)
        final = json.loads((self.cfg.out / "run_report.json").read_text())
        self.assertEqual(code, 1)
        self.assertEqual(called, ["randomize"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["steps"]["plate"]["status"], "blocked")

    def test_selected_step_is_resumable_and_run_is_partial(self):
        called, code = self.run_steps(steps="plate")
        final = json.loads((self.cfg.out / "run_report.json").read_text())
        self.assertEqual((code, called), (0, ["plate"]))
        self.assertEqual(final["status"], "partial")
        self.assertEqual(final["steps"]["randomize"]["status"], "skipped")
        self.assertEqual(final["steps"]["plate"]["status"], "passed")

    def final(self):
        return json.loads((self.cfg.out / "run_report.json").read_text())

    def test_resume_preserves_unselected_steps_and_invalidates_descendants(self):
        self.run_steps()
        previous = self.final()
        self.assertEqual(self.run_steps("plate"), (["plate"], 0))
        current = self.final()
        self.assertEqual(current["steps"]["randomize"]["attempt_id"], previous["steps"]["randomize"]["attempt_id"])
        self.assertEqual(current["steps"]["export"]["status"], "stale")
        self.assertEqual(self.run_steps("export,render,composite")[1], 0)
        self.assertEqual(self.final()["status"], "passed")
        self.assertTrue(list((self.cfg.out / "reports/history").glob("*/plate.json")))

    def test_missing_dependency_blocks_execution(self):
        self.assertEqual(self.run_steps("render"), ([], 1))
        self.assertIn("requires current verified", self.final()["failure"])

    def test_configuration_and_input_changes_reject_recovery(self):
        self.run_steps()
        self.cfg["seed"] = 22
        self.assertEqual(self.run_steps("composite"), ([], 1))
        self.run_steps()
        (self.root / "trajectory.npz").write_text("different")
        self.assertEqual(self.run_steps("render"), ([], 1))

    def test_artifact_tamper_rejects_downstream_recovery(self):
        self.run_steps()
        (self.cfg.out / "samples/s1/trajectory.npz").write_text("tampered")
        self.assertEqual(self.run_steps("composite"), ([], 1))

    def test_interrupted_step_finalizes_report(self):
        self.assertEqual(self.run_steps("all", interrupt="plate"), (["randomize", "plate"], 130))
        final = self.final()
        self.assertEqual(final["status"], "interrupted")
        self.assertEqual(final["steps"]["plate"]["status"], "interrupted")
        self.assertEqual(final["steps"]["render"]["status"], "blocked")

    def test_release_requires_explicit_hash_bound_reviews(self):
        self.cfg["evaluation_profile"] = "dataset_release"
        self.run_steps()
        self.assertFalse(self.final()["release_ready"])
        for step in ("plate", "render"):
            report = self.final()["steps"][step]
            args = ["--approve-review", step, "--reviewer", "human fixture",
                    "--review-artifact-sha256", report["artifact_sha256"]]
            self.assertEqual(self.run_steps(review=args), ([], 0))
        self.assertTrue(self.final()["release_ready"])
        self.assertEqual(self.final()["status"], "passed")
        self.run_steps("plate")
        self.assertFalse(self.final()["release_ready"])
        self.assertTrue(self.final()["steps"]["plate"]["profile_review_pending"])

    def test_review_rejects_old_hash(self):
        self.cfg["evaluation_profile"] = "dataset_release"
        self.run_steps()
        self.assertEqual(self.run_steps(review=["--approve-review", "plate", "--reviewer", "human",
                                               "--review-artifact-sha256", "old"]), ([], 1))
        self.assertFalse(self.final()["release_ready"])

    def test_input_asset_content_is_part_of_identity(self):
        (self.root / "mesh.obj").write_text("mesh version 1")
        (self.root / "scene.xml").write_text('<mujoco><asset><mesh file="mesh.obj"/></asset></mujoco>')
        self.run_steps()
        (self.root / "mesh.obj").write_text("mesh version 2")
        self.assertEqual(self.run_steps("render"), ([], 1))

    def test_invocation_id_is_echoed(self):
        with patch.dict(os.environ, {"DEXAGENT_STAGE4_INVOCATION_ID": "pipeline-attempt"}):
            self.run_steps("plate")
        self.assertEqual(self.final()["invocation_id"], "pipeline-attempt")

    def test_bad_config_replaces_previous_success_summary(self):
        self.run_steps()
        with patch.object(S4, "load_config", side_effect=ValueError("bad config")), \
             patch.object(sys, "argv", ["run_stage4.py", str(self.task)]):
            with self.assertRaises(SystemExit) as caught:
                S4.main()
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(self.final()["status"], "failed")

    def test_plate_zero_masks_and_indices_fail(self):
        import cv2
        import numpy as np
        self.cfg["cameras"] = {"width": 16, "height": 12}
        root = self.cfg.out / "plate"
        root.mkdir(parents=True)
        cv2.imwrite(str(root / "plate.png"), np.zeros((12, 16, 3), np.uint8))
        (root / "meta.json").write_text(json.dumps({"indices": [], "n_source_frames": 8}))
        with patch.object(S4, "_write_plate_contact_sheet", return_value=None):
            checks, _, _ = S4._validate_plate(self.cfg)
        by_name = {c["id"]: c["status"] for c in checks}
        self.assertEqual(by_name["mask_frame_count"], "failed")
        self.assertEqual(by_name["mask_nonempty"], "failed")

    def test_renderer_interrupt_terminates_real_child_and_closes_log(self):
        self.cfg["render"] = {"chunks": 1, "parallel": 1}
        sample = self.cfg.out / "samples/s1"
        view = self.cfg.out / "render/s1/ego"
        view.mkdir(parents=True)
        (view / "scene.json").write_text('{"n_frames": 1}')
        frames = view / "frames"
        frames.mkdir()
        (frames / "f000.png").write_text("untrusted old frame")
        launched, handles = [], []
        original_popen = subprocess.Popen
        original_sleep = S4.time.sleep
        interrupted_once = False
        def interrupt_sleep(seconds):
            nonlocal interrupted_once
            if not interrupted_once:
                interrupted_once = True
                raise KeyboardInterrupt("fixture")
            original_sleep(seconds)
        def spawn(*args, **kwargs):
            handles.append(kwargs["stdout"])
            process = original_popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                     **kwargs)
            launched.append(process)
            return process
        with patch("stage4.export.current_samples", return_value=[sample]), \
             patch("stage4.export.stage_views", return_value=["ego"]), \
             patch.object(S4.subprocess, "Popen", side_effect=spawn), \
             patch.object(S4.time, "sleep", side_effect=interrupt_sleep):
            with self.assertRaises(KeyboardInterrupt):
                S4.render(self.cfg)
        self.assertIsNotNone(launched[0].poll())
        self.assertTrue(handles[0].closed)
        self.assertFalse((frames / "f000.png").exists())
        self.assertEqual(json.loads((view.parent.parent / "render_chunks.json").read_text())["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
