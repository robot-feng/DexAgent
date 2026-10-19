"""Lower SimFoundry's rigid Stage 8/11/12 outputs into RoboSimStudio specs.

This adapter deliberately handles only a single rigid link. It does not import
USD, infer camera/world transforms, or pretend a PyBullet-stabilized pose is a
RoboSimStudio physics result. Stage 12 already provides the rigid object's
world pose; RSS must still build and validate the resulting scene itself.
"""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

Collision = Literal["stage11", "convex_hull", "bounding_box"]


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read SimFoundry metadata {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object in {path}")
    return value


def _scale_from_pose(path: Path) -> float:
    info = _read_json(path)
    try:
        value = info["z_up"]["scale"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"{path}: expected z_up.scale from SimFoundry Stage 8"
        ) from exc
    values = value if isinstance(value, list) else [value]
    try:
        scales = [float(component) for component in values]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{path}: z_up.scale must be a finite scalar or uniform scale"
        ) from exc
    if not scales or any(
        not math.isfinite(component) or component <= 0 for component in scales
    ):
        raise ValueError(f"{path}: z_up.scale must be positive and finite")
    if len(scales) > 1 and any(
        not math.isclose(component, scales[0], rel_tol=1e-5, abs_tol=1e-8)
        for component in scales[1:]
    ):
        raise ValueError(
            f"{path}: non-uniform z_up.scale is not supported by RSS AssetSpec"
        )
    return scales[0]


def _single_link_mass(urdf_path: Path) -> float:
    try:
        root = ET.parse(urdf_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(
            f"cannot read SimFoundry rigid URDF {urdf_path}: {exc}"
        ) from exc
    links = root.findall("link")
    if len(links) != 1:
        raise ValueError(
            f"{urdf_path}: expected one rigid link, found {len(links)}; "
            "flatten the rigid mesh or use the articulated-object importer"
        )
    mass_node = links[0].find("./inertial/mass")
    try:
        mass = float(mass_node.attrib["value"]) if mass_node is not None else math.nan
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"{urdf_path}: the rigid link has no valid inertial mass"
        ) from exc
    if not math.isfinite(mass) or mass <= 0:
        raise ValueError(f"{urdf_path}: inertial mass must be positive and finite")
    return mass


def _urdf_vector(
    text: str | None,
    *,
    size: int,
    default: tuple[float, ...],
    path: Path,
    field: str,
    positive: bool = False,
) -> tuple[float, ...]:
    try:
        values = tuple(float(value) for value in text.split()) if text else default
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}: invalid URDF {field}") from exc
    if (
        len(values) != size
        or not all(math.isfinite(value) for value in values)
        or (positive and any(value <= 0 for value in values))
    ):
        constraint = "positive finite" if positive else "finite"
        raise ValueError(
            f"{path}: URDF {field} must contain {size} {constraint} values"
        )
    return values


