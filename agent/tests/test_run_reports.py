"""Common run-report semantics and profile behavior."""
import json
import io
import signal
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.config import apply_profile
from agent.run_reports import config_identity, status_for_checks, verifier_checks
import run_dexagent as D


class RunReportTests(unittest.TestCase):
    def test_required_skipped_check_fails(self):
        self.assertEqual(status_for_checks([{"required": True, "status": "skipped"}]), "failed")

    def test_empty_or_blocked_required_checks_fail_closed(self):
        self.assertEqual(status_for_checks([]), "failed")
        self.assertEqual(status_for_checks([{"required": True, "status": "blocked"}]), "failed")

    def test_optional_warning_does_not_fail_the_step(self):
        self.assertEqual(status_for_checks([{"required": True, "status": "passed"},
                                            {"required": False, "status": "warning"}]), "warning")

    def test_missing_verifier_reports_fail_closed(self):
        checks = verifier_checks({"ok": True, "reports": {}})
        self.assertEqual(checks[0]["status"], "failed")

    def test_verifier_warnings_reach_the_stage_status(self):
        checks = verifier_checks({"ok": True, "reports": {
            "scene_gate": {"ok": True, "warnings": ["reach search inconclusive"]}}})
        self.assertEqual(checks[0]["status"], "warning")
        self.assertEqual(checks[0]["observed"]["warnings"], ["reach search inconclusive"])
        self.assertEqual(status_for_checks(checks), "warning")

    def test_skipped_reason_is_not_a_passing_required_verifier(self):
        checks = verifier_checks({"ok": True, "reports": {
            "camera_check": {"ok": True, "skipped": "no camera observations"}}})
        self.assertEqual(checks[0]["status"], "skipped")
        self.assertEqual(status_for_checks(checks), "failed")

    def test_a_suite_skip_mapping_does_not_mark_the_entire_suite_skipped(self):
        checks = verifier_checks({"ok": True, "reports": {
            "scene_gate": {"ok": True, "skipped": {}}}})
        self.assertEqual(checks[0]["status"], "passed")

    def test_profiles_control_example_reuse(self):
        cfg = {"examples": {"reuse": True}}
        self.assertFalse(apply_profile(cfg, "pipeline_eval")["examples"]["reuse"])
        self.assertFalse(apply_profile(cfg, "dataset_release")["examples"]["reuse"])
        self.assertTrue(apply_profile(cfg, "smoke")["examples"]["reuse"])

    def test_pipeline_uses_last_result_for_each_stage(self):
        latest = D.latest_stage_results([
            {"stage": "stage3", "finished": True},
            {"stage": "stage3", "finished": False, "failure": "rerun failed"},
        ])
        self.assertFalse(latest["stage3"]["finished"])
        self.assertEqual(latest["stage3"]["failure"], "rerun failed")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {"run": {"name": "report-test", "output_dir": self.tmp.name}}

    @staticmethod
    def preflight():
        return {"config_sha256": "effective-digest", "inputs": {"config": {"sha256": "source-digest"}}}

    def test_agent_stage_archives_old_run_and_snapshots_new_run(self):
        workspace = D.run_dir(self.cfg, "stage1")
        old = {"verdict": {"ok": False, "reports": {"old": {"ok": False}}}}
        current = {"verdict": {"ok": True, "reports": {"new": {"ok": True}}}}
        (workspace / "run.json").write_text(json.dumps(old))

        def stage_main(_args):
            (workspace / "run.json").write_text(json.dumps(current))
            return 0

        with patch("importlib.import_module", return_value=SimpleNamespace(main=stage_main)):
            result = D.run_agent_stage("stage1", "config.yaml", [], self.cfg)

        self.assertTrue(result["finished"])
        self.assertEqual(result["run_record"], current)
        prior = workspace / "reports" / "attempts" / result["attempt_id"] / "prior_run.json"
        self.assertEqual(json.loads(prior.read_text()), old)

    def test_missing_snapshot_fails_closed_even_if_workspace_run_json_exists(self):
        workspace = D.run_dir(self.cfg, "stage1")
        (workspace / "run.json").write_text(json.dumps({
            "verdict": {"ok": True, "reports": {"stale": {"ok": True}}}}))
        with patch("importlib.import_module", return_value=SimpleNamespace(main=lambda _args: 0)):
            result = D.run_agent_stage("stage1", "config.yaml", [], self.cfg)

        self.assertIsNone(result["run_record"])
        report = D.write_stage_report(self.cfg, result, "pipeline_eval", self.preflight())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["attempt_id"], result["attempt_id"])
        self.assertEqual(report["config_sha256"], "effective-digest")
        self.assertIn("current_run_record", {check["id"] for check in report["checks"]})
        missing_reuse = dict(result, reused_example="examples/fake")
        smoke_report = D.write_stage_report(self.cfg, missing_reuse, "smoke", self.preflight())
        self.assertEqual(smoke_report["status"], "failed")

    def test_reuse_is_only_accepted_in_smoke_profile(self):
        result = {"stage": "stage1", "finished": True, "attempt_id": "reuse-attempt",
                  "workspace": str(D.run_dir(self.cfg, "stage1")),
                  "reused_example": "examples/example-a", "run_record": {}}

        regular = D.write_stage_report(self.cfg, dict(result), "pipeline_eval", self.preflight())
        smoke = D.write_stage_report(self.cfg, dict(result), "smoke", self.preflight())
        self.assertEqual(regular["status"], "failed")
        self.assertEqual(smoke["status"], "reused")

    def test_stage4_uses_only_matching_invocation_snapshot(self):
        workspace = D.run_dir(self.cfg, "stage4")
        report_path = workspace / "run_report.json"
        report_path.write_text(json.dumps({"invocation_id": "older", "status": "failed", "steps": {}}))
        steps = {name: {"status": "passed"} for name in
                 ("randomize", "plate", "export", "render", "composite")}
        steps["plate"]["status"] = "warning"
        result = {"stage": "stage4", "finished": True, "workspace": str(workspace),
                  "attempt_id": "current", "stage4_report": str(report_path),
                  "stage4_run_record": {"invocation_id": "current", "status": "passed_with_warnings",
                                         "release_ready": True, "steps": steps}}

        report = D.write_stage_report(self.cfg, result, "pipeline_eval", self.preflight())
        self.assertEqual(report["status"], "warning")
        self.assertEqual(report["attempt_id"], "current")
        self.assertTrue(report["metrics"]["release_ready"])

        result["attempt_id"] = "another"
        rejected = D.write_stage_report(self.cfg, result, "pipeline_eval", self.preflight())
        self.assertEqual(rejected["status"], "failed")
        self.assertIn("stage4_run_report", {check["id"] for check in rejected["checks"]})

    def test_stage4_runner_receives_attempt_id_and_returns_current_snapshot(self):
        stage3 = D.run_dir(self.cfg, "stage3")
        accepted = Path(self.tmp.name) / "accepted"
        accepted.mkdir()
        task_dir = stage3 / "stage4_task"
        task_dir.mkdir(parents=True)
        (task_dir / "stage4.json").write_text("{}")
        report_path = task_dir / "outputs" / "run_report.json"
        calls = {}

        class FakeConsole:
            def __init__(self, *_args, **_kwargs): pass
            def banner(self, **_kwargs): pass
            def _out(self, *_args): pass
            def done(self, *_args): pass

        class FakeProcess:
            pid = 24680
            def __init__(self): self.stdout = io.StringIO("")
            def wait(self):
                report_path.parent.mkdir(parents=True, exist_ok=True)
                steps = {name: {"status": "passed"} for name in
                         ("randomize", "plate", "export", "render", "composite")}
                invocation_id = calls["env"]["DEXAGENT_STAGE4_INVOCATION_ID"]
                if calls["mismatch"]:
                    invocation_id = "stale-invocation"
                report_path.write_text(json.dumps({
                    "invocation_id": invocation_id,
                    "status": "passed", "steps": steps}))
                return 0

        with patch("agent.artifacts.accepted_delivery", return_value=accepted), \
                patch("agent.artifacts.expose_accepted"), patch.object(D, "Console", FakeConsole), \
                patch("subprocess.Popen", side_effect=lambda *args, **kwargs: (calls.update(kwargs) or FakeProcess())):
            calls["mismatch"] = False
            result = D.run_stage4(self.cfg)
            self.assertTrue(result["finished"])
            self.assertEqual(calls["env"]["DEXAGENT_STAGE4_INVOCATION_ID"], result["attempt_id"])
            self.assertTrue(calls["start_new_session"])
            self.assertEqual(result["stage4_run_record"]["invocation_id"], result["attempt_id"])

            calls["mismatch"] = True
            stale_result = D.run_stage4(self.cfg)

        self.assertFalse(stale_result["finished"])
        self.assertIsNone(stale_result["stage4_run_record"])
        self.assertIn("invocation_id", stale_result["failure"])
        report = D.write_stage_report(self.cfg, stale_result, "pipeline_eval", self.preflight())
        self.assertEqual(report["status"], "failed")

    def test_preflight_interrupt_writes_terminal_reports(self):
        cfg = {"run": {"name": "report-test", "output_dir": self.tmp.name}, "inputs": {}}
        with patch.object(D, "load_config", return_value=cfg), \
                patch.object(D, "preflight", side_effect=KeyboardInterrupt()), \
                patch.object(D, "summary_table"):
            code = D.main(["config.yaml", "--to", "stage1"])

        out = Path(self.tmp.name) / "report-test"
        preflight = json.loads((out / "reports" / "preflight.json").read_text())
        summary = json.loads((out / "run_summary.json").read_text())
        stage_report = json.loads((out / "stage1" / "stage_report.json").read_text())
        self.assertEqual(code, 1)
        self.assertEqual(preflight["status"], "failed")
        self.assertTrue(preflight["interrupted"])
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(stage_report["status"], "blocked")

    def test_sigterm_during_stage_becomes_interruption_and_restores_handler(self):
        cfg = {"run": {"name": "report-test", "output_dir": self.tmp.name}, "inputs": {}}
        original_handler = signal.getsignal(signal.SIGTERM)

        def interrupt_stage(*_args):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

        with patch.object(D, "load_config", return_value=cfg), \
                patch.object(D, "preflight", return_value={"stage": "preflight", "status": "passed", "inputs": {}}), \
                patch.object(D, "run_agent_stage", side_effect=interrupt_stage), \
                patch.object(D, "summary_table"):
            code = D.main(["config.yaml", "--to", "stage1"])

        summary = json.loads((Path(self.tmp.name) / "report-test" / "run_summary.json").read_text())
        self.assertEqual(code, 1)
        self.assertTrue(summary["interrupted"])
        self.assertTrue(summary["stages"][0]["interrupted"])
        self.assertIs(signal.getsignal(signal.SIGTERM), original_handler)

    def test_stage_system_exit_becomes_a_terminal_failure(self):
        cfg = {"run": {"name": "report-test", "output_dir": self.tmp.name}, "inputs": {}}
        with patch.object(D, "load_config", return_value=cfg), \
                patch.object(D, "preflight", return_value={"stage": "preflight", "status": "passed", "inputs": {}}), \
                patch.object(D, "run_agent_stage", side_effect=SystemExit(1)), \
                patch.object(D, "summary_table"):
            code = D.main(["config.yaml", "--to", "stage1"])

        out = Path(self.tmp.name) / "report-test"
        summary = json.loads((out / "run_summary.json").read_text())
        stage_report = json.loads((out / "stage1" / "stage_report.json").read_text())
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "failed")
        self.assertFalse(summary["stages"][0]["interrupted"])
        self.assertFalse(summary["interrupted"])
        self.assertEqual(stage_report["status"], "failed")
        self.assertTrue(D.is_interruption(SystemExit(130)))

    def test_cli_overrides_change_hash_without_editing_source_config(self):
        source = Path(self.tmp.name) / "unchanged.yaml"
        source.write_text(f"run:\n  name: report-test\n  output_dir: {self.tmp.name}\n")
        original = source.read_bytes()
        effective_hashes = []
        seen = []

        def preflight(cfg, _stages, _profile):
            effective_hashes.append(config_identity(cfg)["effective_sha256"])
            return {"stage": "preflight", "status": "passed", "inputs": {}}

        def fake_stage(stage, _config_path, extra, cfg):
            seen.append((cfg, extra))
            return {"stage": stage, "finished": True, "workspace": str(D.run_dir(cfg, stage)),
                    "attempt_id": f"{stage}-{len(seen)}",
                    "run_record": {"verdict": {"ok": True, "reports": {"fixture": {"ok": True}}}}}

        with patch.object(D, "preflight", side_effect=preflight), \
                patch.object(D, "run_agent_stage", side_effect=fake_stage), \
                patch("agent.reuse.save_run", return_value=None), patch.object(D, "summary_table"):
            first = D.main([str(source), "--from", "stage1", "--to", "stage1", "--yes", "--prompt", "",
                            "--max-rounds", "4", "--llm", "mock:first.json", "--resume-stage3-attempt", "/attempt/a"])
            second = D.main([str(source), "--from", "stage1", "--to", "stage1", "--yes", "--prompt", "different task",
                             "--max-rounds", "5", "--llm", "mock:second.json", "--resume-stage3-attempt", "/attempt/b"])

        self.assertEqual((first, second), (0, 0))
        self.assertEqual(source.read_bytes(), original)
        self.assertNotEqual(effective_hashes[0], effective_hashes[1])
        first_cfg, first_extra = seen[0]
        second_cfg, _second_extra = seen[1]
        self.assertEqual(first_cfg["inputs"]["task"], "")
        self.assertIn("--prompt", first_extra)
        self.assertEqual(first_extra[first_extra.index("--prompt") + 1], "")
        for stage in ("stage1", "stage2", "stage3"):
            self.assertEqual(first_cfg[stage]["max_rounds"], 4)
        self.assertEqual(first_cfg["runtime_options"], {
            "llm": "mock:first.json", "resume_stage3_attempt": "/attempt/a"})
        self.assertEqual(second_cfg["inputs"]["task"], "different task")
        self.assertEqual(second_cfg["stage3"]["max_rounds"], 5)
        self.assertEqual(second_cfg["runtime_options"], {
            "llm": "mock:second.json", "resume_stage3_attempt": "/attempt/b"})

    def test_dataset_release_respects_stage4_release_ready_with_warnings(self):
        cfg = {"run": {"name": "report-test", "output_dir": self.tmp.name}, "inputs": {},
               "repair": {"max_loops_per_stage": 0}, "examples": {"save_assets": False}}

        def fake_agent_stage(stage, _config_path, _extra, runtime_cfg):
            return {"stage": stage, "finished": True, "workspace": str(D.run_dir(runtime_cfg, stage)),
                    "attempt_id": f"{stage}-attempt",
                    "run_record": {"verdict": {"ok": True, "reports": {"fixture": {"ok": True}}}}}

        steps = {name: {"status": "passed"} for name in
                 ("randomize", "plate", "export", "render", "composite")}
        stage4 = {"stage": "stage4", "finished": True, "workspace": str(D.run_dir(cfg, "stage4")),
                  "attempt_id": "stage4-attempt", "stage4_status": "passed_with_warnings",
                  "stage4_run_record": {"invocation_id": "stage4-attempt", "status": "passed_with_warnings",
                                        "release_ready": True, "steps": steps}}
        with patch.object(D, "load_config", return_value=cfg), \
                patch.object(D, "preflight", return_value={"status": "passed", "inputs": {}}), \
                patch.object(D, "run_agent_stage", side_effect=fake_agent_stage), \
                patch.object(D, "run_stage4", return_value=stage4), \
                patch("agent.reuse.save_run", return_value=None), patch.object(D, "summary_table"):
            code = D.main(["config.yaml", "--profile", "dataset_release", "--yes"])

        summary = json.loads((Path(self.tmp.name) / "report-test" / "run_summary.json").read_text())
        self.assertEqual(code, 0)
        self.assertEqual(summary["status"], "passed_with_warnings")
        self.assertTrue(summary["release_ready"])


if __name__ == "__main__":
    unittest.main()
