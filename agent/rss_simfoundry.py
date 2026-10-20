"""Build and measure SimFoundry rigid assets in the existing RSS runner."""

from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np

from agent.run_reports import atomic_json, sha256_file
from agent.simfoundry_assets import simfoundry_rigid_specs


def asset_scene(specs, *, physics_device="cpu"):
    """Use the same native scene declaration for validation and state replay."""
    import robosimstudio as rss
    from robosimstudio.specs import backend

    scene = rss.Scene(
        cell="void",
        name="simfoundry_rigid_import",
        physics=rss.MUJOCO_500HZ.with_backend(
            "*",
            backend(
                "mujoco",
                nconmax=512,
                njmax=512,
                use_mujoco_contacts=True,
                use_mujoco_cpu=physics_device == "cpu",
            ),
        ),
    )
    for spec in specs:
        scene.add(spec)
    return scene


def model_identity(sim):
    """Bind the tape's body order to the compiled asset geometry and transforms."""
    arrays = ("shape_body", "shape_type", "shape_transform", "shape_scale")
    return {
        "body_labels": list(sim.model.body_label),
        "joint_labels": list(sim.model.joint_label),
        "shapes": {
            name: hashlib.sha256(getattr(sim.model, name).numpy().tobytes()).hexdigest()
            for name in arrays
        },
    }


def snapshot_assets(run):
    """Return only verified, run-local assets referenced by the import snapshot."""
    manifest = json.loads((run / "simfoundry_import.json").read_text())
    if manifest.get("schema") != "dexagent.simfoundry_import.v1":
        raise ValueError("unsupported SimFoundry import snapshot")
    paths = []
    for name, expected in manifest["asset_files"].items():
        path = (run / name).resolve()
        if not path.is_relative_to(run.resolve()) or sha256_file(path) != expected:
            raise ValueError(f"recorded asset identity does not match snapshot: {name}")
        paths.append(path)
    return manifest, paths


def rebuild_snapshot(run, episode):
    """Build display geometry from a frozen import, without warming or stepping."""
    from robosimstudio.core.cameras import CameraSpec
    from robosimstudio.specs import AssetSpec, ObjectSpec

    manifest, paths = snapshot_assets(run)
    meta = json.loads((episode / "meta.json").read_text())
    if meta.get("metrics", {}).get("import_manifest_sha256") != sha256_file(
        run / "simfoundry_import.json"
    ):
        raise ValueError("import snapshot does not match the recorded episode")
    permitted = set(paths)

    def asset_path(name):
        path = (run / name).resolve()
        if path not in permitted:
            raise ValueError(f"asset is absent from recorded identity: {name}")
        return str(path)

    specs = []
    for raw in manifest["objects"]:
        asset = dict(raw["asset"])
        asset["path"] = asset_path(asset["path"])
        asset["collision_paths"] = tuple(
            asset_path(p) for p in asset["collision_paths"]
        )
        if asset.get("collision_path"):
            asset["collision_path"] = asset_path(asset["collision_path"])
        specs.append(ObjectSpec(**{**raw, "asset": AssetSpec(**asset)}))
    sim = asset_scene(specs).build(device="cpu", warmup=False, use_cuda_graph=False)
    try:
        if model_identity(sim) != manifest["model_identity"]:
            raise ValueError(
                "recorded model identity does not match rebuilt asset scene"
            )
        return (
            sim,
            CameraSpec(**manifest["camera"]),
            manifest["model_identity"]["body_labels"],
        )
    except BaseException:
        sim.close()
        raise


