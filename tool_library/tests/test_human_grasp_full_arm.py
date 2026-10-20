"""Recover synthetic human seeds and execute on the original fixed YAM + Sharpa.

This transferable-task regression covers one embodiment only. It neither
qualifies G1/G2 nor represents a recorded HumanEgo task or faithful Real2Sim.
No successful pose or trajectory is loaded: current tools must generate,
screen, physically execute and independently verify the result.
"""
import copy
import hashlib
import json
from pathlib import Path

import mujoco
import numpy as np

from tool_library.common import armhand as AH
from tool_library.common import episode as E
from tool_library.common import rigid_robot as RR
from tool_library.skills.execute_subgoal_plan import execute_subgoal_plan
from tool_library.skills.hand_scene_from_recon import hand_scene_from_recon
from tool_library.skills.optimize_grasp_pose import optimize_grasp_pose
from tool_library.skills.stage_on_bench import stage_on_bench
from tool_library.verifiers.contact_impulse_spike_audit import contact_impulse_spike_audit
from tool_library.verifiers.grasp_hold_test import grasp_hold_test
from tool_library.verifiers.grasp_lift_test import grasp_lift_test
from tool_library.verifiers.held_object_slip_audit import held_object_slip_audit
from tool_library.verifiers.object_visual_penetration_audit import object_visual_penetration_audit
from tool_library.verifiers.physics_recording_check import physics_recording_check
from tool_library.verifiers.rigid_arm_hand_check import rigid_arm_hand_check
from tool_library.verifiers.swept_collision_audit import swept_collision_audit


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures" / "human_grasp_recovery.json"


