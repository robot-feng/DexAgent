"""Rank actual joint acceptance above a lift-only success or an untested prior."""
import importlib

import pytest

from tool_library.skills.human_grasp_seeds import SEED_TESTS, _verify_seeds
from tool_library.tests import grasp_robust_fixtures as F


def stub_verifiers(monkeypatch):
    calls = []

    def lift(scene, pose, lift_heights_m=(.06, .10, .15)):
        calls.append((pose["name"], "lift", tuple(lift_heights_m)))
        if pose["name"] == "exception":
            raise RuntimeError("simulation failed")
        ok = pose["name"] != "failed_lift"
        return {"ok": ok, "first_fail": None if ok else "lift_0.15",
                "lifts": [{"commanded_mm": h * 1000, "achieved_mm": h * 1000 if ok else 0}
                          for h in lift_heights_m]}

    def hold(scene, pose, hold_s=.8, slip_max_mm_s=10., lift_m=.08, maintain_N=0.):
        calls.append((pose["name"], "hold", hold_s, slip_max_mm_s, lift_m, maintain_N))
        ok = pose["name"] == "joint_pass"
        return {"ok": ok, "first_fail": None if ok else "lift_0.08", "slip_mm_s": 0.,
                "fingers": 2 if ok else 0, "contact_force_N": 10. if ok else 0.}

    monkeypatch.setattr(importlib.import_module("tool_library.verifiers.grasp_lift_test"), "grasp_lift_test", lift)
    monkeypatch.setattr(importlib.import_module("tool_library.verifiers.grasp_hold_test"), "grasp_hold_test", hold)
    return calls


def test_lift_only_is_failed_and_joint_pass_ranks_first(monkeypatch):
    calls = stub_verifiers(monkeypatch)
    seeds = [{"name": name, "seed_score": score} for name, score in
             [("lift_only", 1), ("joint_pass", 20), ("untested", 0)]]
    _verify_seeds({}, seeds, 2)
    assert seeds[0]["name"] == "joint_pass"
    by_name = {p["name"]: p for p in seeds}
    assert by_name["joint_pass"]["verification"]["status"] == "passed"
    assert by_name["lift_only"]["lift"]["ok"] is True
    assert by_name["lift_only"]["hold"]["ok"] is False
    assert by_name["lift_only"]["verification"]["first_fail"] == "grasp_hold_test:lift_0.08"
    assert by_name["lift_only"]["verification"]["status"] == "failed"
    assert by_name["untested"]["verification"]["status"] == "not_tested"
    assert by_name["untested"]["stress_tests"] == {}
    assert calls == [
        ("lift_only", "lift", (.06, .10, .15)), ("lift_only", "hold", .8, 10., .08, 0.),
        ("joint_pass", "lift", (.06, .10, .15)), ("joint_pass", "hold", .8, 10., .08, 0.),
    ]


@pytest.mark.parametrize("name", ["failed_lift", "exception"])
def test_failed_lift_or_simulation_error_never_implies_a_hold_pass(monkeypatch, name):
    calls = stub_verifiers(monkeypatch)
    seeds = [{"name": name, "seed_score": 0}]
    _verify_seeds({}, seeds, 1)
    pose = seeds[0]
    assert pose["verification"]["status"] == "failed"
    assert pose["verification"]["required_tests"] == list(SEED_TESTS)
    assert pose["verification"]["completed_tests"] == ["grasp_lift_test"]
    assert "hold" not in pose
    assert "grasp_hold_test" not in pose["stress_tests"]
    assert len(calls) == 1
    if name == "exception":
        assert "RuntimeError: simulation failed" in pose["verification"]["first_fail"]


def test_zero_budget_removes_stale_success_and_calls_no_physics(monkeypatch):
    calls = stub_verifiers(monkeypatch)
    seeds = [{"name": "joint_pass", "seed_score": 0, "lift": {"ok": True}, "hold": {"ok": True},
              "stress_tests": {name: {"ok": True} for name in SEED_TESTS},
              "verification": {"status": "passed"}}]
    _verify_seeds({}, seeds, 0)
    assert not calls
    assert seeds[0]["verification"]["status"] == "not_tested"
    assert seeds[0]["stress_tests"] == {}
    assert "lift" not in seeds[0] and "hold" not in seeds[0]


@pytest.mark.parametrize("budget", [-1, 1.5, True])
def test_invalid_verification_budget_rejected(budget):
    with pytest.raises(ValueError, match="verify_top"):
        _verify_seeds({}, [], budget)


def test_real_sharpa_pose_passes_both_default_physical_tests():
    pose = dict(F.pose(F.BOX_PINCH3), seed_score=0)
    _verify_seeds(F.scene(), [pose], 1)
    assert pose["verification"]["status"] == "passed", pose["stress_tests"]
    assert pose["lift"]["tested_heights_m"] == [.06, .10, .15]
    assert pose["hold"]["fingers"] >= 1
    assert set(pose["stress_tests"]) == set(SEED_TESTS)
    assert all(report["ok"] and report["penetration"]["ok"] for report in pose["stress_tests"].values())
