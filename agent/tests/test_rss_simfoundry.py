"""Exercise the public import command with real RSS physics and native tapes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def write_fixture(root: Path, *, height: float = 0.05):
    """A synthetic cube decomposed into two collision hulls, not reconstructed video data."""
    import trimesh

    stage8, stage11, stage12 = (
        root / "s8_pose",
        root / "s11_sim",
        root / "s12_physics",
    )
    mesh, info = stage8 / "canonical_mesh", stage8 / "info"
    urdf = stage11 / "objects/cube/fixture/urdf"
    for path in (mesh, info, urdf / "meshes", stage12):
        path.mkdir(parents=True, exist_ok=True)
    trimesh.creation.box(extents=(0.1, 0.1, 0.1)).export(mesh / "iter_0.glb")
    trimesh.creation.box(extents=(0.05, 0.1, 0.1)).export(urdf / "meshes/half.obj")
    (info / "iter_0.json").write_text(json.dumps({"z_up": {"scale": 1.0}}))
    (stage11 / "scene_objects_info.json").write_text(
        json.dumps(
            {
                "0": {
                    "name": "iter_0",
                    "category": "cube",
                    "model": "fixture",
                    "friction": 0.5,
                }
            }
        )
    )
    (urdf / "fixture.urdf").write_text(
        '<robot name="fixture"><link name="cube"><inertial><mass value="0.2"/>'
        '<inertia ixx="0.000333333" iyy="0.000333333" izz="0.000333333" ixy="0" ixz="0" iyz="0"/>'
        '</inertial><collision><origin xyz="-0.025 0 0"/>'
        '<geometry><mesh filename="meshes/half.obj"/></geometry></collision>'
        '<collision><origin xyz="0.025 0 0"/><geometry><mesh filename="meshes/half.obj"/></geometry>'
        "</collision></link></robot>"
    )
    (stage12 / "pb_scene_poses.json").write_text(
        json.dumps({"iter_0": [[0.2, -0.1, height], [0, 0, 0, 1]]})
    )
    return stage8, stage11, stage12


class SimFoundryImportCommandTests(unittest.TestCase):
    def invoke(self, directory: Path, *, height=0.05, extra_link=False):
        stage8, stage11, stage12 = write_fixture(directory / "source", height=height)
        if extra_link:
            path = stage11 / "objects/cube/fixture/urdf/fixture.urdf"
            path.write_text(
                path.read_text().replace(
                    "</robot>", '<link name="moving_part"/></robot>'
                )
            )
        out = directory / "result"
        process = subprocess.run(
            [
                sys.executable,
                str(ROOT / "run_robosimstudio.py"),
                "simfoundry",
                "--simfoundry-stage8",
                str(stage8),
                "--simfoundry-stage11",
                str(stage11),
                "--simfoundry-stage12",
                str(stage12),
                "--physics-device",
                "cpu",
                "--gpu",
                "",
                "--no-render",
                "--output",
                str(out),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            env=dict(os.environ, CUDA_VISIBLE_DEVICES="", DEXAGENT_NO_ENV_SWITCH="1"),
        )
        report = json.loads((out / "stage_report.json").read_text())
        return process, report, out

    def test_stable_compound_asset_records_a_passive_rss_episode(self):
        with tempfile.TemporaryDirectory(prefix="rss-simfoundry-cli-") as temp:
            process, report, out = self.invoke(Path(temp))
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertEqual(
                report["status"], "warning"
            )  # inertial-frame review remains explicit
            self.assertTrue(
                all(
                    c["status"] == "passed"
                    for c in report["checks"]
                    if c.get("required", True)
                )
            )
            self.assertEqual(
                report["metrics"]["scope"], "simfoundry_rigid_asset_validation"
            )
            self.assertFalse(report["metrics"]["training_eligible"])
            self.assertLess(
                report["metrics"]["objects"]["iter_0"]["max_drift_m"], 0.001
            )
            with np.load(out / "episodes/0000/steps.npz", allow_pickle=False) as tape:
                self.assertEqual(tape["action"].shape, (150, 0))
                self.assertTrue(np.all(np.diff(tape["sim_time"]) > 0))
            manifest = json.loads((out / "simfoundry_import.json").read_text())
            self.assertTrue(
                (out / manifest["objects"][0]["asset"]["path"])
                .resolve()
                .is_relative_to(out)
            )
            self.assertEqual(len(manifest["objects"][0]["asset"]["collision_paths"]), 2)
            self.assertFalse(manifest["training_eligible"])
            final_q = np.asarray(
                report["metrics"]["objects"]["iter_0"]["final_pose"][3:]
            )
            final_q /= np.linalg.norm(final_q)
            final_rotation = 2 * np.arccos(np.clip(abs(final_q[-1]), 0, 1))
            self.assertGreaterEqual(
                report["metrics"]["objects"]["iter_0"]["max_rotation_rad"],
                final_rotation - 1e-8,
            )

            # Replay must use the same native model, without even warm-up physics.
            from workbench.native_preview import build

            preview = out / "preview"
            preview.mkdir()
            result = build(
                out, out / "episodes/0000", preview, mode="rss_assets", max_frames=5
            )
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["physics_steps_executed"], 0)
            self.assertEqual(result["preview_frames"], 5)
            self.assertTrue((preview / "viewer.html").is_file())

            # Metadata and asset edits invalidate identity rather than silently
            # displaying a different scene with the old recorded body poses.
            from agent.rss_simfoundry import rebuild_snapshot, snapshot_assets

            manifest_path = out / "simfoundry_import.json"
            original = manifest_path.read_bytes()
            manifest["objects"][0]["pose"][0] += 1
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "snapshot does not match"):
                rebuild_snapshot(out, out / "episodes/0000")
            manifest_path.write_bytes(original)
            from agent.run_reports import sha256_file

            manifest = json.loads(original)
            manifest["model_identity"]["body_labels"] = ["wrong-body-order"]
            manifest_path.write_text(json.dumps(manifest))
            meta_path = out / "episodes/0000/meta.json"
            meta_original = meta_path.read_bytes()
            meta = json.loads(meta_original)
            meta["metrics"]["import_manifest_sha256"] = sha256_file(manifest_path)
            meta_path.write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError, "model identity"):
                rebuild_snapshot(out, out / "episodes/0000")
            manifest_path.write_bytes(original)
            meta_path.write_bytes(meta_original)
            visual = out / manifest["objects"][0]["asset"]["path"]
            visual.write_bytes(visual.read_bytes() + b"changed")
            with self.assertRaisesRegex(ValueError, "asset identity"):
                snapshot_assets(out)

    def test_unstable_import_fails_and_retains_diagnostic_recording(self):
        with tempfile.TemporaryDirectory(prefix="rss-simfoundry-fall-") as temp:
            process, report, out = self.invoke(Path(temp), height=0.3)
            self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
            self.assertEqual(report["status"], "failed")
            failed = {c["id"] for c in report["checks"] if c["status"] == "failed"}
            self.assertIn("iter_0.drift", failed)
            self.assertTrue((out / "episodes/0000/privileged.npz").is_file())
            self.assertFalse(
                json.loads((out / "run_summary.json").read_text())[
                    "full_pipeline_complete"
                ]
            )

    def test_unsupported_asset_fails_before_physics_and_keeps_report(self):
        with tempfile.TemporaryDirectory(prefix="rss-simfoundry-reject-") as temp:
            process, report, out = self.invoke(Path(temp), extra_link=True)
            self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
            self.assertEqual(report["status"], "failed")
            self.assertIn("expected one rigid link", json.dumps(report))
            self.assertFalse((out / "episodes").exists())

    def test_rejects_overlapping_output_without_writing_upstream(self):
        with tempfile.TemporaryDirectory(prefix="rss-simfoundry-overlap-") as temp:
            stage8, stage11, stage12 = write_fixture(Path(temp))
            before = {
                str(p): p.read_bytes() for p in Path(temp).rglob("*") if p.is_file()
            }
            process = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "run_robosimstudio.py"),
                    "simfoundry",
                    "--simfoundry-stage8",
                    str(stage8),
                    "--simfoundry-stage11",
                    str(stage11),
                    "--simfoundry-stage12",
                    str(stage12),
                    "--output",
                    str(stage8 / "result"),
                    "--no-render",
                    "--gpu",
                    "",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                env=dict(os.environ, DEXAGENT_NO_ENV_SWITCH="1"),
                check=False,
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("must not overlap", process.stderr)
            self.assertEqual(
                {str(p): p.read_bytes() for p in Path(temp).rglob("*") if p.is_file()},
                before,
            )


if __name__ == "__main__":
    unittest.main()
