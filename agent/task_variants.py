"""Freeze accepted layout inputs, then enter the existing DexAgent planner."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

from agent.run_reports import atomic_json, sha256_file
from agent.scene_candidate import digest, freeze_candidate, verify_snapshot


def require_acceptance(report, identity):
    try:
        checks = [c for c in report.get("checks", []) if c.get("required", True)]
        valid = (
            report.get("metrics", {}).get("scope") == "scene_candidate_validation"
            and report.get("status") in {"passed", "warning"}
            and bool(checks)
            and all(c.get("status") == "passed" for c in checks)
            and report.get("inputs", {}).get("candidate", {}).get("sha256") == identity
        )
    except (AttributeError, TypeError):
        valid = False
    if not valid:
        raise ValueError("候选尚未通过必需物理检查，或报告与候选身份不一致")


def accepted_candidate(run, expected=None):
    run = Path(run).resolve()
    candidate = verify_snapshot(run / "candidate")
    if (run / "stage_report.json").is_symlink():
        raise ValueError("候选验收报告不能通过符号链接引入")
    report = json.loads((run / "stage_report.json").read_text())
    require_acceptance(report, candidate["candidate_sha256"])
    if expected is not None and expected != candidate["candidate_sha256"]:
        raise ValueError("候选身份与请求不一致")
    return candidate


def local_input(directory, name):
    path = directory / name
    if (
        path.is_symlink()
        or not path.resolve().is_relative_to(directory)
        or any(
            p.is_symlink()
            for p in path.parents
            if p != directory and p.is_relative_to(directory)
        )
    ):
        raise ValueError("任务输入必须位于运行目录内，不能使用符号链接")
    return path


def input_files(source):
    """Only registered run-local files; symlinks cannot import unrelated data."""
    source = Path(source).resolve()
    required = [
        "stage1/scene_semantics.json",
        "stage2/scene.xml",
        "stage2/scene_recon.json",
        "stage2/camera.json",
        "stage2/reference_evidence_recipes.json",
        "stage3/stage4_task/human_video.mp4",
    ]
    for name in required:
        if not (source / name).is_file():
            raise ValueError(f"源任务缺少 {name}；请先完成其原生输入与来源验证")
    paths = [source / required[0], source / required[-1]]
    paths += [p for p in sorted((source / "stage2").rglob("*")) if p.is_file()]
    for path in paths:
        local_input(source, path.relative_to(source))
    reuse = source / "stage2/reuse.json"
    if reuse.is_file():
        declaration = json.loads(reuse.read_text())
        if not isinstance(declaration, dict) or declaration.get("reused_from"):
            raise ValueError("源任务依赖外部示例证书；请先封存为独立来源输入")
    return {str(p.relative_to(source)): sha256_file(p) for p in paths}


def describe_task(candidate_run, source_run):
    candidate = accepted_candidate(candidate_run)
    files = input_files(source_run)
    semantics = json.loads(
        (Path(source_run) / "stage1/scene_semantics.json").read_text()
    )
    if (
        not isinstance(semantics, dict)
        or not isinstance(semantics.get("subgoals"), list)
        or not semantics["subgoals"]
    ):
        raise ValueError("源任务没有可供重新规划的子目标")
    identity = {
        "candidate_sha256": candidate["candidate_sha256"],
        "source_candidate_report_sha256": sha256_file(
            Path(candidate_run) / "stage_report.json"
        ),
        "source_files": files,
    }
    return {
        "sha256": digest(identity),
        **identity,
        "task": str(
            semantics.get("task_success") or semantics.get("task") or "沿用源任务子目标"
        ),
    }


def verify_task_snapshot(directory, expected, manifest_sha256=None):
    directory = Path(directory).resolve()
    manifest_path = local_input(directory, "task_inputs.json")
    if manifest_sha256 and sha256_file(manifest_path) != manifest_sha256:
        raise ValueError("任务输入清单发生变化")
    manifest = json.loads(manifest_path.read_text())
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "embodiforge.task_variant.v1"
        or manifest.get("input_identity_sha256") != expected
        or manifest.get("old_controls_reused") is not False
    ):
        raise ValueError("任务输入快照身份不一致")
    required = {
        "stage1/scene_semantics.json",
        "stage2/scene.xml",
        "stage2/scene_recon.json",
        "stage2/camera.json",
        "stage2/reference_evidence_recipes.json",
        "inputs/video.mp4",
        "candidate/scene.xml",
        "candidate/manifest.json",
        "inputs/planning_model_identity.json",
        "inputs/source_candidate_report.json",
    }
    if (
        not isinstance(manifest.get("files"), dict)
        or not required.issubset(manifest["files"])
        or not manifest.get("proposal_model_sha256")
    ):
        raise ValueError("任务输入快照缺少必需文件")
    for name, sha in manifest["files"].items():
        path = local_input(directory, name)
        if sha256_file(path) != sha:
            raise ValueError(f"任务输入快照发生变化：{name}")
    if (
        verify_snapshot(directory / "candidate")["candidate_sha256"]
        != manifest["candidate_sha256"]
    ):
        raise ValueError("任务与候选快照身份不一致")
    require_acceptance(
        json.loads((directory / "inputs/source_candidate_report.json").read_text()),
        manifest["candidate_sha256"],
    )
    proof = json.loads((directory / "inputs/planning_model_identity.json").read_text())
    if (
        not isinstance(proof, dict)
        or proof.get("compiled_physics_equal") is not True
        or proof.get("compiled_model_sha256") != manifest.get("compiled_model_sha256")
        or proof.get("proposal_model_sha256") != manifest["proposal_model_sha256"]
        or proof.get("planning_scene_sha256") != manifest["files"]["stage2/scene.xml"]
        or proof.get("candidate_scene_sha256")
        != manifest["files"]["candidate/scene.xml"]
    ):
        raise ValueError("规划与执行模型的身份凭据不一致")
    return manifest


def relocate_references(recon, recipes, source, destination, files):
    """Keep active native references inside the sealed Stage 2 tree."""

    def relative(value):
        path = local_input(source, value).resolve()
        name = path.relative_to(source).as_posix()
        expected = files.get("stage2/" + name)
        copied = local_input(destination, name)
        if not expected or not copied.is_file() or sha256_file(copied) != expected:
            raise ValueError(f"来源引用未纳入冻结输入：{value}")
        return name

    def bind(entry, key):
        if entry.get(key):
            entry[key] = relative(entry[key])

    recon.update(scene_xml="scene.xml", scene_semantic="../stage1/scene_semantics.json")
    recon.pop("frame0_compare_png", None)
    for key in ("frame0", "camera_json"):
        bind(recon, key)
    for obj in recon.get("objects", []):
        for key in ("mask", "asset_xml"):
            bind(obj, key)
        hidden = obj.get("hidden_at_frame0")
        if isinstance(hidden, dict):
            bind(hidden, "reveal_mask")
    # These are exact temporal lookup keys, not external files to open. Preserve
    # archival keys outside the source tree, and relocate local ones portably.
    index = {}
    for value, record in recipes.get("frame_paths", {}).items():
        path = (source / value).resolve()
        key = relative(value) if path.is_relative_to(source) else value
        if key in index and index[key] != record:
            raise ValueError("来源帧的精确时间索引存在冲突")
        index[key] = record
    if "frame_paths" in recipes:
        recipes["frame_paths"] = index


def planning_scene(source, candidate, destination, evidence_dir):
    """Reuse the original proposal; its compiled physics must equal the candidate."""
    import mujoco
    from agent.artifacts import bundle_scene, model_digest
    from tool_library.common import episode as E, rigid_robot as RR

    evidence_dir.mkdir(parents=True, exist_ok=True)
    expanded = evidence_dir / "source_proposal.xml"
    model, _ = E.build_execution_model(
        source,
        out_xml=str(expanded),
        scene_recon_json=str(Path(source).parent / "scene_recon.json"),
    )
    if RR.is_rigid(model):
        raise ValueError(
            "源任务没有可用于新规划的 proposal 场景，不能反向猜测机器人结构"
        )
    physical = mujoco.MjModel.from_xml_path(str(candidate))
    spec = mujoco.MjSpec.from_file(str(expanded))
    initial = model.key_qpos[0].copy()
    for j in range(physical.njnt):
        if physical.jnt_type[j] != mujoco.mjtJoint.mjJNT_FREE:
            continue
        name = physical.body(int(physical.jnt_bodyid[j])).name
        bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
        joints = [
            k
            for k in range(model.njnt)
            if model.jnt_bodyid[k] == bid
            and model.jnt_type[k] == mujoco.mjtJoint.mjJNT_FREE
        ]
        if len(joints) != 1:
            raise ValueError(f"候选对象不能映射到源规划场景：{name}")
        a, b = int(model.jnt_qposadr[joints[0]]), int(physical.jnt_qposadr[j])
        initial[a : a + 7] = physical.key_qpos[0, b : b + 7]
        spec.body(name).pos = initial[a : a + 3]
        spec.body(name).quat = initial[a + 3 : a + 7]
    spec.keys[0].qpos = initial
    proposal = spec.compile()
    RR.register_model(proposal, spec)
    compiled = RR.compile_rigid(proposal)
    expected = model_digest(physical)
    if model_digest(compiled.model) != expected:
        raise ValueError("源 proposal 编译后的完整物理模型与已验收候选不一致")
    proposed = evidence_dir / "planning_proposal.xml"
    proposed.write_text(spec.to_xml())
    proposal_digest = bundle_scene(proposed, destination)
    # Asset rebasing must preserve both representations and their state mapping.
    if model_digest(RR.compile_rigid(destination).model) != expected:
        raise ValueError("规划场景打包后物理模型身份发生变化")
    proof = {
        "schema": "embodiforge.proposal_identity.v1",
        "compiled_model_sha256": expected,
        "proposal_model_sha256": proposal_digest,
        "compiled_physics_equal": True,
        "candidate_scene_sha256": sha256_file(candidate),
        "planning_scene_sha256": sha256_file(destination),
    }
    atomic_json(evidence_dir / "planning_model_identity.json", proof)
    return proof


def prepare_task(request, output):
    """Relocate evidence; never carry old controls or certify a new perception run."""
    import mujoco
    import numpy as np
    from scipy.spatial.transform import Rotation
    from agent.input_identity import check_video_identity

    output = Path(output).resolve()
    if request.get("snapshot"):
        original = Path(request["snapshot"]).resolve()
        manifest = verify_task_snapshot(
            original, request["sha256"], request["snapshot_sha256"]
        )
        if output.exists():
            raise ValueError("任务变体输出已存在，请使用新目录")
        for name in manifest["files"]:
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original / name, target)
        atomic_json(output / "task_inputs.json", manifest)
        from agent.artifacts import model_digest
        from tool_library.common.rigid_robot import compile_rigid

        if (
            model_digest(compile_rigid(output / "stage2/scene.xml").model)
            != manifest["compiled_model_sha256"]
        ):
            raise ValueError("当前编译环境与候选物理身份不一致，请重新验收")
        return verify_task_snapshot(output, request["sha256"])
    source = Path(request["source_run"]).resolve()
    candidate_run = Path(request["candidate_run"]).resolve()
    descriptor = describe_task(candidate_run, source)
    if descriptor["sha256"] != request["sha256"]:
        raise ValueError("候选或任务输入已变化，请重新读取后再规划")
    video = source / "stage3/stage4_task/human_video.mp4"
    original_semantics = source / "stage1/scene_semantics.json"
    identity = check_video_identity(video, original_semantics)
    if identity.get("ok") is not True or not identity.get("identity_verified"):
        raise ValueError(
            "源任务视频身份未通过独立核对：" + "; ".join(identity.get("issues", []))
        )
    if output.exists():
        raise ValueError("任务变体输出已存在，请使用新目录")
    output.mkdir(parents=True)
    candidate = freeze_candidate(
        {
            "snapshot": str(candidate_run / "candidate"),
            "candidate_sha256": descriptor["candidate_sha256"],
        },
        output / "candidate",
    )
    frozen = output / "inputs/source_task"
    (output / "inputs").mkdir(exist_ok=True)
    report_copy = output / "inputs/source_candidate_report.json"
    shutil.copyfile(candidate_run / "stage_report.json", report_copy)
    if sha256_file(report_copy) != descriptor["source_candidate_report_sha256"]:
        raise ValueError("冻结期间候选验收报告发生变化")
    for name, expected in descriptor["source_files"].items():
        target = frozen / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
        if sha256_file(target) != expected:
            raise ValueError("复制期间源任务输入发生变化")
    if describe_task(candidate_run, source)["sha256"] != request["sha256"]:
        raise ValueError("冻结期间源候选或任务输入发生变化")

    # Old reports remain historical evidence under inputs/, not live stages.
    def old_results(folder, names):
        return {
            n
            for n in names
            if n in {"stage_report.json", "run.json", "failure_report.json"}
            or n.startswith(".")
            or n.endswith(".log")
        }

    shutil.copytree(frozen / "stage2", output / "stage2", ignore=old_results)
    (output / "inputs/video.mp4").parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        frozen / "stage3/stage4_task/human_video.mp4", output / "inputs/video.mp4"
    )
    sem = json.loads((frozen / "stage1/scene_semantics.json").read_text())
    sem.setdefault("video", {}).update(
        path="../inputs/video.mp4", sha256=identity["actual"]["sha256"]
    )
    atomic_json(output / "stage1/scene_semantics.json", sem)
    recon = json.loads((frozen / "stage2/scene_recon.json").read_text())
    recipes = json.loads(
        (frozen / "stage2/reference_evidence_recipes.json").read_text()
    )
    relocate_references(
        recon, recipes, source / "stage2", output / "stage2", descriptor["source_files"]
    )
    atomic_json(output / "stage2/scene_recon.json", recon)
    atomic_json(output / "stage2/reference_evidence_recipes.json", recipes)
    proof = planning_scene(
        output / "stage2/scene.xml",
        output / "candidate/scene.xml",
        output / "stage2/scene.xml",
        output / "inputs",
    )
    model = mujoco.MjModel.from_xml_path(str(output / "stage2/scene.xml"))
    state = mujoco.MjData(model)
    state.qpos[:] = model.key_qpos[0]
    mujoco.mj_forward(model, state)
    mapped = set()
    for obj in recon.get("objects", []):
        name = obj.get("root_body")
        bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name or "")
        if bid < 0:
            raise ValueError(f"任务对象无法绑定候选实体：{obj.get('id')}")
        mapped.add(name)
        x, y, z = state.xpos[bid]
        yaw = Rotation.from_matrix(state.xmat[bid].reshape(3, 3)).as_euler("xyz")[2]
        obj["pose"] = {
            "x": float(x),
            "y": float(y),
            "z": float(z),
            "yaw_deg": float(np.degrees(yaw)),
        }
        # Previous layout metrics cannot certify a different initial layout.
        obj.pop("verifiers", None)
        obj.pop("iou", None)
    if not {obj["name"] for obj in candidate["objects"]}.issubset(mapped):
        raise ValueError("候选自由物体与源任务对象不是一一对应")
    recon.update(
        scene_xml="scene.xml",
        scene_semantic="../stage1/scene_semantics.json",
        layout_variant={
            "candidate_sha256": candidate["candidate_sha256"],
            "source_inputs_sha256": descriptor["sha256"],
            "reference_role": "original task video and masks; not observations of the new layout",
            "source_stage2_revalidated": False,
        },
    )
    recon.pop("frame0_compare_png", None)
    atomic_json(output / "stage2/scene_recon.json", recon)
    # Original human hand coordinates describe the original layout only.
    for path in (output / "stage2/hands.npz", output / "stage2/hands/hands.npz"):
        if path.is_file():
            path.unlink()
    files = {
        str(p.relative_to(output)): sha256_file(p)
        for folder in ("stage1", "stage2", "candidate")
        for p in sorted((output / folder).rglob("*"))
        if p.is_file()
    }
    files["inputs/video.mp4"] = sha256_file(output / "inputs/video.mp4")
    files["inputs/planning_model_identity.json"] = sha256_file(
        output / "inputs/planning_model_identity.json"
    )
    files["inputs/source_candidate_report.json"] = sha256_file(report_copy)
    manifest = {
        "schema": "embodiforge.task_variant.v1",
        "scope": "fresh_planning_from_layout_candidate",
        "input_identity_sha256": descriptor["sha256"],
        "candidate_sha256": candidate["candidate_sha256"],
        "source_run_id": request["source_run_id"],
        "candidate_run_id": request["candidate_run_id"],
        "compiled_model_sha256": proof["compiled_model_sha256"],
        "proposal_model_sha256": proof["proposal_model_sha256"],
        "files": files,
        "old_controls_reused": False,
        "task_success_evaluated": False,
    }
    atomic_json(output / "task_inputs.json", manifest)
    return verify_task_snapshot(output, descriptor["sha256"])


def main():
    import argparse
    import os
    import sys
    from agent.config import load_config
    from agent.run_reports import finish_report, make_report

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("request", type=Path)
    parser.add_argument(
        "--to", dest="stop", choices=("stage3", "stage4"), default="stage3"
    )
    args = parser.parse_args()
    cfg = load_config(args.config)
    output = Path(cfg["run"]["output_dir"]) / cfg["run"]["name"]
    request = json.loads(args.request.read_text())
    try:
        print("[task inputs] 正在冻结并核对候选、源任务与规划场景。", flush=True)
        prepare_task(request, output)
        print(
            "[task inputs] 输入已冻结，规划场景编译后的物理身份与已验收候选一致；启动原生 Stage 3。",
            flush=True,
        )
    except Exception as exc:
        output.mkdir(parents=True, exist_ok=True)
        report = make_report(
            run=output.name, profile="pipeline_eval", stage="task_inputs"
        )
        finish_report(
            report,
            failure=f"{type(exc).__name__}: {exc}",
            next_action="重新读取候选与源任务，修复输入后创建新运行",
        )
        atomic_json(output / "stage_report.json", report)
        atomic_json(
            output / "run_summary.json",
            {
                "status": "failed",
                "full_pipeline_complete": False,
                "requested_stages": ["stage3"],
                "scope": "task_variant_input_preparation",
                "failure": report["failure"],
            },
        )
        print(report["failure"], flush=True)
        return 1
    entry = Path(__file__).resolve().parents[1] / "run_dexagent.py"
    os.execv(
        sys.executable,
        [
            sys.executable,
            str(entry),
            str(args.config),
            "--from",
            "stage3",
            "--to",
            args.stop,
            "--profile",
            "pipeline_eval",
            "--yes",
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
