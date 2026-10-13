"""run_dexagent between stages: every stage's report is printed, and when stages remain the user is asked whether to
continue; no stops the run there (with the command that continues it), --yes runs through, no terminal means stop, and
a cross-stage repair is confirmed before the earlier stage is re-opened.

    python -m unittest agent.tests.test_pipeline_confirm
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DEXAGENT_QUIET", "1")

import run_dexagent as D  # noqa: E402


class PipelineConfirmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg_path = Path(self.tmp.name) / "c.yaml"
        self.cfg_path.write_text(f"run:\n  name: p\n  output_dir: {self.tmp.name}\n")
        self.ran = []

    def fake_stage(self, results=None):
        results = results or {}

        def run(stage, cfg_path, extra, cfg):
            self.ran.append(stage)
            ws = Path(self.tmp.name) / "p" / stage
            ws.mkdir(parents=True, exist_ok=True)
            result = {"stage": stage, "finished": True, "verdict": {"ok": True, "reports": {"fixture": {"ok": True}}}}
            r = {"stage": stage, "finished": True, "workspace": str(ws), "rounds": 3, "tool_calls": 7,
                 "tools_used": {"execute_subgoal_plan": 2}, "new_tools": [{"name": "my_check", "type": "verifier"}]}
            r.update(results.get(stage, {}))
            result.update(r)
            r["run_record"] = result
            (ws / "run.json").write_text(json.dumps(result))
            return r
        return run

    @staticmethod
    def passed_preflight(cfg, stages, profile):
        return {"schema": "dexagent.step_report.v1", "run": cfg["run"]["name"], "profile": profile,
                "stage": "preflight", "status": "passed", "checks": [], "inputs": {}}

    def run_main(self, args, answers=(), tty=True, results=None):
        it = iter(answers)
        with patch.object(D, "preflight", self.passed_preflight), \
             patch.object(D, "run_agent_stage", self.fake_stage(results)), \
             patch.object(D, "run_stage4", lambda cfg: {"stage": "stage4", "finished": True, "workspace": ""}), \
             patch("sys.stdin.isatty", return_value=tty), patch("builtins.input", lambda q: next(it)):
            rc = D.main([str(self.cfg_path), *args])
        summary = json.loads((Path(self.tmp.name) / "p" / "run_summary.json").read_text())
        return rc, summary

    def test_no_stops_after_the_stage(self):
        rc, summary = self.run_main(["--to", "stage3", "--confirm-stages"], answers=["y", "n"])
        self.assertEqual((rc, self.ran), (2, ["stage1", "stage2"]))
        self.assertEqual(summary["stopped_by_user_after"], "stage2")
        self.assertEqual(summary["status"], "partial")
        self.assertTrue(summary["stages"][-1]["stopped_by_user"])

    def test_yes_runs_every_stage_and_no_terminal_stops(self):
        rc, _ = self.run_main(["--to", "stage3", "--yes"])
        self.assertEqual(self.ran, ["stage1", "stage2", "stage3"])
        self.ran.clear()
        rc, summary = self.run_main(["--to", "stage3", "--confirm-stages"], tty=False)       # no TTY: partial, exit 2
        self.assertEqual((rc, self.ran, summary["stopped_by_user_after"]), (2, ["stage1"], "stage1"))

    def test_default_advances_without_confirmation(self):
        rc, summary = self.run_main(["--to", "stage3"])
        self.assertEqual((rc, self.ran), (0, ["stage1", "stage2", "stage3"]))
        self.assertEqual(summary["status"], "scope_completed")
        self.assertFalse(summary["full_pipeline_complete"])

    def test_repair_is_confirmed(self):
        blocked = {"stage3": {"finished": False, "upstream": {"stage": "stage2", "evidence": "nothing to grip"}, "failure": "blocked"}}
        rc, summary = self.run_main(["--from", "stage3", "--to", "stage3"], answers=["n"], results=blocked)
        self.assertEqual((rc, self.ran), (1, ["stage3"]))
        self.assertIn("user stopped", summary["repair_loops"][0]["refused"])

    def test_required_skipped_verifier_blocks_downstream(self):
        verdict = {"ok": True, "reports": {"fixture": {"ok": False, "skipped": True, "issues": ["not executed"]}}}
        rc, summary = self.run_main(["--to", "stage3"], results={"stage1": {"verdict": verdict}})
        self.assertEqual((rc, self.ran), (1, ["stage1"]))
        self.assertEqual(summary["stages"][0]["report_status"], "failed")
        self.assertTrue(summary["stages"][1]["blocked"])
        blocked_report = json.loads(Path(summary["stages"][1]["report_path"]).read_text())
        self.assertEqual(blocked_report["status"], "blocked")

    def test_stage4_settings_choose_cached_lama_checkpoint(self):
        task = Path(self.tmp.name) / "stage4.json"
        task.write_text(json.dumps({"inpaint": {"backend": "void"}}))
        D.apply_stage4_settings(task, {"inpaint_backend": "lama", "lama_checkpoint": "/data/model/big-lama.pt"})
        saved = json.loads(task.read_text())
        self.assertEqual(saved["inpaint"]["backend"], "lama")
        self.assertEqual(saved["inpaint"]["lama_model"], "/data/model/big-lama.pt")

    def test_partial_stage4_report_cannot_pass_top_level_stage_gate(self):
        cfg = D.load_config(self.cfg_path)
        workspace = Path(self.tmp.name) / "p" / "stage4"
        workspace.mkdir(parents=True)
        path = workspace / "run_report.json"
        steps = {name: {"status": "passed" if name == "randomize" else "skipped"}
                 for name in ("randomize", "plate", "export", "render", "composite")}
        path.write_text(json.dumps({"status": "partial", "steps": steps, "steps_requested": ["randomize"]}))
        result = {"stage": "stage4", "finished": True, "workspace": str(workspace),
                  "attempt_id": "fixture-attempt", "stage4_run_record": {
                      "invocation_id": "fixture-attempt", "status": "partial", "steps": steps,
                      "steps_requested": ["randomize"]},
                  "stage4_report": str(path), "stage4_status": "partial"}
        report = D.write_stage_report(cfg, result, "pipeline_eval", {"inputs": {}})
        self.assertEqual(report["status"], "failed")
        self.assertFalse(result["finished"])


if __name__ == "__main__":
    unittest.main()
