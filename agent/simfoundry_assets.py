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

Collision = Literal["convex_hull", "bounding_box"]


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


def simfoundry_rigid_specs(
    *,
    canonical_mesh_dir: str | Path,
    pose_info_dir: str | Path,
    stage11_dir: str | Path,
    stage12_dir: str | Path,
    collision: Collision,
) -> list:
    """Build RSS ``ObjectSpec`` values from one SimFoundry rigid reconstruction.

    ``pose_info_dir`` is the exact Stage 8 directory selected by the pipeline:
    ``info`` or ``info_interactive...``. Stage 12's ``pb_scene_poses.json`` is
    preferred because its positions already include the camera-to-world
    transform and its PyBullet layout stabilization. Use these specs in an RSS
    cell whose work-surface frame is the world origin (normally ``cell="void"``)
    or convert the world poses to the chosen cell's work-surface frame.

    The Stage 8 pre-scale is baked into the canonical GLB, so only the residual
    ``z_up.scale`` is applied. RSS computes inertia from the imported mesh and
    stated mass; SimFoundry's URDF collision proxies and inertial frame are not
    copied by this first adapter. ``collision`` is required to make that choice
    explicit; the resulting scene still needs RSS physics acceptance.
    """
    if collision not in {"convex_hull", "bounding_box"}:
        raise ValueError(f"unsupported RSS collision approximation: {collision!r}")

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

        specs.append(
            ObjectSpec(
                name=name,
                category=category,
                asset=AssetSpec(path=str(mesh_path), scale=_scale_from_pose(pose_path)),
                pose=xyz,
                quat=quat,
                mass=_single_link_mass(urdf_path),
                friction=friction,
                collider=collision,
            )
        )
    return specs


__all__ = ["Collision", "simfoundry_rigid_specs"]
