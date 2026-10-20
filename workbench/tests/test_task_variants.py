"""Input handoff contracts; fixture reports do not certify real physics."""

import json
from pathlib import Path

import cv2
import mujoco
import numpy as np
import pytest

from agent.input_identity import video_identity
from agent.run_reports import atomic_json, sha256_file
from agent.scene_candidate import digest
from agent.task_variants import (
    accepted_candidate,
    describe_task,
    prepare_task,
    verify_task_snapshot,
)


@pytest.fixture
def task_input(tmp_path, monkeypatch):
    from tool_library.common import episode as E, rigid_robot as RR

    # Data fixture only. Real 56-joint equivalence is checked on the task run.
    def compile_fixture(value):
        if isinstance(value, (str, Path)):
            value, _ = E.build_execution_model(value)
        return RR.RigidRobot(value, value, "")

    monkeypatch.setattr(RR, "compile_rigid", compile_fixture)
    source, candidate = tmp_path / "source", tmp_path / "accepted"
    video = source / "stage3/stage4_task/human_video.mp4"
    video.parent.mkdir(parents=True)
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (32, 32))
    for i in range(4):
        writer.write(np.full((32, 32, 3), i * 30, np.uint8))
    writer.release()
    identity = video_identity(video)
    atomic_json(
        source / "stage1/scene_semantics.json",
        {
            "video": identity,
            "task_success": "The cup rests inside the bowl.",
            "subgoals": [{"index": 0, "goal": "inside(cup,bowl)"}],
        },
    )
    atomic_json(
        source / "stage2/scene_recon.json",
        {
            "scene_xml": "scene.xml",
            "camera_json": "camera.json",
            "objects": [
                {
                    "id": n,
                    "root_body": n,
                    "pose": {"x": 0},
                    "iou": 0.9,
                    "verifiers": {"old_layout": {"ok": True}},
                }
                for n in ("cup", "bowl")
            ],
        },
    )
    atomic_json(source / "stage2/camera.json", {"name": "fixture_camera"})
    atomic_json(
        source / "stage2/reference_evidence_recipes.json",
        {"schema": "dexagent.reference_recipes.v1", "authority": False},
    )
    atomic_json(
        source / "stage2/stage_report.json",
        {"status": "passed", "purpose": "historical fixture only"},
    )
    scene = candidate / "candidate/scene.xml"
    scene.parent.mkdir(parents=True)
    scene.write_text("""<mujoco><worldbody>
        <body name="cup" pos=".12 .2 .03"><freejoint name="cup_free"/><geom size=".03" mass=".1"/></body>
        <body name="bowl" pos=".4 .5 .03"><freejoint name="bowl_free"/><geom size=".04" mass=".2"/></body>
        </worldbody><keyframe><key name="home"/></keyframe></mujoco>""")
    E.build_execution_model(scene, out_xml=str(source / "stage2/scene.xml"))
    scene.write_bytes((source / "stage2/scene.xml").read_bytes())
    manifest = {
        "schema": "embodiforge.scene_candidate.v1",
        "objects": [{"name": n} for n in ("cup", "bowl")],
        "files": {"scene.xml": sha256_file(scene)},
    }
    manifest["candidate_sha256"] = digest(manifest)
    atomic_json(scene.parent / "manifest.json", manifest)
    atomic_json(
        candidate / "stage_report.json",
        {
            "status": "passed",
            "metrics": {"scope": "scene_candidate_validation"},
            "checks": [{"id": "fixture_only", "required": True, "status": "passed"}],
            "inputs": {"candidate": {"sha256": manifest["candidate_sha256"]}},
        },
    )
    pending = {
        "candidate_run": str(candidate),
        "source_run": str(source),
        "source_run_id": "a" * 24,
        "candidate_run_id": "b" * 24,
        "sha256": describe_task(candidate, source)["sha256"],
    }
    return pending, source, candidate


