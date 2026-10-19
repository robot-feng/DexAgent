"""Run one supervised phase of the optional SimFoundry cleanup workflow."""
from __future__ import annotations

import base64
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback


def _load(job_dir: Path) -> dict:
    return json.loads((job_dir / 'job.json').read_text())


def _save(job_dir: Path, job: dict) -> None:
    from datetime import datetime, timezone
    job['updated_at'] = datetime.now(timezone.utc).isoformat()
    temporary = job_dir / 'job.json.tmp'
    temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2))
    temporary.replace(job_dir / 'job.json')


def _set_phase(job_dir: Path, status: str, step: str, message: str) -> dict:
    job = _load(job_dir)
    job.update(status=status, current_step=step, message=message)
    _save(job_dir, job)
    return job


def _hydra_string(value: str) -> str:
    """Quote a scalar for Hydra's override grammar (arguments are never shell-evaluated)."""
    return json.dumps(value, ensure_ascii=False)


def _run(job_dir: Path, command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    with (job_dir / 'worker.log').open('ab') as log:
        log.write(('\n$ ' + ' '.join(command) + '\n').encode('utf-8', errors='replace'))
        log.flush()
        subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)


def _runner_command(manager: Path, env_name: str, script: Path, *overrides: str) -> list[str]:
    return [str(manager), 'run', '-n', env_name, 'python', str(script), *overrides]


def _build_object_categories(job: dict) -> list[str]:
    """Reuse reviewed cleanup labels as Stage 5 categories instead of proposing twice."""
    candidates = (job.get('include_objects') or job.get('proposed_objects')
                  or job.get('detected_objects') or [])
    skipped = {str(value).strip().casefold() for value in job.get('skip_objects', []) if str(value).strip()}
    categories, seen = [], set()
    for value in candidates:
        if not isinstance(value, str):
            continue
        name = ' '.join(value.split())
        key = name.casefold()
        if name and key not in skipped and key not in seen:
            categories.append(name)
            seen.add(key)
    return categories


def _agent_object_proposal(job_dir: Path, keyframe: Path, job: dict) -> list[str]:
    """Ask the existing DexAgent external-model transport for typed object labels."""
    from agent.external import ExternalLLM

    transport = job_dir / 'agent_external'
    job = _load(job_dir)
    job.update(current_step='等待 Codex external 对象提案', proposal_transport_dir=str(transport),
               message='关键帧已准备。请通过 DexAgent external 请求/响应给出要从静态背景移除的对象；可在工作台检查返回结果。')
    _save(job_dir, job)
    llm = ExternalLLM(transport, timeout_s=float(os.environ.get('WORKBENCH_AGENT_PROPOSAL_TIMEOUT_S', '43200')))
    manual = job.get('include_objects', [])
    skip = job.get('skip_objects', [])
    content = llm.complete(
        system=(
            'You are the scene-understanding provider in EmbodiForge. Inspect the representative frame from a moving-camera scene scan. '
            'Return concise SAM3 text prompts for every foreground item that must be removed from the static background, including robot arms, '
            'grippers, hands, and task objects. Do not include the floor, table/support surface, walls, or fixed room structure. '
            'Respect operator-provided removals and exclusions. Do not invent objects that are not visible. Return one structured tool result only.'
        ),
        messages=[{'role': 'user', 'content': [
            {'type': 'text', 'text': json.dumps({
                'floor_category': job['floor_category'],
                'operator_include_objects': manual,
                'operator_skip_objects': skip,
                'task': 'Propose visible foreground removal targets as short text prompts. Include confidence and a short evidence note for each target.',
            }, ensure_ascii=False)},
            {'type': 'image', 'mime': 'image/png', 'data': base64.b64encode(keyframe.read_bytes()).decode('ascii')},
        ]}],
        tools=[{'name': 'propose_objects', 'description': 'Return foreground objects to remove from the static background.',
                'input_schema': {'type': 'object', 'properties': {
                    'objects': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 20},
                    'evidence': {'type': 'string'},
                }, 'required': ['objects'], 'additionalProperties': False}}],
    )
    calls = [block for block in content if block.get('type') == 'tool_use' and block.get('name') == 'propose_objects']
    if len(calls) != 1:
        raise RuntimeError('Codex external proposal must return exactly one propose_objects tool result.')
    raw = calls[0]['input'].get('objects')
    if not isinstance(raw, list) or not raw or len(raw) > 20 or any(not isinstance(name, str) for name in raw):
        raise RuntimeError('Codex external proposal objects must be a nonempty list of at most 20 text prompts.')
    names, seen = [], set()
    for value in raw:
        name = ' '.join(value.split())
        if name and name.casefold() not in seen:
            names.append(name)
            seen.add(name.casefold())
    if not names:
        raise RuntimeError('Codex external returned no usable object prompts.')
    llm.close({'provider': 'codex_external', 'objects': names})
    return names