def _stage11_collision_paths(
    urdf_path: Path, *, name: str, asset_scale: float, output_dir: Path
) -> tuple[str, ...]:
    """Bake Stage 11 URDF proxy transforms for RSS's shared uniform asset scale.

    SimFoundry stores each proxy with its own URDF scale and origin. RSS applies
    ``AssetSpec.scale`` to both visual and collision meshes, so divide each
    proxy's authored transform by the residual visual scale before exporting it.
    The runtime then restores the Stage 11 coordinates with the shared scale.
    """
    try:
        root = ET.parse(urdf_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(
            f"cannot read SimFoundry rigid URDF {urdf_path}: {exc}"
        ) from exc
    links = root.findall("link")
    if len(links) != 1:
        raise ValueError(f"{urdf_path}: expected one rigid link, found {len(links)}")

    # Import only when this collision backend is selected.
    import trimesh

    model_root = urdf_path.parent.parent.resolve()
    output_dir = output_dir.resolve() / name
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, collision in enumerate(links[0].findall("collision")):
        geometry = collision.find("geometry")
        mesh_node = geometry.find("mesh") if geometry is not None else None
        if mesh_node is None:
            raise ValueError(
                f"{urdf_path}: Stage 11 collision {index} is not a mesh proxy"
            )
        filename = mesh_node.get("filename", "")
        if not filename or filename.startswith("package://"):
            raise ValueError(
                f"{urdf_path}: unsupported Stage 11 collision mesh path {filename!r}"
            )
        source = Path(filename)
        if not source.is_absolute():
            source = urdf_path.parent / source
        source = source.resolve()
        try:
            source.relative_to(model_root)
        except ValueError as exc:
            raise ValueError(
                f"{urdf_path}: collision mesh escapes its SimFoundry asset directory"
            ) from exc
        if not source.is_file():
            raise FileNotFoundError(
                f"{urdf_path}: missing Stage 11 collision proxy {source}"
            )

        mesh_scale = _urdf_vector(
            mesh_node.get("scale"),
            size=3,
            default=(1.0, 1.0, 1.0),
            path=urdf_path,
            field=f"collision[{index}].mesh.scale",
            positive=True,
        )
        origin = collision.find("origin")
        xyz = _urdf_vector(
            origin.get("xyz") if origin is not None else None,
            size=3,
            default=(0.0, 0.0, 0.0),
            path=urdf_path,
            field=f"collision[{index}].origin.xyz",
        )
        rpy = _urdf_vector(
            origin.get("rpy") if origin is not None else None,
            size=3,
            default=(0.0, 0.0, 0.0),
            path=urdf_path,
            field=f"collision[{index}].origin.rpy",
        )

        proxy = trimesh.load(str(source), force="mesh", process=False)
        proxy.apply_scale(tuple(component / asset_scale for component in mesh_scale))
        transform = trimesh.transformations.euler_matrix(*rpy, axes="sxyz")
        transform[:3, 3] = tuple(component / asset_scale for component in xyz)
        proxy.apply_transform(transform)
        target = output_dir / f"{index:03d}_{source.stem}.obj"
        proxy.export(target, file_type="obj")
        paths.append(str(target))

    if not paths:
        raise ValueError(f"{urdf_path}: Stage 11 has no collision mesh proxies")
    return tuple(paths)


def simfoundry_rigid_specs(
    *,
    canonical_mesh_dir: str | Path,
    pose_info_dir: str | Path,
    stage11_dir: str | Path,
    stage12_dir: str | Path,
    collision: Collision,
    derived_asset_dir: str | Path | None = None,
) -> list:
    """Build RSS ``ObjectSpec`` values from one SimFoundry rigid reconstruction.

    ``pose_info_dir`` is the exact Stage 8 directory selected by the pipeline:
    ``info`` or ``info_interactive...``. Stage 12's ``pb_scene_poses.json`` is
    preferred because its positions already include the camera-to-world
    transform and its PyBullet layout stabilization. Use these specs in an RSS
    cell whose work-surface frame is the world origin (normally ``cell="void"``)
    or convert the world poses to the chosen cell's work-surface frame.

    The Stage 8 pre-scale is baked into the canonical GLB, so only the residual
    ``z_up.scale`` is applied. ``collision="stage11"`` carries the Stage 11
    URDF's compound mesh proxies into RSS's existing ``AssetSpec.collision_paths``;
    provide ``derived_asset_dir`` for the small scale/origin-baked proxy files.
    The alternative collider approximations remain explicit choices. RSS
    computes inertia from the visual mesh and stated mass; the Stage 11
    inertial frame is not represented by ``ObjectSpec`` and still needs review.
    """
    if collision not in {"stage11", "convex_hull", "bounding_box"}:
        raise ValueError(f"unsupported RSS collision approximation: {collision!r}")
    if collision == "stage11" and derived_asset_dir is None:
        raise ValueError("derived_asset_dir is required when collision='stage11'")

    mesh_root = Path(canonical_mesh_dir).resolve()
    pose_root = Path(pose_info_dir).resolve()
    stage11_root = Path(stage11_dir).resolve()
    stage12_root = Path(stage12_dir).resolve()
    objects_path = stage11_root / "scene_objects_info.json"
    poses_path = stage12_root / "pb_scene_poses.json"
    objects = _read_json(objects_path)
    poses = _read_json(poses_path)
    if not objects:
        raise ValueError(f"{objects_path}: no rigid objects were produced")

    # Import late so DexAgent workflows that do not use RSS remain importable
    # without the RoboSimStudio package installed.
    from robosimstudio.specs import AssetSpec, ObjectSpec

    specs = []
    names: set[str] = set()
    for key, record in sorted(objects.items(), key=lambda item: int(item[0])):
        if not isinstance(record, dict):
            raise TypeError(f"{objects_path}: object {key!r} must be a JSON object")
        name = str(record.get("name", ""))
        category = str(record.get("category", ""))
        model = str(record.get("model", ""))
        if not name or not category or not model:
            raise ValueError(
                f"{objects_path}: object {key!r} needs name, category and model"
            )
        if any(
            Path(component).name != component
            or component in {".", ".."}
            or "\\" in component
            for component in (name, category, model)
        ):
            raise ValueError(
                f"{objects_path}: object {key!r} has a name/category/model that is not a path component"
            )
        if name in names:
            raise ValueError(f"{objects_path}: duplicate rigid object name {name!r}")
        names.add(name)

        mesh_path = (mesh_root / f"{name}.glb").resolve()
        pose_path = (pose_root / f"{name}.json").resolve()
        urdf_path = (
            stage11_root / "objects" / category / model / "urdf" / f"{model}.urdf"
        ).resolve()
        for required in (mesh_path, pose_path, urdf_path):
            if not required.is_file():
                raise FileNotFoundError(
                    f"SimFoundry rigid object {name!r} is missing {required}"
                )

        try:
            position, quaternion = poses[name]
            xyz = tuple(float(value) for value in position)
            quat = tuple(float(value) for value in quaternion)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"{poses_path}: expected [position, xyzw quaternion] for {name!r}"
            ) from exc
        if (
            len(xyz) != 3
            or len(quat) != 4
            or not all(math.isfinite(v) for v in (*xyz, *quat))
        ):
            raise ValueError(
                f"{poses_path}: {name!r} pose must contain 3 finite positions and 4 finite xyzw values"
            )
        norm = math.sqrt(sum(component * component for component in quat))
        if norm < 1e-8 or abs(norm - 1.0) > 1e-3:
            raise ValueError(
                f"{poses_path}: {name!r} quaternion must be normalized, got norm {norm:g}"
            )
        quat = tuple(component / norm for component in quat)

        try:
            friction = float(record["friction"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"{objects_path}: {name!r} needs a numeric friction coefficient"
            ) from exc
        if not math.isfinite(friction) or friction < 0:
            raise ValueError(
                f"{objects_path}: {name!r} friction must be finite and non-negative"
            )

        scale = _scale_from_pose(pose_path)
        collision_paths = ()
        collider = collision
        if collision == "stage11":
            collision_paths = _stage11_collision_paths(
                urdf_path,
                name=name,
                asset_scale=scale,
                output_dir=Path(derived_asset_dir),
            )
            collider = ""

        specs.append(
            ObjectSpec(
                name=name,
                category=category,
                asset=AssetSpec(
                    path=str(mesh_path), scale=scale, collision_paths=collision_paths
                ),
                pose=xyz,
                quat=quat,
                mass=_single_link_mass(urdf_path),
                friction=friction,
                collider=collider,
            )
        )
    return specs


__all__ = ["Collision", "simfoundry_rigid_specs"]
