"""Small native USD regressions for the Omnipicker collision import boundary."""
import os

import pytest

from agent import geniesim_import


@pytest.fixture
def native():
    pytest.importorskip("pxr")
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    wp = pytest.importorskip("warp")
    wp.config.enable_cuda = False
    newton = pytest.importorskip("newton")
    from newton.solvers import SolverMuJoCo
    from newton.usd import SchemaResolverNewton, SchemaResolverPhysx
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    return (newton, SolverMuJoCo, SchemaResolverNewton, SchemaResolverPhysx,
            Gf, Sdf, Usd, UsdGeom, UsdPhysics)


def make_stage(tmp_path, native, *, physx=False, newton_override=None,
               root_on_fixed_joint=False):
    """Three articulated cubes, plus a separate sphere touching the last cube."""
    _, _, _, _, Gf, Sdf, Usd, UsdGeom, UsdPhysics = native
    path = tmp_path / "robot.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    root = UsdGeom.Xform.Define(stage, "/Robot").GetPrim()
    stage.SetDefaultPrim(root)
    def body(path, position):
        transform = UsdGeom.Xform.Define(stage, path)
        transform.AddTranslateOp().Set(Gf.Vec3d(*position))
        prim = transform.GetPrim()
        UsdPhysics.RigidBodyAPI.Apply(prim)
        mass = UsdPhysics.MassAPI.Apply(prim)
        mass.CreateMassAttr(1.0)
        mass.CreateDiagonalInertiaAttr(Gf.Vec3f(0.01))
        return prim

    for i in range(3):
        body(f"/Robot/link{i}", (i * 0.25, 0, 0.2))
        cube = UsdGeom.Cube.Define(stage, f"/Robot/link{i}/collision")
        cube.CreateSizeAttr(0.1)
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    fixed = UsdPhysics.FixedJoint.Define(stage, "/Robot/fixed")
    fixed.CreateBody1Rel().SetTargets(["/Robot/link0"])
    fixed.CreateLocalPos0Attr(Gf.Vec3f(0, 0, 0.2))
    articulation_root = fixed.GetPrim() if root_on_fixed_joint else root
    UsdPhysics.ArticulationRootAPI.Apply(articulation_root)
    articulation_root.CreateAttribute("physxArticulation:enabledSelfCollisions",
                                      Sdf.ValueTypeNames.Bool).Set(physx)
    if newton_override is not None:
        articulation_root.CreateAttribute("newton:selfCollisionEnabled",
                                          Sdf.ValueTypeNames.Bool).Set(newton_override)
    for i in range(2):
        joint = UsdPhysics.RevoluteJoint.Define(stage, f"/Robot/joint{i}")
        joint.CreateBody0Rel().SetTargets([f"/Robot/link{i}"])
        joint.CreateBody1Rel().SetTargets([f"/Robot/link{i + 1}"])
        joint.CreateLocalPos0Attr(Gf.Vec3f(0.25, 0, 0))
        joint.CreateAxisAttr("Z")
        joint.CreateLowerLimitAttr(-45)
        joint.CreateUpperLimitAttr(45)
    body("/External", (0.5, 0.06, 0.2))
    sphere = UsdGeom.Sphere.Define(stage, "/External/collision")
    sphere.CreateRadiusAttr(0.025)
    UsdPhysics.CollisionAPI.Apply(sphere.GetPrim())
    stage.GetRootLayer().Save()
    return path, stage


def import_stage(stage, native):
    newton, SolverMuJoCo, NewtonResolver, PhysxResolver, *_ = native
    builder = newton.ModelBuilder()
    SolverMuJoCo.register_custom_attributes(builder)
    builder.add_usd(
        stage, collapse_fixed_joints=False,
        schema_resolvers=[NewtonResolver(), PhysxResolver()],
    )
    return builder


def shape_ids(builder):
    return {path: i for i, path in enumerate(builder.shape_label)}


def filtered(builder, a, b):
    ids = shape_ids(builder)
    pair = tuple(sorted((ids[a], ids[b])))
    return pair in {tuple(sorted(p)) for p in builder.shape_collision_filter_pairs}


