"""Import qualification must reject lost identities and physical semantics."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent.geniesim_import import (
    builder_inventory, compare_joints, compare_physics, qualify,
    source_inventory, unchanged_files_check,
)
from agent.run_reports import sha256_file


def joint(path="/robot/driver", **kwargs):
    return {"path": path, "kind": "PhysicsRevoluteJoint", "enabled": True,
            "loop": False, "lower": 0.0, "upper": 0.7853981633974483,
            "mimics": [], **kwargs}


def failed(checks):
    return {c["id"] for c in checks if c["status"] == "failed"}


def test_equal_counts_with_different_names_do_not_pass():
    checks = compare_joints([joint()], [joint("/other/driver")], [])
    assert "named_joints_preserved" in failed(checks)


def test_duplicate_and_disabled_joints_fail():
    assert "named_joints_preserved" in failed(compare_joints([joint()], [joint(), joint()], []))
    assert "named_joints_preserved" in failed(compare_joints([joint()], [joint(enabled=False)], []))


def test_wrong_body_binding_fails():
    src = joint(body0=["/robot/wrist"], body1=["/robot/finger"])
    dst = joint(body0=["/robot/other"], body1=["/robot/finger"])
    assert "joint_types_limits_loops_preserved" in failed(compare_joints([src], [dst], []))


def test_lost_loop_and_degree_radian_confusion_fail():
    src = joint(loop=True)
    dst = joint(upper=45)
    checks = compare_joints([src], [dst], [])
    assert "joint_types_limits_loops_preserved" in failed(checks)
    assert {e["field"] for e in checks[1]["evidence"]} == {"loop", "upper"}


@pytest.mark.parametrize("change", ["missing", "wrong_sign", "disabled", "wrong_leader"])
def test_mimic_must_preserve_driver_sign_and_enabled_constraint(change):
    src = joint("/robot/follower", mimics=[
        {"leader": "/robot/driver", "coef0": 0.0, "coef1": -1.0}])
    mimic = {"follower": src["path"], "leader": "/robot/driver",
             "coef0": 0.0, "coef1": -1.0, "enabled": True}
    if change == "wrong_sign":
        mimic["coef1"] = 1.0
    elif change == "disabled":
        mimic["enabled"] = False
    elif change == "wrong_leader":
        mimic["leader"] = "/other/driver"
    checks = compare_joints([src], [src], [] if change == "missing" else [mimic])
    assert "driver_follower_constraints_preserved" in failed(checks)


def test_matching_mimic_passes():
    src = joint("/robot/follower", mimics=[
        {"leader": "/robot/driver", "coef0": 0.0, "coef1": -1.0}])
    mimic = {"follower": src["path"], "leader": "/robot/driver",
             "coef0": 0.0, "coef1": -1.0, "enabled": True}
    assert not failed(compare_joints([src], [src], [mimic]))


def test_mass_inertia_and_collision_loss_fail():
    src = {"bodies": [{"path": "/body", "mass": 2.0, "diagonal_inertia": [1, 2, 3]}],
           "collisions": [{"path": "/body/collision"}]}
    dst = {"bodies": [{"path": "/body", "mass": 1.0,
                      "inertia": [[1, 0, 0], [0, 2, 0], [0, 0, 4]]}],
           "collisions": [{"path": "/body/visual"}]}
    assert failed(compare_physics(src, dst)) == {
        "authored_mass_principal_inertia_preserved", "declared_colliders_preserved"}


def test_inertia_comparison_respects_principal_axis_rotation():
    src = {"bodies": [{"path": "/body", "mass": 2.0, "diagonal_inertia": [1, 2, 3]}],
           "collisions": [{"path": "/body/collision"}]}
    dst = {"bodies": [{"path": "/body", "mass": 2.0,
                      "inertia": [[2, 0, 0], [0, 1, 0], [0, 0, 3]]}],
           "collisions": [{"path": "/body/collision/mesh"}]}
    assert not failed(compare_physics(src, dst))
    broken = deepcopy(dst)
    broken["bodies"] = []
    assert "rigid_bodies_preserved" in failed(compare_physics(src, broken))


def test_source_inventory_converts_degrees_and_reads_unregistered_physx_mimic():
    pytest.importorskip("pxr")
    from pxr import Sdf, Usd, UsdPhysics

    stage = Usd.Stage.CreateInMemory()
    driver = UsdPhysics.RevoluteJoint.Define(stage, "/robot/driver")
    driver.CreateLowerLimitAttr(0)
    driver.CreateUpperLimitAttr(45)
    follower = UsdPhysics.RevoluteJoint.Define(stage, "/robot/follower")
    follower.GetPrim().CreateRelationship(
        "physxMimicJoint:rotZ:referenceJoint").SetTargets([driver.GetPath()])
    follower.GetPrim().CreateAttribute(
        "physxMimicJoint:rotZ:gearing", Sdf.ValueTypeNames.Float).Set(1)
    inv = source_inventory(stage)
    assert inv["joints"][0]["upper"] == pytest.approx(0.7853981633974483)
    assert inv["joints"][1]["mimics"] == [
        {"leader": "/robot/driver", "coef0": 0.0, "coef1": -1.0}]


def test_position_target_uses_q_layout_after_floating_base():
    newton = pytest.importorskip("newton")
    builder = SimpleNamespace(
        joint_label=["/base", "/driver"],
        joint_type=[newton.JointType.FREE, newton.JointType.REVOLUTE],
        joint_q_start=[0, 7], joint_qd_start=[0, 6],
        joint_articulation=[0, 0], joint_enabled=[True, True],
        joint_limit_lower=[0] * 7, joint_limit_upper=[1] * 7,
        joint_target_q=[0, 0, 0, 0, 0, 0, 1, 0.4],
        joint_parent=[-1, -1], joint_child=[-1, -1],
        joint_q=[0] * 8, body_label=[], constraint_mimic_joint0=[],
        shape_label=[], shape_count=0,
    )
    assert builder_inventory(builder)["joints"][1]["target"] == 0.4


def test_missing_file_after_identity_capture_is_a_failed_check(tmp_path):
    source = tmp_path / "robot.usda"
    source.write_text("#usda 1.0\n")
    identity = {"path": str(source), "sha256": sha256_file(source)}
    source.unlink()
    result = unchanged_files_check("source_layers_unchanged", [identity])
    assert result["status"] == "failed"
    assert result["evidence"][0]["path"] == str(source)


def test_missing_source_still_writes_a_failed_report(tmp_path):
    output = tmp_path / "report.json"
    report = qualify(tmp_path / "absent.usda", output)
    assert report["status"] == "failed"
    assert report["task_success_evaluated"] is False
    assert output.exists()


def test_usd_zero_mass_means_unspecified_not_required_zero_imported_mass():
    src = {"bodies": [{"path": "/body", "mass": 0.0, "diagonal_inertia": None}], "collisions": []}
    dst = {"bodies": [{"path": "/body", "mass": 2.0, "inertia": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}],
           "collisions": []}
    assert not failed(compare_physics(src, dst))
