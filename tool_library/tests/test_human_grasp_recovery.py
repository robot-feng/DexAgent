"""Recover failed synthetic human seeds using the actual Sharpa model and physics.

This is a hand-only MuJoCo regression, not whole-arm or multi-robot acceptance.
No recorded successful pose is loaded: the current optimizer must find a grasp
within its original bounded budget, then independent default verifiers judge it.
"""
import copy
import json
from pathlib import Path

import pytest

from tool_library.skills.optimize_grasp_pose import optimize_grasp_pose
from tool_library.verifiers.grasp_hold_test import grasp_hold_test
from tool_library.verifiers.grasp_lift_test import grasp_lift_test


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures" / "human_grasp_recovery.json"


@pytest.mark.parametrize("case", ["box_right", "tall_box_right_hamer"])
def test_failed_human_seed_is_recovered_with_default_lift_and_hold(case, tmp_path, monkeypatch):
    # Limit numerical-library worker threads, not the physical test or search.
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("MUJOCO_GL", "egl")
    fixture = json.loads(FIXTURE.read_text())
    data = fixture["cases"][case]
    object_xml = tmp_path / f"{case}.xml"
    object_xml.write_text(data["object_xml"])
    scene = {
        "hand_xml": str(ROOT / data["hand_xml"]),
        "side": data["side"],
        "floor": data["floor"],
        "objects": [{**data["object"], "xml": str(object_xml)}],
    }
    starts = copy.deepcopy(data["starts"])
    # Input poses may carry results from an earlier evaluation. A physically
    # changed candidate must not retain those results as its own acceptance.
    for start in starts:
        start.update(
            lift={"ok": False, "first_fail": "stale_fixture_lift"},
            hold={"ok": False, "first_fail": "stale_fixture_hold"},
            verification={"ok": False, "source": "stale_fixture_verification"},
            stress_tests={
                "grasp_lift_test": {"ok": False, "first_fail": "stale_fixture_test"},
                "grasp_hold_test": {"ok": False, "first_fail": "stale_fixture_test"},
            },
        )
    baseline_lift = grasp_lift_test(scene, starts[0])
    baseline_hold = grasp_hold_test(scene, starts[0])
    baseline = {"lift": baseline_lift, "hold": baseline_hold}
    (tmp_path / "baseline_validation.json").write_text(json.dumps(baseline, indent=2))
    assert not (baseline_lift["ok"] and baseline_hold["ok"]), baseline

    # Use current repository code and both default physical tests. In particular,
    # do not supply shorter lift heights, relaxed gates or a recorded passing pose.
    result = optimize_grasp_pose(
        scene, starts=starts, out_json=str(tmp_path / "recovered_pose.json"),
        **fixture["budget"],
    )
    (tmp_path / "optimizer_report.json").write_text(json.dumps(result, indent=2))
    assert result["complete"] and result["status"] == "passed" and result["ok"], result
    assert 0 < result["n_evals"] <= fixture["budget"]["max_evals"]
    assert result["n_tested"] > 0
    assert result["tests"] == ["grasp_lift_test", "grasp_hold_test"]
    assert result["pose"] and result["passing"]

    # Re-run each returned pass independently, including the separately exposed
    # best pose. Cached optimizer success flags are not accepted as test evidence.
    checks = []
    for label, pose in [("best", result["pose"])] + [
        (f"passing_{i}", pose) for i, pose in enumerate(result["passing"])
    ]:
        assert not {"lift", "hold", "verification"}.intersection(pose), pose
        assert set(pose["stress_tests"]) == {"grasp_lift_test", "grasp_hold_test"}
        assert all(value["ok"] for value in pose["stress_tests"].values()), pose
        lift = grasp_lift_test(scene, pose)
        hold = grasp_hold_test(scene, pose)
        checks.append({"candidate": label, "lift": lift, "hold": hold})
    (tmp_path / "independent_validation.json").write_text(json.dumps(checks, indent=2))
    for check in checks:
        assert check["lift"]["ok"] and check["hold"]["ok"], check
        assert [step["commanded_mm"] for step in check["lift"]["lifts"]] == [60.0, 100.0, 150.0]
