"""CPU qualification of the exact Omnipicker USDs through RSS's native importer.

This records import fidelity, never demo/task success. Run in an environment
with Newton's USD importer dependencies; the normal DexAgent environment need
not be changed. Source assets are read-only, including optional session edits.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.metadata
import math
import os
from pathlib import Path
import sys
import time
import traceback
import warnings

from agent.run_reports import atomic_json, make_report, sha256_file, status_for_checks


ROBOTS = ("G1_omnipicker", "G2_omnipicker")
MISSING_CAMERA = "/G01/head_link2/Top_Camera/OmniverseKitViewportCameraMesh"


def check(name, ok, evidence, *, required=True):
    return {"id": name, "status": "passed" if ok else "failed",
            "required": required, "evidence": evidence}


def unchanged_files_check(name, identities):
    """Report missing/replaced assets even when the final read itself fails."""
    issues = []
    if not identities:
        issues.append("No source identities were captured")
    for entry in identities:
        try:
            if not entry.get("sha256") or sha256_file(entry["path"]) != entry["sha256"]:
                issues.append({"path": entry["path"], "error": "missing or changed source hash"})
        except OSError as exc:
            issues.append({"path": entry["path"], "error": str(exc)})
    return check(name, not issues, issues)


def compare_joints(source, imported, mimics):
    """Compare by full USD path; counts or suffix matches cannot prove identity."""
    checks = []
    expected = {j["path"]: j for j in source if j["enabled"]}
    actual = {j["path"]: j for j in imported}
    missing = sorted(set(expected) - set(actual))
    duplicates = sorted(path for path, count in Counter(j["path"] for j in imported).items() if count > 1)
    disabled = sorted(path for path in expected if path in actual and not actual[path]["enabled"])
    checks.append(check("named_joints_preserved", not (missing or duplicates or disabled),
                        {"missing": missing, "duplicates": duplicates, "disabled": disabled}))
    mismatches = []
    for path, src in expected.items():
        dst = actual.get(path)
        if dst is None:
            continue
        if src["kind"] != dst["kind"]:
            mismatches.append({"path": path, "field": "kind",
                               "source": src["kind"], "imported": dst["kind"]})
        if src["loop"] and not dst["loop"]:
            mismatches.append({"path": path, "field": "loop", "imported": False})
        for field in ("body0", "body1"):
            if field in src and src[field] != dst.get(field):
                mismatches.append({"path": path, "field": field,
                                   "source": src[field], "imported": dst.get(field)})
        for field in ("lower", "upper"):
            a, b = src.get(field), dst.get(field)
            if a is not None and (b is None or not math.isclose(a, b, abs_tol=1e-5)):
                mismatches.append({"path": path, "field": field, "source": a, "imported": b})
    checks.append(check("joint_types_limits_loops_preserved", not mismatches, mismatches))
    actual_mimics = {m["follower"]: m for m in mimics if m["enabled"]}
    differences = []
    for src in expected.values():
        for mimic in src.get("mimics", []):
            dst = actual_mimics.get(src["path"])
            if (not dst or dst["leader"] != mimic["leader"]
                    or not math.isclose(dst["coef0"], mimic["coef0"], abs_tol=1e-6)
                    or not math.isclose(dst["coef1"], mimic["coef1"], abs_tol=1e-6)):
                differences.append({"follower": src["path"], "source": mimic, "imported": dst})
    checks.append(check("driver_follower_constraints_preserved", not differences, differences))
    return checks


def source_inventory(stage):
    from pxr import Usd, UsdGeom, UsdPhysics

    def value(prim, name, default=None):
        attr = prim.GetAttribute(name)
        result = attr.Get() if attr else None
        return result if result is not None else default

    def authored(prim, name):
        attr = prim.GetAttribute(name)
        return bool(attr and attr.HasAuthoredValueOpinion())

    joints, bodies, collisions, articulations = [], [], [], []
    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
        path = str(prim.GetPath())
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            setting = {"path": path, "self_collision_enabled": None, "attribute": None}
            # Match the explicit resolver order used below. Unauthored schema
            # defaults must not hide an authored PhysX value.
            for name in ("newton:selfCollisionEnabled", "physxArticulation:enabledSelfCollisions"):
                attr = prim.GetAttribute(name)
                if attr and attr.HasAuthoredValueOpinion():
                    setting.update(self_collision_enabled=bool(attr.Get()), attribute=name)
                    break
            articulations.append(setting)
        if prim.IsA(UsdPhysics.Joint):
            joint = {"path": path, "kind": prim.GetTypeName(),
                     "enabled": bool(value(prim, "physics:jointEnabled", True)),
                     "loop": bool(value(prim, "physics:excludeFromArticulation", False)),
                     "body0": [str(p) for p in prim.GetRelationship("physics:body0").GetTargets()],
                     "body1": [str(p) for p in prim.GetRelationship("physics:body1").GetTargets()],
                     "mimics": []}
            scale = math.pi / 180 if prim.IsA(UsdPhysics.RevoluteJoint) else 1
            for key in ("lower", "upper"):
                limit = value(prim, f"physics:{key}Limit")
                joint[key] = float(limit) * scale if limit is not None and math.isfinite(limit) else None
            drive_axis = "linear" if prim.IsA(UsdPhysics.PrismaticJoint) else "angular"
            target = value(prim, f"drive:{drive_axis}:physics:targetPosition")
            joint["target"] = float(target) * scale if target is not None else None
            joint["coordinate_unit"] = "m" if drive_axis == "linear" else "rad"
            for rel in prim.GetRelationships():
                if rel.GetName().startswith("physxMimicJoint:") and rel.GetName().endswith(":referenceJoint"):
                    prefix = rel.GetName().rsplit(":", 1)[0]
                    targets = rel.GetTargets()
                    if targets:
                        joint["mimics"].append({
                            "leader": str(targets[0]),
                            "coef0": -float(value(prim, prefix + ":offset", 0)),
                            "coef1": -float(value(prim, prefix + ":gearing", 1)),
                        })
            joints.append(joint)
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            diagonal = value(prim, "physics:diagonalInertia")
            bodies.append({"path": path, "mass": value(prim, "physics:mass"),
                           "mass_authored": authored(prim, "physics:mass"),
                           "density": value(prim, "physics:density"),
                           "density_authored": authored(prim, "physics:density"),
                           "gravity_disabled": value(prim, "physxRigidBody:disableGravity")
                           if authored(prim, "physxRigidBody:disableGravity") else None,
                           "diagonal_inertia": list(diagonal) if diagonal is not None else None,
                           "diagonal_inertia_authored": authored(prim, "physics:diagonalInertia"),
                           "geometry_paths": [str(p.GetPath()) for p in Usd.PrimRange(
                               prim, Usd.TraverseInstanceProxies()) if p.IsA(UsdGeom.Gprim)]})
        if prim.HasAPI(UsdPhysics.CollisionAPI) and value(prim, "physics:collisionEnabled", True):
            collisions.append({"path": path, "kind": prim.GetTypeName()})
    # Articulations can be rooted at a joint or one body; other members need
    # not be descendants of that prim. Reuse the same native descriptors as
    # Newton's importer instead of inferring membership from path prefixes.
    native = UsdPhysics.LoadUsdPhysicsFromRange(stage, [stage.GetPseudoRoot().GetPath()])
    paths, descriptors = native.get(UsdPhysics.ObjectType.Articulation, ([], []))
    membership = {str(path): [str(body) for body in desc.articulatedBodies if str(body)]
                  for path, desc in zip(paths, descriptors) if desc.isValid}
    for articulation in articulations:
        articulation["articulated_bodies"] = membership.get(articulation["path"])
    return {"joints": joints, "bodies": bodies, "collisions": collisions, "articulations": articulations,
            "meters_per_unit": UsdGeom.GetStageMetersPerUnit(stage),
            "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
            "composition_errors": [str(e) for e in stage.GetCompositionErrors()]}


def builder_inventory(builder):
    import newton
    import numpy as np

    kinds = {newton.JointType.REVOLUTE: "PhysicsRevoluteJoint",
             newton.JointType.PRISMATIC: "PhysicsPrismaticJoint",
             newton.JointType.FIXED: "PhysicsFixedJoint",
             newton.JointType.BALL: "PhysicsSphericalJoint"}
    joints = []
    for i, path in enumerate(builder.joint_label):
        dof = builder.joint_qd_start[i]
        scalar = builder.joint_type[i] in (newton.JointType.REVOLUTE, newton.JointType.PRISMATIC)
        joints.append({"path": path, "kind": kinds.get(builder.joint_type[i], str(builder.joint_type[i])),
                       "loop": builder.joint_articulation[i] < 0,
                       "lower": float(builder.joint_limit_lower[dof]) if scalar else None,
                       "upper": float(builder.joint_limit_upper[dof]) if scalar else None,
                       "target": float(builder.joint_target_q[builder.joint_q_start[i]]) if scalar else None,
                       "body0": [builder.body_label[builder.joint_parent[i]]]
                       if builder.joint_parent[i] >= 0 else [],
                       "body1": [builder.body_label[builder.joint_child[i]]]
                       if builder.joint_child[i] >= 0 else [],
                       "enabled": bool(builder.joint_enabled[i])})
    mimics = [{"follower": builder.joint_label[follower],
               "leader": builder.joint_label[builder.constraint_mimic_joint1[i]],
               "coef0": builder.constraint_mimic_coef0[i],
               "coef1": builder.constraint_mimic_coef1[i],
               "enabled": builder.constraint_mimic_enabled[i]}
              for i, follower in enumerate(builder.constraint_mimic_joint0)]
    bodies = [{"path": path, "mass": float(builder.body_mass[i]),
               "inertia": np.asarray(builder.body_inertia[i]).reshape(3, 3).tolist()}
              for i, path in enumerate(builder.body_label)]
    collisions = [{"index": i, "path": path, "body": builder.body_label[builder.shape_body[i]]
                   if builder.shape_body[i] >= 0 else None}
                  for i, path in enumerate(builder.shape_label)
                  if int(builder.shape_flags[i]) & int(newton.ShapeFlags.COLLIDE_SHAPES)]
    return {"joints": joints, "mimics": mimics, "bodies": bodies, "collisions": collisions,
            "shape_count": builder.shape_count, "joint_coordinate_count": len(builder.joint_q)}


def compare_collision_filters(source, imported, filter_pairs):
    """Check every imported hull against authored disabled articulation self-contact.

    A compact boolean matrix avoids building a second Python set of millions
    of hull pairs. This does not certify external contact forces or the geometry
    of convex approximations; those require separate physical qualification.
    """
    import numpy as np

    shapes = imported["collisions"]
    positions = {shape["index"]: i for i, shape in enumerate(shapes)}
    filtered = np.zeros((len(shapes), len(shapes)), dtype=bool)
    for a, b in filter_pairs:
        i, j = positions.get(a), positions.get(b)
        if i is not None and j is not None:
            filtered[min(i, j), max(i, j)] = True
    roots = source.get("articulations", [])
    bodies = np.asarray([shape["body"] for shape in shapes], dtype=object)
    order = np.arange(len(shapes))
    evidence = []
    for root in roots:
        if root["self_collision_enabled"] is not False:
            continue
        if not root.get("articulated_bodies"):
            evidence.append({**root, "expected_filtered_pairs": None, "missing_filtered_pairs": None,
                             "error": "Native articulation membership is missing or invalid", "examples": []})
            continue
        expected, missing, examples = 0, 0, []
        member = np.isin(bodies, root["articulated_bodies"])
        for i in np.flatnonzero(member):
            targets = np.flatnonzero(member & (bodies != bodies[i]) & (order > i))
            absent = targets[~filtered[i, targets]]
            expected += len(targets)
            missing += len(absent)
            for j in absent[:max(0, 5 - len(examples))]:
                examples.append([shapes[i]["path"], shapes[j]["path"]])
        evidence.append({**root, "expected_filtered_pairs": expected,
                         "missing_filtered_pairs": missing, "examples": examples})
    return check("authored_self_collision_filters_preserved",
                 all(e["missing_filtered_pairs"] == 0 for e in evidence), evidence)


def compare_physics(source, imported):
    import numpy as np

    missing, mismatches = [], []
    actual = {body["path"]: body for body in imported["bodies"]}
    for body in source["bodies"]:
        dst = actual.get(body["path"])
        if dst is None:
            missing.append(body["path"])
            continue
        # USD mass=0 means unspecified/derive from density, even when authored.
        # Newton's native _mass_api_effective_mass follows the same semantics.
        mass = body["mass"]
        if mass is not None and mass > 0 and not math.isclose(mass, dst["mass"], rel_tol=1e-4, abs_tol=1e-7):
            mismatches.append({"path": body["path"], "field": "mass",
                               "source": body["mass"], "imported": dst["mass"]})
        diagonal = body["diagonal_inertia"]
        if diagonal and min(diagonal) > 0:
            eigenvalues = np.linalg.eigvalsh(dst["inertia"])
            if not np.allclose(sorted(diagonal), eigenvalues, rtol=1e-3, atol=1e-8):
                mismatches.append({"path": body["path"], "field": "inertia_eigenvalues",
                                   "source": sorted(diagonal), "imported": eigenvalues.tolist()})
    collision_paths = [shape["path"] for shape in imported["collisions"]]
    missing_colliders = [c["path"] for c in source["collisions"]
                        if not any(p == c["path"] or p.startswith(c["path"] + "/")
                                   or p.startswith(c["path"] + "_") for p in collision_paths)]
    return [check("rigid_bodies_preserved", not missing, missing),
            check("authored_mass_principal_inertia_preserved", not mismatches, mismatches),
            check("declared_colliders_preserved", not missing_colliders, missing_colliders)]


def describe_inertia_changes(source, imported, finalized):
    """Record finalization changes without waiving source-fidelity failures.

    In particular, USD's fallback mass=0 means unspecified, not an explicit
    zero-mass physical body. Empty fixed reference frames can still carry
    authored placeholder inertia. This evidence does not establish how the
    source engine interprets those incomplete mass properties.
    """
    import numpy as np

    sources = {body["path"]: body for body in source["bodies"]}
    before = {body["path"]: body for body in imported}
    changes = []
    for after in finalized:
        prior = before[after["path"]]
        if (math.isclose(prior["mass"], after["mass"], rel_tol=1e-5, abs_tol=1e-9)
                and np.allclose(prior["inertia"], after["inertia"], rtol=1e-5, atol=1e-9)):
            continue
        changes.append({"path": after["path"], "source": sources.get(after["path"]),
                        "before_finalize": prior, "after_finalize": after,
                        "attached_joints": [joint for joint in source["joints"]
                                            if after["path"] in joint["body0"] + joint["body1"]]})
    return changes


def map_physx_body_gravity(stage):
    """Map authored per-body gravity into the CPU backend's supported schema.

    Newton's PhysX resolver currently reads gravity only for a scene prim.
    Keep world gravity for other objects and leave the source layers untouched.
    """
    from pxr import Sdf, Usd, UsdPhysics

    mappings = []
    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        attr = prim.GetAttribute("physxRigidBody:disableGravity")
        if not attr or not attr.HasAuthoredValueOpinion() or attr.Get() is None:
            continue
        expected = float(bool(attr.Get()))
        current = prim.GetAttribute("mjc:gravcomp")
        if current and current.HasAuthoredValueOpinion():
            if current.Get() != expected:
                raise ValueError(f"Conflicting authored gravity settings on {prim.GetPath()}")
            continue
        mappings.append({"path": str(prim.GetPath()), "source_disabled": bool(attr.Get()),
                         "mujoco_gravcomp": expected})
    # Validate conflicts before writing any session opinions.
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for entry in mappings:
            prim = stage.GetPrimAtPath(entry["path"])
            prim.CreateAttribute("mjc:gravcomp", Sdf.ValueTypeNames.Float).Set(entry["mujoco_gravcomp"])
    return mappings


def compare_body_gravity(source, model, solver):
    import numpy as np

    mapping = solver.mjc_body_to_newton.numpy()[0]
    body_ids = {name: i for i, name in enumerate(model.body_label)}
    evidence = []
    for body in source["bodies"]:
        disabled = body.get("gravity_disabled")
        if disabled is None:
            continue
        ids = np.flatnonzero(mapping == body_ids.get(body["path"], -2))
        actual = [float(solver.mj_model.body_gravcomp[i]) for i in ids]
        evidence.append({"path": body["path"], "source_disabled": disabled,
                         "compiled_gravcomp": actual,
                         "preserved": len(actual) == 1 and abs(actual[0] - float(disabled)) < 1e-6})
    return check("authored_body_gravity_preserved", all(e["preserved"] for e in evidence), evidence)


def compare_ball_loop_anchors(model, solver):
    """Audit both local anchors after the CPU backend compiles ball closures.

    A backend may synthesize the second anchor from the initial body pose.
    That closes the initial configuration but changes an authored mechanism.
    Compare against the imported joint frames, without changing either model.
    """
    import newton
    import numpy as np
    import mujoco

    types = model.joint_type.numpy()
    loops = np.flatnonzero((types == int(newton.JointType.BALL))
                           & (model.joint_articulation.numpy() < 0)
                           & model.joint_enabled.numpy())
    native = solver.mj_model
    mapping = (solver.mjc_eq_to_newton_jnt.numpy()[0]
               if solver.mjc_eq_to_newton_jnt is not None else np.array([], dtype=int))
    bodies = solver.mjc_body_to_newton.numpy()[0]
    parent, child = model.joint_parent.numpy(), model.joint_child.numpy()
    xp, xc = model.joint_X_p.numpy(), model.joint_X_c.numpy()
    evidence = []
    for j in loops:
        ids = np.flatnonzero(mapping == j)
        entry = {"path": model.joint_label[j], "equality_ids": ids.tolist(), "preserved": False}
        if len(ids) == 1:
            i = int(ids[0])
            expected = np.concatenate((xp[j, :3], xc[j, :3]))
            actual = native.eq_data[i, :6]
            bound = (int(native.eq_type[i]) == int(mujoco.mjtEq.mjEQ_CONNECT)
                     and bodies[native.eq_obj1id[i]] == parent[j]
                     and bodies[native.eq_obj2id[i]] == child[j])
            entry.update(expected_anchors=expected.tolist(), compiled_anchors=actual.tolist(),
                         bodies_match=bool(bound),
                         max_anchor_component_error_m=float(np.max(abs(actual - expected))),
                         preserved=bool(bound and solver.mj_data.eq_active[i]
                                        and np.allclose(actual, expected, rtol=0, atol=1e-6)))
        evidence.append(entry)
    return check("native_ball_loop_anchors_preserved", all(e["preserved"] for e in evidence), evidence)


def qualify(source: Path, output: Path, *, omit_missing_camera=False):
    """Import and finalize on CPU, retaining failed attempts and source identity."""
    started = time.monotonic()
    report = make_report(run=output.name, profile=source.parent.name,
                         stage="embodiment_import_qualification",
                         inputs={"source": str(source)})
    report.update(scope="embodiment_import_qualification", task_success_evaluated=False,
                  device="cpu", adaptations=[], checks=[])
    checks = report["checks"]
    try:
        report["inputs"]["sha256"] = sha256_file(source)
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        import warp as wp
        wp.config.enable_cuda = False
        import newton
        from newton.solvers import SolverMuJoCo
        from newton.usd import SchemaResolverNewton, SchemaResolverPhysx
        from pxr import Sdf, Usd
        from robosimstudio.scene.build.imports import add_usd_stage

        report["environment"] = {"python": sys.executable,
            "packages": {name: importlib.metadata.version(name)
                         for name in ("newton", "warp-lang", "usd-core", "newton-usd-schemas")}}
        report["import_options"] = {
            "schema_resolvers": ["SchemaResolverNewton", "SchemaResolverPhysx"],
            "collapse_fixed_joints": False,
            "skip_mesh_approximation": False,
        }
        root = Sdf.Layer.FindOrOpen(str(source))
        session = Sdf.Layer.CreateAnonymous("geniesim_import_qualification")
        stage = Usd.Stage.Open(root, session, Usd.Stage.LoadAll)
        inventory = source_inventory(stage)
        report["source_inventory"] = inventory
        report["inputs"]["layers"] = [
            {"path": layer.realPath, "sha256": sha256_file(layer.realPath)}
            for layer in stage.GetUsedLayers() if layer.realPath]
        checks.append(check("exact_source_composes", not inventory["composition_errors"],
                            inventory["composition_errors"]))
        if omit_missing_camera:
            prim = stage.GetPrimAtPath(MISSING_CAMERA)
            if (source.parent.name != "G1_omnipicker" or not prim
                    or len(inventory["composition_errors"]) != 1
                    or "camera.usd" not in inventory["composition_errors"][0]):
                raise ValueError("Camera overlay only applies to the verified single G1 camera dependency error")
            stage.SetEditTarget(session)
            prim.GetReferences().SetReferences([])
            prim.SetActive(False)
            report["adaptations"].append({"operation": "clear_reference_and_deactivate_in_anonymous_session",
                                          "path": MISSING_CAMERA,
                                          "reason": "unavailable Isaac decorative camera mesh"})
        gravity_mapping = map_physx_body_gravity(stage)
        if gravity_mapping:
            report["adaptations"].append({"operation": "map_authored_physx_body_gravity_to_mujoco",
                                          "bodies": gravity_mapping,
                                          "source_layers_modified": False})
        builder = newton.ModelBuilder()
        builder.validate_inertia_detailed = True
        SolverMuJoCo.register_custom_attributes(builder)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            add_usd_stage(builder, stage, collapse_fixed_joints=False, verbose=False,
                          schema_resolvers=[SchemaResolverNewton(), SchemaResolverPhysx()])
            report["imported_inventory"] = builder_inventory(builder)
            checks.append(check("native_rss_import", True,
                                "robosimstudio.scene.build.imports.add_usd_stage"))
            imported = report["imported_inventory"]
            checks.extend(compare_joints(inventory["joints"], imported["joints"], imported["mimics"]))
            checks.extend(compare_physics(inventory, imported))
            # Iterate Newton's compact storage without materializing its public
            # list property (millions of pairs on the fully decomposed robots).
            checks.append(compare_collision_filters(inventory, imported, builder._shape_collision_filter_pairs))
            invalid_source_targets = [j for j in inventory["joints"]
                                      if j["target"] is not None and (
                                          (j["lower"] is not None and j["target"] < j["lower"] - 1e-6)
                                          or (j["upper"] is not None and j["target"] > j["upper"] + 1e-6))]
            checks.append(check("authored_drive_targets_within_limits",
                                not invalid_source_targets, invalid_source_targets))
            invalid_targets = [j for j in imported["joints"]
                               if j["target"] is not None and (
                                   (j["lower"] is not None and j["target"] < j["lower"] - 1e-6)
                                   or (j["upper"] is not None and j["target"] > j["upper"] + 1e-6))]
            checks.append(check("drive_targets_within_limits", not invalid_targets, invalid_targets))
            model = builder.finalize(device="cpu")
            checks.append(check("native_cpu_finalize", str(model.device) == "cpu", str(model.device)))
            final_masses, final_inertias = model.body_mass.numpy(), model.body_inertia.numpy()
            final_bodies = [{"path": body["path"], "mass": float(final_masses[i]),
                             "inertia": final_inertias[i].tolist()}
                            for i, body in enumerate(imported["bodies"])]
            report["finalized_bodies"] = final_bodies
            report["runtime_inertia_changes"] = describe_inertia_changes(
                inventory, imported["bodies"], final_bodies)
            inertia_check = compare_physics(inventory, {**imported, "bodies": final_bodies})[1]
            inertia_check["id"] = "finalized_authored_mass_principal_inertia_preserved"
            checks.append(inertia_check)
            try:
                solver = SolverMuJoCo(model, use_mujoco_cpu=True)
                checks.append(check("native_cpu_solver_build", True,
                                    {"nq": solver.mj_model.nq, "nv": solver.mj_model.nv,
                                     "neq": solver.mj_model.neq}))
            except Exception as exc:
                checks.append(check("native_cpu_solver_build", False, f"{type(exc).__name__}: {exc}"))
            else:
                checks.append(compare_body_gravity(inventory, model, solver))
                checks.append(compare_ball_loop_anchors(model, solver))
            report["warnings"] = [str(w.message) for w in caught]
        report["sweep"] = {"status": "not_run",
                           "simulation_steps": 0,
                           "reason": "This import audit does not step physics; resolve remaining physical fidelity checks before a controlled sweep"}
    except Exception as exc:
        checks.append(check("native_runtime_completed", False, f"{type(exc).__name__}: {exc}"))
        report["failure"] = traceback.format_exc()
    checks.append(unchanged_files_check("source_entry_unchanged", [
        {"path": str(source), "sha256": report["inputs"].get("sha256")}]))
    checks.append(unchanged_files_check("source_layers_unchanged", report["inputs"].get("layers", [])))
    checks.append({"id": "remaining_physical_qualification", "required": True, "status": "skipped",
                   "evidence": ["Joint axes/local frames, full inertia orientation, collision hull geometry "
                                "and filtering, solver constraint enforcement, gripper dynamics and cameras "
                                "are not qualified by this structural import audit"]})
    report["status"] = status_for_checks(checks)
    report["blockers"] = [c for c in checks if c["required"] and c["status"] != "passed"]
    report["elapsed_s"] = round(time.monotonic() - started, 3)
    report["next_action"] = "Resolve import fidelity blockers before controlled gripper dynamics and per-robot task execution"
    atomic_json(output, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot", choices=ROBOTS, required=True)
    parser.add_argument("--assets-root", type=Path, default=Path("GenieSimAssets/robot"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--omit-missing-camera", action="store_true",
                        help="Record and deactivate only G1's missing decorative camera in an anonymous session")
    args = parser.parse_args(argv)
    report = qualify((args.assets_root / args.robot / "robot.usda").resolve(),
                     args.output, omit_missing_camera=args.omit_missing_camera)
    print(f"{args.robot}: {report['status']}; report={args.output}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