def _save(path, value):
    path.write_text(json.dumps(value, indent=2, default=str))


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_failed_human_seeds_recover_on_fixed_arm_and_pass_physical_audits(tmp_path, monkeypatch):
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("MUJOCO_GL", "egl")
    fixture = json.loads(FIXTURE.read_text())
    case = fixture["cases"]["box_right"]
    starts = copy.deepcopy(case["starts"])
    obj = case["object"]
    asset = tmp_path / "box.xml"
    asset.write_text(case["object_xml"])
    scene = tmp_path / "scene.xml"
    placement = dict(
        name=obj["name"], xml=str(asset), x=obj["pos"][0],
        y=obj["pos"][1], z=obj["pos"][2], yaw_deg=obj["yaw_deg"],
    )
    stage_on_bench(
        str(ROOT / "setup/scene.xml"), [placement], str(scene),
        rig={"origin": [0, 0, 0], "R": np.eye(3).tolist()},
    )
    recon = tmp_path / "scene_recon.json"
    _save(recon, {
        "scene_xml": "scene.xml",
        "objects": [{
            "id": "box", "type": "rigid", "asset_xml": "box.xml",
            "root_body": "box_box", "bodies": ["box_box"],
            "pose": {key: placement[key] for key in ("x", "y", "z", "yaw_deg")},
        }],
    })
    hand_scene = tmp_path / "hand_scene.json"
    hand_scene_from_recon(str(recon), "right", "box", out_json=str(hand_scene))

    # Preserve the existing rig frame, fixed station bases and primitive pose.
    original = mujoco.MjModel.from_xml_path(str(ROOT / "setup/scene.xml"))
    model, _ = E.build_execution_model(str(scene))
    for side in ("left", "right"):
        name = f"robot_{side}_arm_root"
        np.testing.assert_array_equal(model.body(name).pos, original.body(name).pos)
        np.testing.assert_array_equal(model.body(name).quat, original.body(name).quat)
    q = model.key_qpos[0].copy()
    data = mujoco.MjData(model)
    data.qpos[:] = q
    mujoco.mj_forward(model, data)
    np.testing.assert_allclose(data.body("box_box").xpos, obj["pos"], atol=1e-12)
    expected_quat = [np.cos(np.deg2rad(obj["yaw_deg"]) / 2), 0, 0,
                     np.sin(np.deg2rad(obj["yaw_deg"]) / 2)]
    # MuJoCo's saved MJCF rounds quaternion components to six significant
    # digits. Account for serialization precision, not physical gate tolerance.
    np.testing.assert_allclose(data.body("box_box").xquat, expected_quat, rtol=0, atol=1e-6)
    waypoints = [
        {"object": "box_box", "target_pos": (np.asarray(obj["pos"]) + [0, 0, h]).tolist(), "steps": 8}
        for h in (0.06, 0.10, 0.15)
    ]
    probe = AH.ArmProbe(str(scene))
    baseline = {
        "arm": AH.screen_poses(model, "right", starts, q, probe=probe, waypoints=waypoints),
        "lift": grasp_lift_test(str(hand_scene), starts[0]),
        "hold": grasp_hold_test(str(hand_scene), starts[0]),
    }
    _save(tmp_path / "baseline_validation.json", baseline)
    assert not baseline["arm"][0]["ok"], baseline
    assert not (baseline["lift"]["ok"] and baseline["hold"]["ok"]), baseline

    budget = {**fixture["budget"], "family": "rigid", "max_starts": 6}
    _save(tmp_path / "task_evidence.json", {
        "objective": "transferable_task_generation",
        "robot": "I2RT YAM + Sharpa Wave",
        "source": "synthetic box_right initial failed human seeds",
        "aggregate_complete": False,
        "other_robots": {"G1_omnipicker": "missing", "G2_omnipicker": "missing"},
        "budget": budget, "arm_waypoints": waypoints,
    })
    recovered = optimize_grasp_pose(
        str(hand_scene), starts=starts, bench_scene=str(scene), arm_waypoints=waypoints,
        out_json=str(tmp_path / "recovered_pose.json"), **budget,
    )
    _save(tmp_path / "optimizer_report.json", recovered)
    assert recovered["complete"] and recovered["ok"], recovered
    assert 0 < recovered["n_evals"] <= budget["max_evals"]
    assert recovered["n_tested"] > 0
    assert recovered["tests"] == ["grasp_lift_test", "grasp_hold_test"]
    assert recovered["passing"]
    checks = []
    for pose in recovered["passing"]:
        checks.append({
            "arm": AH.screen_poses(model, "right", [pose], q, probe=probe, waypoints=waypoints)[0],
            "lift": grasp_lift_test(str(hand_scene), pose),
            "hold": grasp_hold_test(str(hand_scene), pose),
        })
    _save(tmp_path / "independent_candidates.json", checks)
    assert all(c["arm"]["ok"] and c["lift"]["ok"] and c["hold"]["ok"] for c in checks), checks

    poses = tmp_path / "qualified_candidates.json"
    _save(poses, recovered["passing"])
    plan = {
        "scene_recon_json": str(recon),
        "subgoals": [{
            "index": 1, "motion": "grasp_lift", "side": "right", "object": "box_box",
            "pose": str(poses), "hand_scene": str(hand_scene), "lift_m": 0.15,
            "lift_path": [[0, 0, 0.06], [0, 0, 0.10], [0, 0, 0.15]],
        }],
    }
    _save(tmp_path / "plan.json", plan)
    episode = tmp_path / "episode"
    result = execute_subgoal_plan(str(scene), plan, str(episode))
    _save(tmp_path / "execution_result.json", result)
    assert result["ok"], result.get("first_failure")
    assert not result["dry_run"]
    assert result["robot_schema"] == "dexagent.rigid_robot.v1"
    runtime_path = episode / "scene_physics.xml"
    trajectory = episode / "trajectory.npz"
    runtime = mujoco.MjModel.from_xml_path(str(runtime_path))
    assert RR.is_rigid(runtime)
    assert runtime.nq == model.nq - 12  # Proposal wrist freedoms do not exist in execution.
    for side in ("left", "right"):
        name = f"robot_{side}_arm_root"
        np.testing.assert_array_equal(runtime.body(name).pos, original.body(name).pos)
        np.testing.assert_allclose(runtime.body(name).quat, original.body(name).quat, atol=1e-12)
    assert result["scene_sha256"] == _sha256(runtime_path)
    assert result["trajectory_sha256"] == _sha256(trajectory)

    # Recheck the selected/reanchored pose in the executor's exact starting scene.
    selected = result["subgoals"][0]["selected_pose_evidence"]
    assert selected["runtime_scene_sha256"] == _sha256(runtime_path)
    assert selected["sha256"] == _sha256(selected["path"])
    assert selected["hand_scene_used_sha256"] == _sha256(selected["hand_scene_used"])
    assert selected["start_state_proposal_sha256"] == _sha256(selected["start_state_proposal"])
    pose = json.loads(Path(selected["path"]).read_text())
    selected_checks = {
        "lift": grasp_lift_test(selected["hand_scene_used"], pose),
        "hold": grasp_hold_test(selected["hand_scene_used"], pose),
        "pose_sha256": _sha256(selected["path"]),
        "hand_scene_sha256": _sha256(selected["hand_scene_used"]),
    }
    _save(tmp_path / "selected_pose_stress_validation.json", selected_checks)
    assert selected_checks["lift"]["ok"] and selected_checks["hold"]["ok"], selected_checks
    assert [v["commanded_mm"] for v in selected_checks["lift"]["lifts"]] == [60.0, 100.0, 150.0]

    audits = {}
    for verifier in (
        rigid_arm_hand_check, physics_recording_check, swept_collision_audit,
        object_visual_penetration_audit, contact_impulse_spike_audit, held_object_slip_audit,
    ):
        kwargs = dict(scene=str(runtime_path), trajectory=str(trajectory))
        if verifier is physics_recording_check:
            kwargs["scene_recon_json"] = str(recon)
        if verifier is held_object_slip_audit:
            kwargs["words"] = ["lift", "carry", "transport", "pour", "tilt", "set down"]
        audits[verifier.__name__] = verifier(**kwargs)
        _save(tmp_path / "audits.json", audits)
    assert all(report["ok"] for report in audits.values()), audits
