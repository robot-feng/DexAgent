"""Candidate identity/pose contracts; real RSS evidence is recorded separately."""

import json
from pathlib import Path

import mujoco
import numpy as np
import pytest

from agent.scene_candidate import freeze_candidate, inspect_scene, verify_snapshot
from pydantic import ValidationError
from workbench.models import ExecutionCreate


@pytest.fixture
def source(tmp_path, monkeypatch):
    from tool_library.common import rigid_robot

    mesh = tmp_path / "shape.obj"
    mesh.write_text(
        "v 0 0 0\nv .03 0 0\nv 0 .03 0\nv 0 0 .03\nf 1 3 2\nf 1 2 4\nf 1 4 3\nf 2 3 4\n"
    )
    robot = "".join(
        f'<body name="link_{i}" pos="{i} 0 1"><joint name="robot_{i}"/><geom size=".01" mass="1"/></body>'
        for i in range(56)
    )
    actuators = "".join(
        f'<motor name="motor_{i}" joint="robot_{i}"/>' for i in range(56)
    )
    path = tmp_path / "scene.xml"
    path.write_text(f'''<mujoco><asset><mesh name="shape" file="{mesh}"/></asset>
        <worldbody>{robot}<body name="cup" pos=".1 .2 .3"><freejoint name="cup_free"/>
        <geom type="mesh" mesh="shape" mass=".2" friction=".8 .01 .001"/></body>
        <body name="bowl" pos=".4 .5 .6"><freejoint name="bowl_free"/><geom size=".03" mass=".4"/></body>
        </worldbody><actuator>{actuators}</actuator><keyframe><key name="home"/></keyframe></mujoco>''')

    def compile_fixture(value):
        spec = mujoco.MjSpec.from_file(str(value))
        for asset in (*spec.meshes, *spec.textures):
            if asset.file and not Path(asset.file).is_absolute():
                asset.file = str(Path(value).parent / asset.file)
        model = spec.compile()
        return rigid_robot.RigidRobot(model, model, spec.to_xml())

    monkeypatch.setattr(rigid_robot, "compile_rigid", compile_fixture)
    return path


def request(source, offsets=None):
    descriptor, _ = inspect_scene(source)
    return {
        "source": str(source),
        "source_sha256": descriptor["sha256"],
        "offsets": offsets or [],
    }


def test_frozen_candidate_changes_only_requested_initial_position(source, tmp_path):
    original = source.read_bytes()
    before, rig = inspect_scene(source)
    frozen = freeze_candidate(
        request(source, [{"name": "cup", "translation_m": [0.01, -0.02, 0.03]}]),
        tmp_path / "candidate",
    )
    model = mujoco.MjModel.from_xml_path(str(tmp_path / "candidate/scene.xml"))
    expected = rig.model.key_qpos[0].copy()
    joint = model.joint("cup_free").id
    start = int(model.jnt_qposadr[joint])
    expected[start : start + 3] += [0.01, -0.02, 0.03]
    np.testing.assert_allclose(model.key_qpos[0], expected, atol=1e-12)
    for field in (
        "body_mass",
        "body_inertia",
        "geom_friction",
        "jnt_range",
        "actuator_gear",
    ):
        np.testing.assert_array_equal(getattr(model, field), getattr(rig.model, field))
    assert source.read_bytes() == original
    assert frozen["source_identity_sha256"] == before["sha256"]
    assert set(frozen["files"]) >= {"scene.xml"}
    assert str(source.parent) not in (tmp_path / "candidate/scene.xml").read_text()


def test_retry_uses_portable_frozen_assets_and_rejects_tampering(source, tmp_path):
    first = freeze_candidate(request(source), tmp_path / "first")
    source.unlink()
    (tmp_path / "shape.obj").unlink()
    retry = {
        "snapshot": str(tmp_path / "first"),
        "candidate_sha256": first["candidate_sha256"],
    }
    second = freeze_candidate(retry, tmp_path / "second")
    assert second == first
    mujoco.MjModel.from_xml_path(str(tmp_path / "second/scene.xml"))
    mesh = next((tmp_path / "first/assets").iterdir())
    mesh.write_bytes(mesh.read_bytes() + b"\n# changed\n")
    with pytest.raises(ValueError, match="身份不匹配"):
        freeze_candidate(retry, tmp_path / "third")


def test_new_layout_keeps_its_parent_candidate_and_composes_translation(source, tmp_path):
    first = freeze_candidate(request(source, [{"name": "cup", "translation_m": [.01, 0, 0]}]), tmp_path / "first")
    child = freeze_candidate(request(tmp_path / "first/scene.xml", [{"name": "cup", "translation_m": [.02, 0, 0]}]), tmp_path / "child")
    assert child["parent_candidate_sha256"] == first["candidate_sha256"]
    assert child["candidate_sha256"] != first["candidate_sha256"]
    cup = next(item for item in child["objects"] if item["name"] == "cup")
    assert cup["position_m"] == pytest.approx([.13, .2, .3])


@pytest.mark.parametrize("changed", ["scene.xml", "shape.obj"])
def test_source_or_asset_changes_require_a_new_review(source, tmp_path, changed):
    pending = request(source)
    path = tmp_path / changed
    path.write_text(
        path.read_text()
        + ("\n<!-- changed -->" if changed.endswith("xml") else "\n# changed")
    )
    with pytest.raises(ValueError, match="已改变"):
        freeze_candidate(pending, tmp_path / "candidate")


@pytest.mark.parametrize(
    "offsets",
    [
        [{"name": "robot_0", "translation_m": [0, 0, 0]}],
        [{"name": "cup", "translation_m": [0, 0, 0]}] * 2,
        [{"name": "cup", "translation_m": [float("nan"), 0, 0]}],
        [{"name": "cup", "translation_m": [2, 0, 0]}],
    ],
)
def test_invalid_pose_changes_are_rejected(source, tmp_path, offsets):
    with pytest.raises(ValueError, match="物体不能重复"):
        freeze_candidate(request(source, offsets), tmp_path / "candidate")


def test_manifest_identity_cannot_be_rewritten_silently(source, tmp_path):
    freeze_candidate(request(source), tmp_path / "candidate")
    path = tmp_path / "candidate/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["objects"][0]["position_m"][0] += 0.2
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="候选清单身份"):
        verify_snapshot(path.parent)


def test_api_pose_contract_rejects_nonfinite_or_wrong_dimensions():
    for value in ([0, 0], [0, 0, 0, 0], [0, float("inf"), 0], [0, 2, 0]):
        with pytest.raises(ValidationError):
            ExecutionCreate(
                config_id="a" * 24,
                mode="candidate",
                scene_offsets=[{"name": "cup", "translation_m": value}],
            )


def test_scene_recording_is_not_classified_as_task_success_or_failure(tmp_path):
    from agent.run_reports import atomic_json
    from workbench.index import Index

    run = tmp_path / "runs/candidate"
    meta = run / "episodes/0000/meta.json"
    native = {
        "success": False,
        "metrics": {"task_success_evaluated": False, "scene_validation_passed": True},
    }
    atomic_json(meta, native)
    atomic_json(
        run / "stage_report.json",
        {"schema": "dexagent.step_report.v1", "status": "passed"},
    )
    index = Index(tmp_path, tmp_path / "runs", tmp_path / "state")
    index.refresh()
    key = index.runs()[0].id
    assert index.episodes(key)[0].success is None
    assert index.episode_catalog(success="passed") == []
    assert index.episode_catalog(success="failed") == []
    assert json.loads(meta.read_text()) == native