def test_prepare_freezes_layout_and_task_without_old_controls_or_verdicts(
    task_input, tmp_path
):
    pending, source, candidate = task_input
    old = (source / "stage2/scene_recon.json").read_bytes()
    out = tmp_path / "new-run"
    manifest = prepare_task(pending, out)
    assert manifest["old_controls_reused"] is False
    assert (
        not (out / "stage3").exists()
        and not (out / "stage2/stage_report.json").exists()
    )
    assert not (out / "run_summary.json").exists()
    recon = json.loads((out / "stage2/scene_recon.json").read_text())
    assert recon["objects"][0]["pose"]["x"] == pytest.approx(0.12)
    assert all("verifiers" not in obj and "iou" not in obj for obj in recon["objects"])
    assert recon["layout_variant"]["source_stage2_revalidated"] is False
    assert (source / "stage2/scene_recon.json").read_bytes() == old
    assert (out / "inputs/source_task/stage2/stage_report.json").is_file()
    model = mujoco.MjModel.from_xml_path(str(out / "stage2/scene.xml"))
    assert model.nq == 14
    assert sha256_file(out / "inputs/video.mp4") == sha256_file(
        source / "stage3/stage4_task/human_video.mp4"
    )
    assert (
        manifest["candidate_sha256"]
        == accepted_candidate(candidate)["candidate_sha256"]
    )


def test_source_changes_or_failed_candidate_block_handoff(task_input, tmp_path):
    pending, source, candidate = task_input
    (source / "stage2/camera.json").write_text('{"changed":true}')
    with pytest.raises(ValueError, match="已变化"):
        prepare_task(pending, tmp_path / "new-run")
    report = json.loads((candidate / "stage_report.json").read_text())
    report["checks"][0]["status"] = "failed"
    atomic_json(candidate / "stage_report.json", report)
    with pytest.raises(ValueError, match="必需物理检查"):
        accepted_candidate(candidate)


def test_retry_uses_sealed_inputs_after_upstream_removed_and_rejects_changes(
    task_input, tmp_path
):
    pending, source, candidate = task_input
    out = tmp_path / "first"
    prepare_task(pending, out)
    seal = sha256_file(out / "task_inputs.json")
    import shutil

    shutil.rmtree(source)
    shutil.rmtree(candidate)
    retry = {"snapshot": str(out), "sha256": pending["sha256"], "snapshot_sha256": seal}
    prepare_task(retry, tmp_path / "second")
    assert (tmp_path / "second/task_inputs.json").read_bytes() == (
        out / "task_inputs.json"
    ).read_bytes()
    (out / "stage1/scene_semantics.json").write_text('{"changed":true}')
    with pytest.raises(ValueError, match="快照发生变化"):
        prepare_task(retry, tmp_path / "third")
    manifest = json.loads((out / "task_inputs.json").read_text())
    manifest["files"]["stage1/scene_semantics.json"] = sha256_file(
        out / "stage1/scene_semantics.json"
    )
    atomic_json(out / "task_inputs.json", manifest)
    with pytest.raises(ValueError, match="清单发生变化"):
        verify_task_snapshot(out, pending["sha256"], seal)


def test_task_inputs_cannot_read_source_files_through_symlinks(task_input, tmp_path):
    _, source, candidate = task_input
    secret = tmp_path / "private.txt"
    secret.write_text("not an input")
    (source / "stage2/linked.txt").symlink_to(secret)
    with pytest.raises(ValueError, match="符号链接"):
        describe_task(candidate, source)


def test_fresh_source_review_cannot_change_the_accepted_physics(task_input, tmp_path):
    pending, source, candidate = task_input
    spec = mujoco.MjSpec.from_file(str(source / "stage2/scene.xml"))
    spec.option.gravity = [0, 0, -1]
    spec.compile()
    (source / "stage2/scene.xml").write_text(spec.to_xml())
    pending["sha256"] = describe_task(candidate, source)["sha256"]
    out = tmp_path / "changed-physics"
    with pytest.raises(ValueError, match="完整物理模型.*不一致"):
        prepare_task(pending, out)
    assert not (out / "task_inputs.json").exists()
    assert not (out / "stage3").exists()