def _mask_phase(repo: Path, manager: Path, job_dir: Path, env: dict[str, str], job: dict) -> None:
    sim = repo / 'SimFoundry'
    data_root = sim / 'Data'
    scene = job['scene_name']
    source = Path(job['source_copy']).resolve()
    if not source.is_file() or not source.is_relative_to(job_dir.resolve()):
        raise RuntimeError('任务输入视频丢失或不在此任务目录中。')

    _set_phase(job_dir, 'preparing', '抽帧与统一尺寸', '使用 SimFoundry Stage 1b 生成同源帧集，供所选视频分割模型处理。')
    video_stage = sim / 'scripts/pipeline/A_reconstruction/stages/1b_process_raw_video.py'
    overrides = [
        f'root_dir={_hydra_string(str(data_root))}',
        f'scene_name={scene}',
        f's1_video.video_fpath={_hydra_string(str(source))}',
        's1_video.n_subsampled_frames=400',
        's1_video.splat_prep=true',
        's1_video.mp4_fps=12',
        's1_video.target_w=672',
        's1_video.target_h=384',
    ]
    _run(job_dir, _runner_command(manager, 'simfoundry', video_stage, *overrides), cwd=sim, env=env)

    scene_data = data_root / scene
    frames = scene_data / 's1_video/frames_subsampled_400'
    input_video = scene_data / 's1_video/input_video.mp4'
    frame_paths = sorted(frames.glob('*.png'))
    if not input_video.is_file() or not frame_paths:
        raise RuntimeError('Stage 1b 未生成有效的统一输入帧。')
    void_input = scene_data / 'auto_bg/void/input'
    void_input.mkdir(parents=True, exist_ok=True)
    (void_input / 'subsampled').symlink_to(frames, target_is_directory=True)
    (void_input / 'input_video.mp4').symlink_to(input_video)

    if job.get('proposal_backend', 'manual') == 'codex_external':
        _set_phase(job_dir, 'preparing', '等待对象提案', '关键帧已生成，正在通过 DexAgent external 请求对象移除提案。')
        proposed = _agent_object_proposal(job_dir, frame_paths[0], job)
        job = _load(job_dir)
        combined = list(job.get('include_objects', [])) + proposed
        names, seen = [], set()
        for value in combined:
            name = ' '.join(str(value).split())
            if name and name.casefold() not in seen:
                names.append(name)
                seen.add(name.casefold())
        job.update(proposed_objects=proposed, include_objects=names,
                   message=f'Codex external 提议 {len(proposed)} 个对象：{", ".join(proposed)}。正在生成并传播 SAM mask。')
        _save(job_dir, job)

    model_name = ('SAM3 单模型双向跟踪' if job['propagation_model'] == 'sam3'
                  else 'SAM3 关键帧分割 + SAM2 双向传播')
    _set_phase(job_dir, 'preparing', model_name,
               f'正在使用 {model_name} 处理全部抽样帧。')
    mask_stage = sim / 'scripts/pipeline/A_reconstruction/stages/auto_bg_reconstruction/1_generate_quadmask_for_void.py'
    override_values = [
        f'scene_name={scene}',
        f'floor_category={_hydra_string(job["floor_category"])}',
        f's1_quadmask.use_gemini_proposal={str(job.get("proposal_backend") == "gemini").lower()}',
        's1_quadmask.include_objects=' + json.dumps(job['include_objects'], ensure_ascii=False),
        's1_quadmask.skip=' + json.dumps(job['skip_objects'], ensure_ascii=False),
        f's1_quadmask.propagation_model={job["propagation_model"]}',
    ]
    _run(job_dir, _runner_command(manager, 'simfoundry', mask_stage, *override_values), cwd=sim, env=env)
    overlay = void_input / 'keyframe_masks_debug/all_objects_overlay.png'
    quadmask = void_input / 'quadmask_0.mp4'
    metadata = void_input / 'quadmask_meta.json'
    if not overlay.is_file() or not quadmask.is_file() or not metadata.is_file():
        raise RuntimeError('SAM3/SAM2 阶段结束，但缺少关键帧 overlay、传播 mask 或元数据。')
    meta = json.loads(metadata.read_text())
    if not meta.get('objects'):
        raise RuntimeError('SAM3 没有为任何对象生成 mask；请调整对象名称后重试。')
    job = _load(job_dir)
    job.update(status='review_mask', current_step=f'检查 {job["propagation_model"].upper()} mask 与跨帧传播结果',
               message=f'已生成 {len(meta["objects"])} 个对象的 mask。检查 overlay 和黑白 mask 视频后再确认补洞。',
               detected_objects=meta['objects'])
    _save(job_dir, job)