def build_import(args, out, report, check):
    from robosimstudio.core.cameras import CameraSpec
    import trimesh

    stage8, stage11, stage12 = (
        args.simfoundry_stage8.resolve(),
        args.simfoundry_stage11.resolve(),
        args.simfoundry_stage12.resolve(),
    )
    pose_info = (args.simfoundry_pose_info or stage8 / "info").resolve()
    roots = (stage8 / "canonical_mesh", pose_info, stage11, stage12)
    for root in roots:
        if not root.is_dir():
            raise FileNotFoundError(f"SimFoundry input directory is missing: {root}")
        if out.resolve().is_relative_to(root) or root.is_relative_to(out.resolve()):
            raise ValueError(
                "RSS output and SimFoundry input directories must not overlap"
            )
    importer_sources = {}
    for source in (Path(__file__), Path(__file__).with_name("simfoundry_assets.py")):
        target = out / f"{source.stem}_source.py"
        shutil.copy2(source, target)
        importer_sources[str(source)] = sha256_file(target)
    report["inputs"]["importer_sources"] = importer_sources
    # Preserve the exact source identity, including original collision assets.
    # The source files remain upstream-owned; derived proxies live under out.
    paths = sorted(
        {p.resolve() for root in roots for p in root.rglob("*") if p.is_file()}
    )
    before = {str(p): sha256_file(p) for p in paths}
    report["inputs"]["simfoundry"] = {
        "stage8": str(stage8),
        "pose_info": str(pose_info),
        "stage11": str(stage11),
        "stage12": str(stage12),
        "files": before,
    }
    specs = simfoundry_rigid_specs(
        canonical_mesh_dir=stage8 / "canonical_mesh",
        pose_info_dir=pose_info,
        stage11_dir=stage11,
        stage12_dir=stage12,
        collision=args.simfoundry_collision,
        derived_asset_dir=out / "collision_assets",
    )
    if any(sha256_file(p) != before[str(p)] for p in paths):
        raise RuntimeError(
            "SimFoundry inputs changed during import; retry from a completed reconstruction"
        )
    visual_dir = out / "visual_assets"
    visual_dir.mkdir()
    copied = []
    for spec in specs:
        target = visual_dir / f"{spec.name}.glb"
        shutil.copy2(spec.asset.path, target)
        if sha256_file(target) != before[spec.asset.path]:
            raise RuntimeError(
                f"SimFoundry visual asset changed while copying: {spec.asset.path}"
            )
        copied.append(replace(spec, asset=replace(spec.asset, path=str(target))))
    specs = copied
    snapshot = {
        "schema": "dexagent.simfoundry_import.v1",
        "input_contract": "SimFoundry Stage 8/11/12 rigid outputs",
        "source_files": before,
        "collision": args.simfoundry_collision,
        "frame": "stage12_world_z_up_metres",
        "quaternion_order": "xyzw",
        "support": "RSS void ground at z=0, matching Stage 12 plane.urdf",
        "inertia": "RSS estimates from visual geometry and stated mass; Stage 11 inertial frame is not transferred",
        "training_eligible": False,
        "objects": [],
        "asset_files": {},
    }
    for spec in specs:
        raw = asdict(spec)
        for key in ("path", "collision_paths"):
            originals = [raw["asset"][key]] if key == "path" else raw["asset"][key]
            names = [str(Path(p).relative_to(out)) for p in originals]
            snapshot["asset_files"].update(
                {name: sha256_file(out / name) for name in names}
            )
            raw["asset"][key] = names[0] if key == "path" else names
        snapshot["objects"].append(raw)
    scene = asset_scene(specs, physics_device=args.physics_device)
    sim = scene.build(
        device="cuda:0" if args.render or args.physics_device == "gpu" else "cpu",
        use_cuda_graph=args.physics_device == "gpu",
    )
    try:
        if args.physics_device == "cpu":
            from agent.rss_scene import CPUContactProbe

            sim.contacts = CPUContactProbe(
                sim.pipeline.solver, sim.model, sim.pipeline.sim_dt
            )
        else:
            from robosimstudio.core.probes import ContactProbe

            sim.contacts = ContactProbe(
                sim.pipeline.solver, sim.model, sim.pipeline.sim_dt
            )
        masses = sim.model.body_mass.numpy()
        frictions = sim.model.shape_material_mu.numpy()
        for spec in specs:
            ref = sim[spec.name]
            handle = ref.handle
            observed = ref.pose
            report["checks"].extend(
                [
                    check(
                        f"{spec.name}.initial_pose",
                        bool(
                            np.allclose(observed[:3], spec.pose, atol=1e-6)
                            and abs(np.dot(observed[3:], spec.quat)) > 1 - 1e-6
                        ),
                        observed.tolist(),
                    ),
                    check(
                        f"{spec.name}.mass",
                        bool(np.isclose(masses[handle.body], spec.mass, rtol=1e-5)),
                        float(masses[handle.body]),
                        spec.mass,
                    ),
                    check(
                        f"{spec.name}.collision_proxies",
                        len(handle.collision_shapes)
                        == (
                            len(spec.asset.collision_paths)
                            if args.simfoundry_collision == "stage11"
                            else 1
                        ),
                        len(handle.collision_shapes),
                    ),
                    check(
                        f"{spec.name}.friction",
                        bool(
                            len(handle.collision_shapes)
                            and np.allclose(
                                frictions[handle.collision_shapes],
                                spec.friction,
                                atol=1e-6,
                            )
                        ),
                        frictions[handle.collision_shapes].tolist(),
                        spec.friction,
                    ),
                ]
            )
        if any(
            c["status"] != "passed" for c in report["checks"] if c.get("required", True)
        ):
            raise RuntimeError(
                "RSS import audit failed; physics validation was not started"
            )

        corners = []
        for spec in specs:
            mesh = trimesh.load(spec.asset.path, force="mesh", process=False)
            qx, qy, qz, qw = spec.quat
            transform = trimesh.transformations.quaternion_matrix((qw, qx, qy, qz))
            transform[:3, 3] = spec.pose
            corners.extend(
                trimesh.transform_points(
                    trimesh.bounds.corners(mesh.bounds * spec.asset.scale), transform
                )
            )
        bounds = np.array(corners)
        center = (bounds.min(axis=0) + bounds.max(axis=0)) / 2
        distance = max(float(np.linalg.norm(np.ptp(bounds, axis=0))), 0.2) * 1.5
        camera = CameraSpec(
            name="overview",
            width=640,
            height=480,
            fov_y=48,
            eye=tuple(center + distance * np.array([1, -1, 0.8])),
            look_at=tuple(center),
        )
        snapshot.update(model_identity=model_identity(sim), camera=asdict(camera))
        atomic_json(out / "simfoundry_import.json", snapshot)
        report["metrics"]["import_manifest_sha256"] = sha256_file(
            out / "simfoundry_import.json"
        )
        return sim, specs, camera
    except BaseException:
        sim.close()
        raise