@pytest.mark.parametrize("linked", ["file", "directory", "manifest"])
def test_sealed_inputs_reject_symlinks_even_with_identical_bytes(
    task_input, tmp_path, linked
):
    pending, _, _ = task_input
    out = tmp_path / "frozen"
    prepare_task(pending, out)
    seal = sha256_file(out / "task_inputs.json")
    target = (
        out
        / {
            "file": "stage2/camera.json",
            "directory": "stage2",
            "manifest": "task_inputs.json",
        }[linked]
    )
    saved = out / "inputs/linked-input"
    target.rename(saved)
    target.symlink_to(saved, target_is_directory=linked == "directory")
    with pytest.raises(ValueError, match="符号链接"):
        verify_task_snapshot(out, pending["sha256"], seal)


def test_absolute_source_references_remain_local_after_source_removal(
    task_input, tmp_path
):
    import shutil

    pending, source, _ = task_input
    stage2 = source / "stage2"
    paths = ["frame0.png", "cup.png", "bowl.png", "reveal.png", "asset.xml"]
    for name in paths:
        (stage2 / name).write_bytes(b"local source reference fixture")
    recon = json.loads((stage2 / "scene_recon.json").read_text())
    recon.update(
        frame0=str(stage2 / "frame0.png"), camera_json=str(stage2 / "camera.json")
    )
    for obj in recon["objects"]:
        obj["mask"] = str(stage2 / f"{obj['id']}.png")
        obj["asset_xml"] = str(stage2 / "asset.xml")
    recon["objects"][0]["hidden_at_frame0"] = {
        "reveal_mask": str(stage2 / "reveal.png")
    }
    atomic_json(stage2 / "scene_recon.json", recon)
    atomic_json(
        stage2 / "reference_evidence_recipes.json",
        {
            "schema": "dexagent.reference_recipes.v1",
            "authority": False,
            "frame_paths": {
                str(stage2 / "frame0.png"): {
                    "sha256": "fixture",
                    "record": {"source_frame_index": 0},
                }
            },
        },
    )
    pending["sha256"] = describe_task(pending["candidate_run"], source)["sha256"]
    out = tmp_path / "frozen"
    prepare_task(pending, out)
    seal = sha256_file(out / "task_inputs.json")
    shutil.rmtree(source)
    prepare_task(
        {"snapshot": str(out), "sha256": pending["sha256"], "snapshot_sha256": seal},
        tmp_path / "retry",
    )
    for run in (out, tmp_path / "retry"):
        active = json.loads((run / "stage2/scene_recon.json").read_text())
        assert active["frame0"] == "frame0.png"
        assert active["camera_json"] == "camera.json"
        for obj in active["objects"]:
            for field in ("mask", "asset_xml"):
                assert (run / "stage2" / obj[field]).is_file()
                assert not Path(obj[field]).is_absolute()
        assert active["objects"][0]["hidden_at_frame0"]["reveal_mask"] == "reveal.png"
        recipes = json.loads(
            (run / "stage2/reference_evidence_recipes.json").read_text()
        )
        assert list(recipes["frame_paths"]) == ["frame0.png"]


def test_external_active_references_and_example_certificates_block_handoff(
    task_input, tmp_path
):
    pending, source, candidate = task_input
    external = tmp_path / "outside.png"
    external.write_text("not a registered source input")
    path = source / "stage2/scene_recon.json"
    recon = json.loads(path.read_text())
    recon["frame0"] = str(external)
    atomic_json(path, recon)
    pending["sha256"] = describe_task(candidate, source)["sha256"]
    with pytest.raises(ValueError, match="运行目录内"):
        prepare_task(pending, tmp_path / "new")
    atomic_json(source / "stage2/reuse.json", {"reused_from": "/outside/example"})
    with pytest.raises(ValueError, match="外部示例证书"):
        describe_task(candidate, source)


def test_non_object_json_cannot_certify_or_seal_task_inputs(task_input, tmp_path):
    pending, _, candidate = task_input
    out = tmp_path / "frozen"
    prepare_task(pending, out)
    atomic_json(candidate / "stage_report.json", [])
    with pytest.raises(ValueError, match="必需物理检查"):
        accepted_candidate(candidate)
    atomic_json(out / "task_inputs.json", [])
    with pytest.raises(ValueError, match="快照身份"):
        verify_task_snapshot(out, pending["sha256"])