def _inpaint_phase(repo: Path, manager: Path, job_dir: Path, env: dict[str, str], job: dict) -> None:
    sim = repo / 'SimFoundry'
    scene = job['scene_name']
    stage_root = sim / 'scripts/pipeline/A_reconstruction/stages/auto_bg_reconstruction'
    data_root = sim / 'Data' / scene
    common = [f'scene_name={scene}']
    _set_phase(job_dir, 'inpainting', 'VOID Pass 1/2 视频补洞',
               'Pass 1 生成时间连续的粗修复，再由 Pass 2 细化；该步骤可能运行较久。')
    _run(job_dir, _runner_command(manager, 'simfoundry', stage_root / '2_run_void_pass1.py', *common),
         cwd=sim, env=env)
    _run(job_dir, _runner_command(manager, 'simfoundry', stage_root / '3_run_void_pass2.py', *common),
         cwd=sim, env=env)
    cleaned = data_root / 'auto_bg/void/pass2/pass2.mp4'
    if not cleaned.is_file() or not (data_root / 'auto_bg/void/pass2/cleaned_frames').is_dir():
        raise RuntimeError('VOID 阶段结束，但未生成最终清理视频与帧集。')
    job = _load(job_dir)
    job.update(status='review_clean', current_step='检查清理视频',
               message='VOID 两遍补洞已完成。请检查机器人和物体是否移除、背景是否出现伪影。')
    _save(job_dir, job)


def _build_phase(repo: Path, manager: Path, job_dir: Path, env: dict[str, str], job: dict) -> None:
    sim = repo / 'SimFoundry'
    scene = job['scene_name']
    source = Path(job['source_copy']).resolve()
    object_categories = _build_object_categories(job)
    proposal_note = (f'复用已确认的 {len(object_categories)} 个对象类别，跳过 Stage 5 Gemini 对象提案。'
                    if object_categories else '没有可复用的对象类别，Stage 5 将使用上游默认提案流程。')
    _set_phase(job_dir, 'building', 'SimFoundry 场景重建',
               f'先运行原生 A 重建（{proposal_note}），再复用已检查的 mask/补洞结果训练并桥接背景高斯。')
    run_a = sim / 'scripts/pipeline/A_reconstruction/run.sh'
    canonical = [
        'bash', str(run_a), '--scene-name', scene, '--root-dir', str(sim / 'Data'),
        '--video-fpath', str(source), '--no-stream',
        '--env-simfoundry', 'simfoundry', '--env-nerfstudio', 'nerfstudio_simfoundry',
        '--env-da3', 'da3', '--env-mesh', 'hunyuan', '--env-b1k', 'simfoundry', '--',
        's1_video.n_subsampled_frames=400', 's1_video.splat_prep=true',
        's1_video.mp4_fps=12', 's1_video.target_w=672', 's1_video.target_h=384',
        's2_depth.backend=da3', 's14_og.interactive=false', 's14_og.headless=true',
        's3_ground.frame_selection.mode=heuristic',
        f's3_ground.floor_categories={json.dumps([job["floor_category"]], ensure_ascii=False)}',
    ]
    if object_categories:
        canonical.append(f's5_scene.force_categories={json.dumps(object_categories, ensure_ascii=False)}')
    env = dict(env, OMNIGIBSON_HEADLESS='1')
    _run(job_dir, canonical, cwd=sim, env=env)

    _set_phase(job_dir, 'building', 'AutoBG 背景高斯对齐',
               '完整 A 场景已生成；正在将清理背景映射回场景坐标系并写入最终场景。')
    align = sim / 'scripts/pipeline/A_reconstruction/stages/auto_bg_reconstruction/run_auto_align.sh'
    command = ['bash', str(align), scene, str(source), '--num-frames', '400',
               '--floor-category', job['floor_category']]
    _run(job_dir, command, cwd=sim, env=env)
    final_scene = sim / 'assets/scenes' / scene / f'{scene}_scene_state_auto_bg.json'
    if not final_scene.is_file():
        raise RuntimeError('AutoBG 结束，但未找到最终 scene_state_auto_bg.json。')
    job = _load(job_dir)
    job.update(status='ready', current_step='重建完成', message='清理背景与重建场景均已生成。',
               scene_path=str(final_scene))
    _save(job_dir, job)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--job-dir', type=Path, required=True)
    parser.add_argument('--phase', choices=('mask', 'inpaint', 'build'), required=True)
    args = parser.parse_args(argv)
    repo, job_dir = args.repo.resolve(), args.job_dir.resolve()
    job = _load(job_dir)
    manager_value = os.environ.get('SIMFOUNDRY_ENV_RUNNER')
    if not manager_value:
        raise RuntimeError('SIMFOUNDRY_ENV_RUNNER is not configured')
    manager = Path(manager_value)
    env = dict(os.environ, PYTHONUNBUFFERED='1')
    try:
        if args.phase == 'mask':
            _mask_phase(repo, manager, job_dir, env, job)
        elif args.phase == 'inpaint':
            _inpaint_phase(repo, manager, job_dir, env, job)
        else:
            _build_phase(repo, manager, job_dir, env, job)
    except Exception as exc:
        with (job_dir / 'worker.log').open('ab') as log:
            log.write(('\n' + traceback.format_exc() + '\n').encode('utf-8', errors='replace'))
        job = _load(job_dir)
        job.update(status='failed', current_step='任务失败',
                   message=f'{type(exc).__name__}: {exc}；日志和中间产物已保留。')
        _save(job_dir, job)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
