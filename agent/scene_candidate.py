"""Inspect and freeze native MJCF candidates; physics stays in the RSS runner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from agent.run_reports import atomic_json, sha256_file


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def free_objects(model):
    import mujoco

    if not model.nkey:
        raise ValueError("场景缺少初始关键帧")
    result = []
    for j in range(model.njnt):
        if model.jnt_type[j] != mujoco.mjtJoint.mjJNT_FREE:
            continue
        body = int(model.jnt_bodyid[j])
        name, joint = model.body(body).name, model.joint(j).name
        if (
            not name
            or not joint
            or name.startswith("robot_")
            or model.body_parentid[body] != 0
        ):
            raise ValueError("只支持具有唯一名称、位于世界根节点的自由物体")
        start = int(model.jnt_qposadr[j])
        pose = model.key_qpos[0, start : start + 7]
        result.append(
            {
                "name": name,
                "joint": joint,
                "position_m": pose[:3].tolist(),
                "quaternion_wxyz": pose[3:].tolist(),
            }
        )
    if not result:
        raise ValueError("场景没有可调整的自由物体")
    return result


def inspect_scene(path):
    import mujoco
    from tool_library.common.rigid_robot import compile_rigid

    path = Path(path).resolve()
    before = sha256_file(path)
    rig = compile_rigid(path)
    model = rig.model
    objects = free_objects(model)
    joints = [j for j in range(model.njnt) if model.joint(j).name.startswith("robot_")]
    if (
        len(joints) != 56
        or model.nu != 56
        or any(model.jnt_type[j] != mujoco.mjtJoint.mjJNT_HINGE for j in joints)
    ):
        raise ValueError("当前候选验收支持 DexAgent YAM + Sharpa 的完整 MJCF 任务场景")
    if any(model.key_qvel[0]) or any(
        model.body_mocapid[i] >= 0 and model.body(i).name != "camrig"
        for i in range(model.nbody)
    ):
        raise ValueError("候选落稳要求静止初态，任务物体不能由 mocap 驱动")
    assets = {
        e.get("file")
        for e in ET.fromstring(rig.xml).findall("./asset/*")
        if e.get("file")
    }
    hashes = {}
    for asset in sorted(assets):
        if not Path(asset).is_absolute():
            raise ValueError("编译后的资产路径必须为绝对路径")
        hashes[asset] = sha256_file(asset)
    descriptor = {
        "source_sha256": before,
        "compiled_sha256": rig.scene_sha256,
        "asset_sha256": hashes,
    }
    if sha256_file(path) != before:
        raise ValueError("场景读取期间发生变化，请重新读取")
    return {"sha256": digest(descriptor), "objects": objects, "source": descriptor}, rig


def verify_snapshot(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("schema") != "embodiforge.scene_candidate.v1":
        raise ValueError("不是受支持的场景候选快照")
    identity = manifest.copy()
    expected = identity.pop("candidate_sha256")
    if digest(identity) != expected or "scene.xml" not in manifest["files"]:
        raise ValueError("候选清单身份不匹配")
    for name, sha in manifest["files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or sha256_file(path) != sha:
            raise ValueError(f"候选文件身份不匹配：{name}")
    return manifest


def freeze_candidate(request, output):
    """Copy every compiled asset before changing only initial free-body positions."""
    import mujoco
    import numpy as np

    output = Path(output)
    if output.exists():
        raise ValueError("候选输出已存在")
    if request.get("snapshot"):
        source = Path(request["snapshot"]).resolve()
        manifest = verify_snapshot(source)
        if manifest["candidate_sha256"] != request["candidate_sha256"]:
            raise ValueError("重试的候选身份不匹配")
        output.mkdir()
        for name in manifest["files"]:
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
        atomic_json(output / "manifest.json", manifest)
        return verify_snapshot(output)
    descriptor, rig = inspect_scene(request["source"])
    if descriptor["sha256"] != request["source_sha256"]:
        raise ValueError("场景或资产已改变，请重新读取后再创建候选")
    objects = {item["name"]: item for item in descriptor["objects"]}
    parent = None
    if (Path(request["source"]).parent / "manifest.json").is_file():
        parent = verify_snapshot(Path(request["source"]).parent)["candidate_sha256"]
    offsets = request.get("offsets", [])
    names = set()
    spec = mujoco.MjSpec.from_string(rig.xml)
    initial = rig.model.key_qpos[0].copy()
    for change in offsets:
        name = change["name"]
        offset = np.asarray(change["translation_m"], dtype=float)
        if (
            name not in objects
            or name in names
            or offset.shape != (3,)
            or not np.isfinite(offset).all()
            or np.any(abs(offset) > 1)
        ):
            raise ValueError(
                "物体不能重复，且必须属于当前场景；每轴平移需在 -1 至 1 米内"
            )
        names.add(name)
        start = int(rig.model.jnt_qposadr[rig.model.joint(objects[name]["joint"]).id])
        initial[start : start + 3] += offset
        spec.body(name).pos = initial[start : start + 3]
        spec.body(name).quat = initial[start + 3 : start + 7]
    spec.keys[0].qpos = initial
    output.mkdir()
    # Canonical MJCF contains expanded includes and absolute mesh/texture refs.
    # The frozen version uses only content-addressed, run-local assets.
    root = ET.fromstring(spec.to_xml())
    for element in root.findall("./asset/*"):
        asset = element.get("file")
        if not asset:
            continue
        expected = descriptor["source"]["asset_sha256"][asset]
        name = f"assets/{expected}{Path(asset).suffix.lower()}"
        target = output / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(asset, target)
        if sha256_file(target) != expected:
            raise ValueError("复制期间场景资产发生变化")
        element.set("file", name)
    (output / "scene.xml").write_text(ET.tostring(root, encoding="unicode"))
    model = mujoco.MjModel.from_xml_path(str(output / "scene.xml"))
    if not np.allclose(model.key_qpos[0], initial, atol=1e-10, rtol=0):
        raise ValueError("候选初始位姿未被完整保留")
    manifest = {
        "schema": "embodiforge.scene_candidate.v1",
        "source": descriptor["source"],
        "source_identity_sha256": descriptor["sha256"],
        "offsets": offsets,
        "source_run_id": request.get("source_run_id"),
        "parent_candidate_sha256": parent,
        "frame": "dexagent_rig = rss_world; metres; z up",
        "objects": free_objects(model),
        "files": {
            str(p.relative_to(output)): sha256_file(p)
            for p in sorted(output.rglob("*"))
            if p.is_file()
        },
    }
    manifest["candidate_sha256"] = digest(manifest)
    atomic_json(output / "manifest.json", manifest)
    return verify_snapshot(output)


if __name__ == "__main__":
    import argparse
    import contextlib
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    # Native compilers may log to stdout; the caller needs exactly one JSON value.
    with contextlib.redirect_stdout(sys.stderr):
        description, _ = inspect_scene(args.source)
    print(json.dumps(description, ensure_ascii=False))