@pytest.mark.parametrize(
    "physx,newton_override,expected",
    [(False, None, False), (True, None, True), (False, True, True)],
)
def test_native_qualification_preserves_collision_semantics(
        tmp_path, native, physx, newton_override, expected):
    path, stage = make_stage(
        tmp_path, native, physx=physx, newton_override=newton_override)
    report = geniesim_import.qualify(path, tmp_path / "report.json")
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["native_rss_import"]["status"] == "passed"
    assert checks["native_cpu_solver_build"]["status"] == "passed"
    assert checks["authored_self_collision_filters_preserved"]["status"] == "passed"
    root = next(a for a in report["source_inventory"]["articulations"]
                if a["path"] == "/Robot")
    assert root["self_collision_enabled"] is expected
    assert root["attribute"] == (
        "newton:selfCollisionEnabled" if newton_override is not None
        else "physxArticulation:enabledSelfCollisions")
    assert report["task_success_evaluated"] is False

    # Inspect real imported filters, including the non-adjacent pair that
    # ordinary parent-child filtering would leave collidable.
    builder = import_stage(stage, native)
    assert filtered(builder, "/Robot/link0/collision",
                    "/Robot/link2/collision") is (not expected)
    if not expected:
        assert filtered(builder, "/Robot/link0/collision", "/Robot/link1/collision")
        assert filtered(builder, "/Robot/link1/collision", "/Robot/link2/collision")
    for i in range(3):
        assert not filtered(builder, f"/Robot/link{i}/collision", "/External/collision")

    # Solver construction performs native collision detection, without stepping.
    # Assert a real external contact, not only the absence of an exclusion.
    _, SolverMuJoCo, *_ = native
    solver = SolverMuJoCo(builder.finalize(device="cpu"), use_mujoco_cpu=True)
    external_contacts = []
    for contact in solver.mj_data.contact:
        names = [solver.mj_model.geom(int(g)).name
                 for g in (contact.geom1, contact.geom2)]
        if any("/External/collision" in name for name in names):
            external_contacts.append(names)
    assert external_contacts, "The external sphere must still collide with the robot"


def test_report_rejects_missing_nonadjacent_filter(tmp_path, native):
    _, stage = make_stage(tmp_path, native, physx=False)
    builder = import_stage(stage, native)
    source = geniesim_import.source_inventory(stage)
    imported = geniesim_import.builder_inventory(builder)
    pairs = {tuple(sorted(pair)) for pair in builder.shape_collision_filter_pairs}
    intact = geniesim_import.compare_collision_filters(source, imported, pairs)
    assert intact["status"] == "passed"
    assert intact["evidence"][0]["expected_filtered_pairs"] == 3
    assert intact["evidence"][0]["missing_filtered_pairs"] == 0
    ids = shape_ids(builder)
    pairs.remove(tuple(sorted((ids["/Robot/link0/collision"],
                               ids["/Robot/link2/collision"]))))
    broken = geniesim_import.compare_collision_filters(source, imported, pairs)
    assert broken["id"] == "authored_self_collision_filters_preserved"
    assert broken["status"] == "failed"
    assert broken["required"] is True
    assert broken["evidence"][0]["expected_filtered_pairs"] == 3
    assert broken["evidence"][0]["missing_filtered_pairs"] == 1
    assert {frozenset(pair) for pair in broken["evidence"][0]["examples"]} == {
        frozenset(("/Robot/link0/collision", "/Robot/link2/collision"))}


def test_joint_root_membership_uses_native_articulation_graph(tmp_path, native):
    path, stage = make_stage(tmp_path, native, root_on_fixed_joint=True)
    source = geniesim_import.source_inventory(stage)
    root = next(a for a in source["articulations"] if a["path"] == "/Robot/fixed")
    assert root["self_collision_enabled"] is False
    assert set(root["articulated_bodies"]) == {
        "/Robot/link0", "/Robot/link1", "/Robot/link2"}
    assert "/External" not in root["articulated_bodies"]
    assert all(not body.startswith(root["path"] + "/")
               for body in root["articulated_bodies"])

    builder = import_stage(stage, native)
    imported = geniesim_import.builder_inventory(builder)
    pairs = {tuple(sorted(pair)) for pair in builder.shape_collision_filter_pairs}
    result = geniesim_import.compare_collision_filters(source, imported, pairs)
    assert result["status"] == "passed"
    assert result["evidence"][0]["expected_filtered_pairs"] == 3
    assert result["evidence"][0]["missing_filtered_pairs"] == 0
    ids = shape_ids(builder)
    pairs.remove(tuple(sorted((ids["/Robot/link0/collision"],
                               ids["/Robot/link2/collision"]))))
    broken = geniesim_import.compare_collision_filters(source, imported, pairs)
    assert broken["status"] == "failed"
    assert broken["evidence"][0]["missing_filtered_pairs"] == 1

    report = geniesim_import.qualify(path, tmp_path / "joint_root_report.json")
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["native_cpu_solver_build"]["status"] == "passed"
    assert checks["authored_self_collision_filters_preserved"]["status"] == "passed"
    assert checks["authored_self_collision_filters_preserved"][
        "evidence"][0]["expected_filtered_pairs"] == 3


@pytest.mark.parametrize("membership", [None, []])
def test_report_rejects_unverified_articulation_membership(tmp_path, native, membership):
    _, stage = make_stage(tmp_path, native)
    source = geniesim_import.source_inventory(stage)
    builder = import_stage(stage, native)
    imported = geniesim_import.builder_inventory(builder)
    root = next(a for a in source["articulations"] if a["path"] == "/Robot")
    if membership is None:
        root.pop("articulated_bodies", None)
    else:
        root["articulated_bodies"] = membership
    result = geniesim_import.compare_collision_filters(
        source, imported, builder.shape_collision_filter_pairs)
    assert result["status"] == "failed"
    assert result["required"] is True
