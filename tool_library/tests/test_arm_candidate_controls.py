"""Geometric reach reuse must not discard independent physical control variants."""
import copy
from pathlib import Path
from unittest.mock import patch

import mujoco
import pytest

from tool_library.common import armhand as AH, hand as H, plan as P
from tool_library.skills.execute_subgoal_plan import _screen_entry_poses


@pytest.fixture
def bench():
    model = mujoco.MjModel.from_xml_path(str(Path(__file__).resolve().parents[2] / "setup/scene.xml"))
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, 0)
    mujoco.mj_forward(model, data)
    bid = AH._parts(model)["right"]["ee_body"]
    rig = P.scene_rigs(model)["right"]
    pose = {"side": "right", "pos": data.xpos[bid].tolist(),
            "euler": H.mat_to_euler(data.xmat[bid].reshape(3, 3)).tolist(),
            "finger_targets": rig.finger_targets(.3), "approach": [0, 0, 1],
            "press_depth_rad": .2}
    return model, data.qpos.copy(), pose


def test_same_wrist_different_press_controls_survive_executor_screen(bench):
    model, q, pose = bench
    controls = [pose, dict(pose, press_depth_rad=.152), dict(pose, open_frac=.3)]
    original = copy.deepcopy(controls)
    result = _screen_entry_poses(model, "right", controls, {"motion": "grasp_lift", "approach_m": 0}, q)
    assert [r["index"] for r in result if r["ok"]] == [0, 1, 2]
    assert all("duplicate_of" not in r for r in result)
    assert [r.get("kinematic_reuse_of") for r in result] == [None, 0, 0]
    assert controls == original  # No overwrite or merging of physical controls.
    assert all(r["residual_mm"] < .5 and r["residual_deg"] < .3 for r in result)


def test_nearby_geometry_does_not_share_a_tolerance_based_verdict(bench):
    model, q, pose = bench
    shifted = copy.deepcopy(pose)
    shifted["pos"][0] += .0005  # Less than the former 1 mm deduplication threshold.
    solve = AH.ArmSide.solve
    calls = []

    def counted(self, *args, **kwargs):
        calls.append(1)
        return solve(self, *args, **kwargs)

    with patch.object(AH.ArmSide, "solve", counted):
        result = AH.screen_poses(model, "right", [pose, shifted], q, approach_m=0)
    assert all("kinematic_reuse_of" not in r for r in result)
    assert len(calls) == 4  # Each pose solves pre-grasp and grasp independently.


def test_reused_collision_failure_keeps_candidate_and_owns_its_report(bench):
    model, q, pose = bench

    class Collision:
        # Inject a diagnostic collision to check result reuse; this is not an
        # assertion about real geometry or a passing physical trajectory.
        def check(self, *args, **kwargs):
            return [{"geoms": ["arm_link", "obstacle"], "depth_mm": 5.}]

    result = AH.screen_poses(model, "right", [pose, dict(pose, press_depth_rad=.152)],
                             q, approach_m=0, probe=Collision())
    assert [r["index"] for r in result] == [0, 1]
    assert not any(r["ok"] for r in result)
    assert all("residual_mm" in r and "duplicate_of" not in r for r in result)
    assert result[1]["kinematic_reuse_of"] == 0
    assert result[0]["failed_at"] == result[1]["failed_at"] == "pre-grasp"
    result[1]["arm_hits"][0]["depth_mm"] = 99
    assert result[0]["arm_hits"][0]["depth_mm"] == 5


def test_reuse_indices_remain_correct_after_unreadable_candidate(bench):
    model, q, pose = bench
    result = AH.screen_poses(model, "right", [{}, pose, dict(pose, press_depth_rad=.152)], q, approach_m=0)
    assert not result[0]["ok"]
    assert result[1]["ok"] and result[2]["ok"]
    assert result[2]["kinematic_reuse_of"] == 1
