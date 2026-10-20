"""Read recorded rigid planning failures for display; never package an episode."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from .diagnostics import local_report
from .reports import load_object


def local_file(run: Path, path: Path) -> Path:
    if (not path.is_relative_to(run) or not path.resolve().is_relative_to(run)
            or any(p.is_symlink() for p in (path, *path.parents) if p != run)):
        raise ValueError('diagnostic replay inputs must be regular files inside this run')
    if not path.is_file():
        raise ValueError(f'diagnostic replay input is missing: {path.name}')
    return path


def recording_sources(run: Path, report: Path) -> dict:
    """Resolve fixed native filenames, never arbitrary report-provided file paths."""
    report = local_file(run, local_report(run, report))
    data, _ = load_object(report)
    if data.get('dry_run') is not False or not isinstance(data.get('subgoals'), list):
        raise ValueError('only recorded physical trials can be replayed')
    scene = local_file(run, report.parent / 'scene_physics.xml')
    contract = next((p / 'input_contract.json' for p in report.parents
                     if p.is_relative_to(run / 'stage3') and (p / 'input_contract.json').is_file()), None)
    if contract is None:
        raise ValueError('native input contract is missing; asset identity cannot be checked')
    contract = local_file(run, contract)
    manifest, _ = load_object(contract)
    if manifest.get('schema') != 'dexagent.input_contract.v1' or not isinstance(manifest.get('sha256'), dict):
        raise ValueError('unsupported native input contract')
    try:
        xml = ET.parse(scene).getroot()
    except ET.ParseError as exc:
        raise ValueError(f'invalid recorded scene XML: {exc}') from exc
    if xml.find('.//include') is not None:
        raise ValueError('diagnostic scene must be the expanded recorded XML')
    compiler = xml.find('compiler')
    if compiler is not None and any(compiler.get(k) for k in ('meshdir', 'texturedir', 'assetdir')):
        raise ValueError('diagnostic scene assets must use expanded file paths')
    assets = {}
    for element in xml.findall('./asset/*'):
        if any(key.startswith('file') and key != 'file' for key in element.attrib):
            raise ValueError('diagnostic cubemap face files need explicit asset binding')
        value = element.get('file')
        if value:
            path = Path(value)
            if not path.is_absolute():
                path = scene.parent / path
            path = local_file(run, path)
            expected = manifest['sha256'].get(str(path))
            if not expected or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(f'recorded asset identity mismatch: {path.name}')
            assets[path] = expected
    segments, tags = [], set()
    for step in data['subgoals']:
        if not isinstance(step, dict):
            raise ValueError('invalid native subgoal record')
        if step.get('frames') is None:
            continue  # Planning-only failures have no physical states to animate.
        tag = step.get('tag')
        if not isinstance(tag, str) or not re.fullmatch(r'subgoal_\d+(?:_\d+)?', tag) or tag in tags:
            raise ValueError('invalid or repeated native segment tag')
        tags.add(tag)
        segments.append((step, local_file(run, report.parent / tag / 'rollout_runtime.npz')))
    if not segments:
        raise ValueError('this trial has no recorded physical segment')
    return dict(report=report, data=data, scene=scene, contract=contract, assets=assets, segments=segments,
                files=[report, scene, contract, *assets, *(p for _, p in segments)])


def read_recording(sources: dict, model) -> tuple:
    """Check exact scene, clock and integration-state binding before joining poses."""
    import mujoco
    import numpy as np
    from tool_library.common.episode import joint_names

    if model.nflex or model.neq:
        raise ValueError('diagnostic replay does not yet support flex or changing equality mechanisms')
    if any(model.body(i).name != 'camrig' for i in range(model.nbody) if model.body_mocapid[i] >= 0):
        raise ValueError('unrecorded task mocap is not supported for diagnostic replay')
    for body in range(model.nbody):
        if model.body_geomnum[body]:
            ancestor = body
            while ancestor:
                if model.body_mocapid[ancestor] >= 0:
                    raise ValueError('mocap geometry needs a recorded pose stream')
                ancestor = int(model.body_parentid[ancestor])
    size = 0
    for _, path in sources['segments']:
        with ZipFile(path) as archive:
            size += sum(item.file_size for item in archive.infolist())
    if size > 128 * 1024 * 1024:
        raise ValueError('diagnostic recordings exceed the 128 MiB preview limit')
    scene_sha = hashlib.sha256(sources['scene'].read_bytes()).hexdigest()
    names = joint_names(model)
    times, positions, previous = [], [], None
    first_time = None
    frame_count = 0
    fields = ('time_s', 'qpos', 'qvel', 'initial_qpos', 'initial_qvel', 'initial_state',
              'final_state', 'state_spec', 'initial_time_s', 'substep_qpos', 'substep_time_s')
    for step, path in sources['segments']:
        with np.load(path, allow_pickle=False) as archive:
            if (str(archive['robot_schema'].item()) != 'dexagent.rigid_robot.v1'
                    or str(archive['scene_sha256'].item()) != scene_sha
                    or archive['joint_names'].tolist() != names):
                raise ValueError('diagnostic recording scene or coordinate identity mismatch')
            record = {key: archive[key] for key in fields}
        if any(not np.isfinite(value).all() for value in record.values()):
            raise ValueError('nonfinite diagnostic recording')
        t, q, v = record['time_s'], record['qpos'], record['qvel']
        sub_t, sub_q = record['substep_time_s'], record['substep_qpos']
        if (t.ndim != 1 or len(t) < 2 or t[0] != 0 or not np.all(np.diff(t) > 0)
                or q.shape != (len(t), model.nq) or v.shape != (len(t), model.nv)
                or sub_t.ndim != 1 or sub_q.shape != (len(sub_t), model.nq)
                or len(sub_t) < 1 or not np.all(np.diff(sub_t) > 0)
                or not np.array_equal(q[0], record['initial_qpos'])
                or not np.array_equal(v[0], record['initial_qvel'])):
            raise ValueError('diagnostic recording state or clock shape mismatch')
        for joint in range(model.njnt):
            if model.jnt_type[joint] == mujoco.mjtJoint.mjJNT_FREE:
                offset = model.jnt_qposadr[joint] + 3
                if not np.allclose(np.linalg.norm(q[:, offset:offset + 4], axis=1), 1, atol=1e-3):
                    raise ValueError('recorded free-body quaternions are not normalized')
        indices = np.rint(t[1:] / model.opt.timestep).astype(int) - 1
        if (np.any(indices < 0) or np.any(indices >= len(sub_t))
                or indices[-1] != len(sub_t) - 1
                or not np.allclose(sub_t, np.arange(1, len(sub_t) + 1) * model.opt.timestep, rtol=0, atol=1e-8)
                or not np.allclose(t[1:], sub_t[indices], rtol=0, atol=1e-8)
                or not np.array_equal(q[1:], sub_q[indices])):
            raise ValueError('diagnostic display poses do not match integration timestamps')
        start = float(record['initial_time_s'].item())
        spec = int(record['state_spec'].item())
        if spec != int(mujoco.mjtState.mjSTATE_INTEGRATION):
            raise ValueError('unsupported recorded integration state')
        for key, expected_q, expected_v, expected_time in (
                ('initial_state', q[0], v[0], start), ('final_state', q[-1], v[-1], start + t[-1])):
            state = record[key]
            if state.shape != (mujoco.mj_stateSize(model, spec),):
                raise ValueError('diagnostic integration state shape mismatch')
            d = mujoco.MjData(model)
            default_pos, default_quat = d.mocap_pos.copy(), d.mocap_quat.copy()
            mujoco.mj_setState(model, d, state, spec)
            if (not np.array_equal(d.qpos, expected_q) or not np.array_equal(d.qvel, expected_v)
                    or abs(d.time - expected_time) > 1e-8
                    or not np.array_equal(d.mocap_pos, default_pos) or not np.array_equal(d.mocap_quat, default_quat)):
                raise ValueError('recorded integration endpoints or mocap differ from display states')
        drop = 1 if previous is not None else 0
        if previous is not None and (
                not np.array_equal(record['initial_state'], previous['final_state'])
                or abs(start - (float(previous['initial_time_s']) + previous['time_s'][-1])) > 1e-8):
            raise ValueError('physical state or time is discontinuous between diagnostic segments')
        length = len(t) - drop
        if step.get('frames') != [frame_count, frame_count + length - 1] or step.get('n_frames') != len(t):
            raise ValueError('native report frame span does not match its recording')
        if first_time is None:
            first_time = start
        times.append(t[drop:] + start - first_time)
        positions.append(q[drop:])
        frame_count += length
        previous = record
    if sources['data'].get('n_frames') != frame_count:
        raise ValueError('native report total frames differ from the recorded segments')
    return np.concatenate(times), np.concatenate(positions)