def validate_import(sim, specs, recorder, record, limits, check):
    """Measure passive target-runtime stability without resetting velocities."""
    recorder.begin(sim.episode, sim)
    n = len(specs)
    initial = np.array([(*spec.pose, *spec.quat) for spec in specs])
    max_drift, max_rotation = np.zeros(n), np.zeros(n)
    max_penetration = 0.0
    speeds = []
    hz = float(sim.request.physics.control_hz)
    for _ in range(max(2, round(limits["seconds"] * hz))):
        sim.step()
        record()
        poses = np.array([sim[spec.name].pose for spec in specs])
        velocities = np.array([sim[spec.name].velocity for spec in specs])
        max_drift = np.maximum(
            max_drift, np.linalg.norm(poses[:, :3] - initial[:, :3], axis=1)
        )
        # Float32 tapes can have w=1 with a small nonzero xyz component. Normalize
        # in float64 before taking the dot, or those small rotations disappear.
        quat = poses[:, 3:].astype(np.float64)
        quat /= np.linalg.norm(quat, axis=1, keepdims=True)
        initial_quat = initial[:, 3:] / np.linalg.norm(
            initial[:, 3:], axis=1, keepdims=True
        )
        rotation = 2 * np.arccos(
            np.clip(np.abs(np.sum(quat * initial_quat, axis=1)), 0, 1)
        )
        max_rotation = np.maximum(max_rotation, rotation)
        speeds.append(
            np.stack(
                (
                    np.linalg.norm(velocities[:, :3], axis=1),
                    np.linalg.norm(velocities[:, 3:], axis=1),
                ),
                axis=1,
            )
        )
        sim.contacts.update(sim.step_count)
        overlaps = sim.contacts.penetrations()
        if len(overlaps):
            max_penetration = max(max_penetration, float(overlaps[:, 2].max()))
    rest = np.array(speeds[-max(1, round(limits["rest_window_s"] * hz)) :]).max(axis=0)
    objects, checks = {}, []
    for i, spec in enumerate(specs):
        objects[spec.name] = {
            "initial_pose": initial[i].tolist(),
            "final_pose": sim[spec.name].pose.tolist(),
            "max_drift_m": float(max_drift[i]),
            "max_rotation_rad": float(max_rotation[i]),
            "rest_linear_speed_mps": float(rest[i, 0]),
            "rest_angular_speed_rad_s": float(rest[i, 1]),
        }
        for metric, observed, threshold in (
            ("drift", max_drift[i], limits["max_drift_m"]),
            ("rotation", max_rotation[i], limits["max_rotation_rad"]),
            ("rest_linear_speed", rest[i, 0], limits["max_speed_mps"]),
            ("rest_angular_speed", rest[i, 1], limits["max_angular_speed_rad_s"]),
        ):
            checks.append(
                check(
                    f"{spec.name}.{metric}",
                    bool(observed <= threshold),
                    float(observed),
                    threshold,
                )
            )
    checks.append(
        check(
            "import_penetration",
            max_penetration <= limits["max_penetration_m"],
            max_penetration,
            limits["max_penetration_m"],
        )
    )
    checks.append(
        {
            "id": "source_inertia_review",
            "required": False,
            "status": "warning",
            "evidence": "RSS computes inertia from visual geometry; Stage 11 COM/inertial frame and estimated physical properties need task-specific review.",
        }
    )
    return checks, {
        "objects": objects,
        "max_penetration_m": max_penetration,
        "thresholds": limits,
        "success_semantics": "passive_asset_import_checks_only",
        "training_eligible": False,
    }
