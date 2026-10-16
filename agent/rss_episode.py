"""Execute an existing DexAgent episode in RSS with its original substep actions.

This is a backend comparison. It reuses the video reconstruction and action
proposal as named inputs, performs fresh physics, and independently scores the
result against the original Stage 1 goals. It cannot certify fresh perception.
"""
from __future__ import annotations

import json
import subprocess
import sys
import os
from importlib.metadata import version
import time
from dataclasses import asdict
from pathlib import Path

import cv2
import mujoco
import numpy as np

from agent.run_reports import atomic_json, finish_report, make_report, sha256_file
from agent.rss_sharpa import SharpaBridge


def validate_reference(model, data):
    """Reject unsupported state/control contracts before executing physics."""
    required = ('qpos', 'initial_qpos', 'initial_qvel', 'substep_ctrl', 'substep_time_s', 'substep_qpos', 'substep_qvel',
                'time_s', 'joint_names', 'actuator_names', 'labels', 'subgoal', 'fps')
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f'reference is missing {missing}')
    if model.neq or model.na:
        raise ValueError('reference adapter does not yet replay equality events or actuator activation state')
    if not np.array_equal(data['initial_qpos'], model.key_qpos[0]) or np.any(data['initial_qvel']):
        raise ValueError('reference must start at the scene home with zero velocity')
    if data['qpos'].shape != (len(data['time_s']), model.nq):
        raise ValueError('reference trajectory dimensions do not match the scene')
    if data['actuator_names'].tolist() != [model.actuator(i).name for i in range(model.nu)]:
        raise ValueError('reference actuator identity/order differs from the scene')
    from tool_library.common.episode import joint_names
    if data['joint_names'].tolist() != joint_names(model):
        raise ValueError('reference joint identity/order differs from the scene')
    times = data['substep_time_s']
    if data['substep_ctrl'].shape != (len(times), model.nu) or len(times) % 2:
        raise ValueError('reference requires complete pairs of substep controls')
    if data['substep_qpos'].shape != (len(times), model.nq) or data['substep_qvel'].shape != (len(times), model.nv):
        raise ValueError('reference substep states do not match the scene')
    expected = (np.arange(len(times)) + 1) * model.opt.timestep
    if not np.allclose(times, expected, rtol=0, atol=1e-7):
        raise ValueError('reference substep clock does not match the scene timestep')
    if not all(np.isfinite(data[k]).all() for k in ('qpos', 'substep_ctrl', 'substep_qpos', 'substep_qvel', 'time_s', 'initial_qpos', 'initial_qvel')):
        raise ValueError('reference contains non-finite numeric values')
    if data['time_s'][0] != 0 or np.any(np.diff(data['time_s']) <= 0):
        raise ValueError('reference frame clock is not strictly increasing from zero')
    if not np.isfinite(data['fps']) or float(data['fps']) <= 0 or any(
            len(data[key]) != len(data['qpos']) for key in ('labels', 'subgoal')):
        raise ValueError('reference frame labels or frame rate are invalid')
    frames = np.rint(data['time_s'] / model.opt.timestep).astype(int)
    if frames[-1] > len(times) or not np.allclose(frames * model.opt.timestep, data['time_s'], atol=1e-7):
        raise ValueError('reference frames do not map to physics substeps')
    limited = model.actuator_ctrllimited.astype(bool)
    lo, hi = model.actuator_ctrlrange.T
    if np.any(limited & ((data['substep_ctrl'] < lo) | (data['substep_ctrl'] > hi))):
        raise ValueError('reference exceeds source actuator limits')


def source_qpos(bridge, state):
    """Invert the bridge's named joint mapping, including free-root quaternion order."""
    q = bridge.initial_qpos.copy()
    imported = state.joint_q.numpy()
    for j, name in enumerate(bridge.state_joint_names):
        a = int(bridge.source.jnt_qposadr[j])
        b = bridge.state_q_starts[name]
        if bridge.source.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE:
            q[a:a + 7] = imported[b + np.array([0, 1, 2, 6, 3, 4, 5])]
        else:
            q[a] = imported[b]
    return q


def feedback_control(bridge, data, index, state, *, omega=12.):
    """Original commands plus a bounded computed-torque arm correction.

    The original trajectory's measured state and velocity define the nominal
    response. Correct only the arm motors; the individual finger position
    commands and free object dynamics are untouched. No runtime state writes.
    """
    q, v = bridge.joint_state(state)
    desired_q = data['initial_qpos'] if index == 0 else data['substep_qpos'][index - 1]
    desired_v = data['initial_qvel'] if index == 0 else data['substep_qvel'][index - 1]
    m, d = bridge.source, bridge.reference
    arm = np.asarray(['_arm_' in n for n in bridge.joint_names])
    qa, va = bridge.source_q_indices[arm], bridge.source_v_indices[arm]
    d.qpos[bridge.source_q_indices] = q
    mujoco.mj_forward(m, d)
    mass = np.zeros((m.nv, m.nv))
    mujoco.mj_fullM(m, d, mass)
    acceleration = np.zeros(m.nv)
    acceleration[va] = omega**2 * (desired_q[qa] - q[arm]) + 2 * omega * (desired_v[va] - v[arm])
    correction = mass @ acceleration
    controls = data['substep_ctrl'][index].copy()
    for a, name in enumerate(bridge.actuator_names):
        if name.endswith('_motor'):
            j = int(m.actuator_trnid[a, 0])
            controls[a] = np.clip(controls[a] + correction[m.jnt_dofadr[j]], *m.actuator_ctrlrange[a])
    return bridge.validate_controls(controls)


def compare_initial(bridge, reference_run, out, check, limit):
    """Compare source MuJoCo and RSS under the same source camera and state."""
    from robosimstudio.core.cameras import CameraSpec
    from tool_library.common import rigcam as RC

    cam = RC.scaled(json.loads((reference_run / 'stage2/camera.json').read_text()), 640, 512)
    rotation, eye = np.asarray(cam['R_cv']), np.asarray(cam['pos'])
    camera = CameraSpec(name='video_cam', width=640, height=512, eye=tuple(eye),
        look_at=tuple(eye + rotation[:, 2]), up=tuple(-rotation[:, 1]), fov_y=cam['fovy_deg'], channels=('rgb', 'seg'))
    recon = json.loads((reference_run / 'stage2/scene_recon.json').read_text())
    names = [obj['root_body'] for obj in recon['objects']]
    m, d = bridge.source, mujoco.MjData(bridge.source)
    d.qpos[:] = bridge.initial_qpos
    mujoco.mj_forward(m, d)
    body_labels = {name: i + 1 for i, name in enumerate(names)}
    def body_label(body):
        while body > 0:
            label = body_labels.get(m.body(body).name)
            if label:
                return label
            body = int(m.body_parentid[body])
        return 0
    labels = {i: body_label(m.body(str(bridge.sim.model.body_label[int(b)]).rsplit('/', 1)[-1]).id)
              if b >= 0 else 0 for i, b in enumerate(bridge.sim.model.shape_body.numpy())}
    renderer = bridge.sim.make_renderer('tiled', cameras=[camera], shape_labels=labels)
    try:
        image = renderer.render(bridge.sim.state)[camera.name]
        rss_rgb, rss_seg = image['rgb'].copy(), image['seg'].copy()
    finally:
        renderer.close()
    renderer = mujoco.Renderer(m, height=512, width=640)
    try:
        scene_option = mujoco.MjvOption()
        scene_option.geomgroup[:] = [1, 1, 0, 0, 0, 0]
        renderer.update_scene(d, camera='video_cam', scene_option=scene_option)
        mj_rgb = renderer.render().copy()
        renderer.enable_segmentation_rendering()
        mj_seg = renderer.render().copy()
    finally:
        renderer.close()
    geom_labels = np.asarray([body_label(int(b)) for b in m.geom_bodyid])
    valid = (mj_seg[:, :, 1] == int(mujoco.mjtObj.mjOBJ_GEOM)) & (mj_seg[:, :, 0] >= 0)
    mj_labels = np.zeros(valid.shape, int)
    mj_labels[valid] = geom_labels[mj_seg[:, :, 0][valid]]
    checks, scores = [], {}
    for i, name in enumerate(names, 1):
        a, b = mj_labels == i, rss_seg == i
        union = int(np.count_nonzero(a | b))
        iou = float(np.count_nonzero(a & b) / union) if union else 0.
        scores[name] = {'iou': iou, 'mujoco_pixels': int(a.sum()), 'rss_pixels': int(b.sum())}
        checks.append(check('initial_silhouette:' + name, iou >= limit and a.sum() > 10 and b.sum() > 10,
                            scores[name], {'min_iou': limit}))
    human = cv2.cvtColor(cv2.imread(str(reference_run / 'stage2/frame0.jpg')), cv2.COLOR_BGR2RGB)
    human = cv2.resize(human, (640, 512))
    panels = [human, mj_rgb, rss_rgb, cv2.addWeighted(human, .5, rss_rgb, .5, 0)]
    for panel, text in zip(panels, ['Human reference', 'DexAgent / MuJoCo', 'RSS / same scene', 'Human + RSS'], strict=True):
        cv2.rectangle(panel, (0, 0), (250, 28), (0, 0, 0), -1)
        cv2.putText(panel, text, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 1)
    cv2.imwrite(str(out / 'frame0_compare_rss.png'), cv2.cvtColor(np.hstack(panels), cv2.COLOR_RGB2BGR))
    atomic_json(out / 'frame0_comparison.json', {'scores': scores, 'camera': asdict(camera),
        'comparison': 'same initial state and source camera; renderer agreement is not real reconstruction accuracy'})
    return checks, camera


def comparison_video(bridge, data, video_path, out, camera, fps):
    """Render both simulated states at identical times; human column is resampled."""
    import imageio.v2 as imageio
    from robosimstudio.core.render.offline import StateReplay

    episode = out / 'episodes/0000'
    with np.load(episode / 'steps.npz', allow_pickle=False) as z:
        times = z['sim_time']
    with np.load(episode / 'privileged.npz', allow_pickle=False) as z:
        body_q = z['body_q']
    targets = times[0] + np.arange(int(np.floor((times[-1] - times[0]) * fps)) + 1) / fps
    indices = np.searchsorted(times, targets, side='right') - 1
    renderer = bridge.sim.make_renderer('tiled', cameras=[camera])
    replay = StateReplay(bridge.sim.model, strict=True)
    mj_renderer = mujoco.Renderer(bridge.source, height=camera.height, width=camera.width)
    d = mujoco.MjData(bridge.source)
    cap = cv2.VideoCapture(str(video_path))
    human_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    human_indices, physics_indices = [], []
    try:
        with imageio.get_writer(out / 'comparison.mp4', fps=fps, codec='libx264', macro_block_size=1) as writer:
            for i, frame in enumerate(indices):
                physical = int(round(times[frame] / bridge.source.opt.timestep)) - 1
                if not 0 <= physical < len(data['substep_qpos']):
                    raise ValueError('comparison physics index is outside the reference')
                d.qpos[:] = data['substep_qpos'][physical]
                mujoco.mj_forward(bridge.source, d)
                mj_renderer.update_scene(d, camera='video_cam')
                baseline = mj_renderer.render().copy()
                actual = renderer.render(replay.write({'body_q': body_q[frame]}))[camera.name]['rgb'].copy()
                source = round(i / max(len(indices) - 1, 1) * (human_count - 1))
                cap.set(cv2.CAP_PROP_POS_FRAMES, source)
                ok, human = cap.read()
                if not ok:
                    raise ValueError(f'cannot decode human comparison frame {source}')
                human = cv2.resize(cv2.cvtColor(human, cv2.COLOR_BGR2RGB), (camera.width, camera.height))
                for panel, label in zip([human, baseline, actual],
                        ['Human (resampled visual reference)', 'DexAgent / original execution', 'RSS / fresh physics'], strict=True):
                    cv2.rectangle(panel, (0, 0), (camera.width, 28), (0, 0, 0), -1)
                    cv2.putText(panel, label, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1)
                writer.append_data(np.hstack([human, baseline, actual]))
                human_indices.append(source)
                physics_indices.append(physical)
    finally:
        cap.release()
        renderer.close()
        mj_renderer.close()
    atomic_json(out / 'comparison_frame_map.json', {'fps': fps, 'rss_control_indices': indices.tolist(),
        'reference_substep_indices': physics_indices, 'physics_times_s': times[indices].tolist(),
        'human_video_frame_indices': human_indices, 'human_synchronization': 'resampled visual comparison only',
        'simulator_synchronization': 'same physical timestamps, no dynamics rerun during rendering'})
    return len(indices)


def run_reference(args, out, report):
    from robosimstudio.record import EpisodeRecorder
    from robosimstudio.specs import CaptureSpec, RecordSpec
    from run_robosimstudio import check, print_report, render_tape, validate_tape
    from tool_library.verifiers.stage1_goal_gate import stage1_goal_gate
    from tool_library.verifiers.episode_physics_check import episode_physics_check

    reference = args.reference_run.resolve()
    task = reference / 'stage3/stage4_task'
    paths = {'scene': task / 'scene.xml', 'trajectory': task / 'trajectory.npz',
             'semantic': reference / 'stage1/scene_semantics.json',
             'reconstruction': reference / 'stage2/scene_recon.json',
             'manifest': reference / 'stage3/trajectory_manifest.json',
             'camera': reference / 'stage2/camera.json',
             'video': task / 'human_video.mp4'}
    for name, path in paths.items():
        report['inputs'][name] = {'path': str(path), 'sha256': sha256_file(path)}
    report['metrics'].update(scope='reference_episode_backend_comparison',
        reference_run=str(reference), physics_device=args.physics_device,
        upstream_reused=['stage1_semantics', 'stage2_reconstruction', 'stage3_action_proposal'],
        fresh=['scene_import_audit', 'substep_physics_execution', 'task_goal_gates', 'rendering'],
        action_contract='original Stage 3 feedforward commands plus bounded arm feedback' if args.reference_control == 'feedback'
                        else 'original Stage 3 raw actuator commands at every source physics substep',
        reference_control=args.reference_control,
        full_pipeline_complete=False, python=sys.executable, hf_home=os.environ.get('HF_HOME'),
        versions={name: version(name) for name in ('numpy', 'mujoco', 'newton', 'warp-lang')})
    rss_path = Path(__file__).resolve().parents[1] / 'robosimstudio'
    report['model_identity'] = {'robosimstudio': {'path': str(rss_path), 'commit': subprocess.check_output(
        ['git', '-C', str(rss_path), 'rev-parse', 'HEAD'], text=True).strip()}}
    for name in ('rss_episode.py', 'rss_sharpa.py', 'rss_scene.py'):
        source = Path(__file__).parent / name
        (out / name).write_bytes(source.read_bytes())
        report['inputs'][name] = {'sha256': sha256_file(source)}
    with np.load(paths['trajectory'], allow_pickle=False) as archive:
        data = {key: archive[key] for key in archive.files}
    from tool_library.common.rigid_robot import compile_rigid
    rig = compile_rigid(paths['scene'])
    validate_reference(rig.model, data)
    source_manifest = json.loads(paths['manifest'].read_text())
    boundaries, phase_reports = {}, {}
    for subgoal in source_manifest['subgoals']:
        goal_id = str(subgoal['index'])
        end_frame = int(subgoal['frames'][-1])
        end_step = int(round(data['time_s'][end_frame] / rig.model.opt.timestep))
        boundaries[end_step] = (goal_id, end_frame, subgoal['stage1_indices'])
        phase = make_report(run=out.name, profile='backend_eval', stage='reference', step='subgoal_' + goal_id,
                            status='blocked', next_action='await preceding subgoal acceptance')
        phase_reports[goal_id] = phase
        atomic_json(out / f'phases/subgoal_{goal_id}.json', phase)
    report['metrics']['phase_reports'] = [str(out / f'phases/subgoal_{i}.json') for i in phase_reports]
    bridge = SharpaBridge(out, source=rig.model, physics_device=args.physics_device)
    sim = bridge.sim
    recorder = EpisodeRecorder(out / 'episodes', RecordSpec(record_video=False),
                               capture=CaptureSpec(enabled=True, contacts=False))
    recording = False
    checks = report['checks']
    try:
        validate_reference(bridge.source, data)
        checks.append(check('source_action_contract', True, {'substeps': len(data['substep_ctrl']),
            'frame_count': len(data['qpos']), 'source_fps': float(data['fps'])}))
        checks.extend(bridge.audit(check))
        if any(c['status'] != 'passed' for c in checks if c.get('required', True)):
            raise RuntimeError('reference import audit failed')
        bridge.start()
        initial_checks, camera = compare_initial(bridge, reference, out, check, args.reference_limits['min_silhouette_iou'])
        checks.extend(initial_checks)
        manifest = bridge.manifest()
        manifest['recorded_action'] = 'first raw actuator command in each 4 ms block; full 2 ms controls in trajectory.npz'
        manifest['acceptance_controller'] = report['metrics']['action_contract']
        manifest['feedback_omega_rad_s'] = args.reference_limits['feedback_omega_rad_s'] if args.reference_control == 'feedback' else None
        manifest['policy_dataset_ready'] = False
        atomic_json(out / 'sharpa_manifest.json', manifest)
        atomic_json(out / 'stage_report.json', report)
        if any(c['status'] != 'passed' for c in checks if c.get('required', True)):
            raise RuntimeError('reference initial scene comparison failed')
        # This host hook supplies each 2 ms command separately. Never average two
        # torques or replace the source Stage 3 controller with the acceptance PD.
        sim.pipeline.use_cuda_graph = False
        index = 0
        states = [bridge.initial_qpos.copy()]
        executed_controls = []
        ordered_goals = list(phase_reports)
        phase_started = time.monotonic()
        phase_reports[ordered_goals[0]]['status'] = 'running'
        atomic_json(out / f'phases/subgoal_{ordered_goals[0]}.json', phase_reports[ordered_goals[0]])
        def command(state, dt):
            nonlocal index
            if index >= len(data['substep_ctrl']):
                raise RuntimeError('reference controls exhausted')
            controls = feedback_control(bridge, data, index, state, omega=args.reference_limits['feedback_omega_rad_s']) \
                if args.reference_control == 'feedback' else data['substep_ctrl'][index]
            bridge.apply(controls, sim.control, state)
            executed_controls.append(controls.copy())
            index += 1
        def capture(state, dt):
            nonlocal phase_started
            states.append(source_qpos(bridge, state))
            if index in boundaries:
                goal_id, end_frame, stage1_indices = boundaries[index]
                partial = out / f'phases/subgoal_{goal_id}_trajectory.npz'
                mapping = np.rint(data['time_s'][:end_frame + 1] / bridge.source.opt.timestep).astype(int)
                np.savez_compressed(partial, qpos=np.asarray(states)[mapping], fps=data['fps'],
                                    labels=data['labels'][:end_frame + 1], subgoal=data['subgoal'][:end_frame + 1])
                evidence = out / f'phases/subgoal_{goal_id}_goals.json'
                verdict = stage1_goal_gate(str(paths['semantic']), str(paths['manifest']), bridge.source, str(partial),
                    scene_recon_json=str(paths['reconstruction']), subgoals=stage1_indices, out_json=str(evidence))
                # A subset report intentionally cannot certify the full task.
                # Its independently measured rows certify only this boundary.
                rows = verdict.get('subgoals', [])
                valid = len(rows) == len(stage1_indices) and all(row['ok'] for row in rows)
                phase = phase_reports[goal_id]
                finish_report(phase, checks=[check('independent_subgoal', valid,
                    [row.get('goal') for row in rows], evidence=str(evidence))],
                    elapsed_s=time.monotonic() - phase_started, artifacts=[str(partial), str(evidence)])
                atomic_json(out / f'phases/subgoal_{goal_id}.json', phase)
                checks.append(check('subgoal:' + goal_id, valid, [row.get('goal') for row in rows],
                                    evidence=str(evidence)))
                atomic_json(out / 'stage_report.json', report)
                print_report(phase, out / f'phases/subgoal_{goal_id}.json')
                if not valid:
                    np.savez_compressed(out / 'failed_substep_trajectory.npz', qpos=states,
                                        ctrl=executed_controls, dt=bridge.source.opt.timestep)
                    raise RuntimeError(f'subgoal {goal_id} failed; downstream motion blocked; see {evidence}')
                next_goal = ordered_goals.index(goal_id) + 1
                if next_goal < len(ordered_goals):
                    phase = phase_reports[ordered_goals[next_goal]]
                    phase['status'], phase['next_action'] = 'running', None
                    atomic_json(out / f'phases/subgoal_{ordered_goals[next_goal]}.json', phase)
                phase_started = time.monotonic()
        sim.pipeline.hooks.pre_solve.append(command)
        sim.pipeline.hooks.post_solve.append(capture)
        sim.pipeline.invalidate_graph(recapture=False)
        recorder.begin(sim.episode, sim)
        recording = True
        last_progress = time.monotonic()
        for k in range(len(data['substep_ctrl']) // 2):
            sim.step(data['substep_ctrl'][2 * k])
            if not sim.state_is_finite():
                raise RuntimeError(f'non-finite RSS state at substep {index}')
            q, v = bridge.joint_state()
            recorder.step(sim, executed_controls[2 * k], {'proprio': {'joint_q': q, 'joint_qd': v}}, 0., {})
            if time.monotonic() - last_progress > 15:
                print(f'[reference] {index}/{len(data["substep_ctrl"])} physics substeps', flush=True)
                last_progress = time.monotonic()
        times = np.r_[0., data['substep_time_s']]
        states = np.asarray(states)
        frame_indices = np.rint(data['time_s'] / bridge.source.opt.timestep).astype(int)
        if frame_indices.max() >= len(states) or not np.allclose(times[frame_indices], data['time_s'], atol=1e-7):
            raise ValueError('reference frame-to-physics mapping is invalid')
        observed = states[frame_indices]
        output_trajectory = out / 'trajectory.npz'
        np.savez_compressed(output_trajectory, qpos=observed, fps=data['fps'], labels=data['labels'],
            subgoal=data['subgoal'], time_s=data['time_s'], joint_names=data['joint_names'],
            actuator_names=data['actuator_names'], substep_qpos=states[1:], substep_time_s=data['substep_time_s'],
            substep_ctrl=executed_controls, nominal_substep_ctrl=data['substep_ctrl'],
            initial_qpos=states[0], initial_qvel=data['initial_qvel'])
        errors = {}
        robot_error = float(np.max(abs(observed[:, bridge.source_q_indices] - data['qpos'][:, bridge.source_q_indices])))
        joint_errors = abs(observed[:, bridge.source_q_indices] - data['qpos'][:, bridge.source_q_indices])
        arm = np.asarray(['_arm_' in name for name in bridge.joint_names])
        for group, columns in (('arm', arm), ('finger', ~arm)):
            value = float(joint_errors[:, columns].max())
            checks.append(check(group + '_reference_error', value <= args.reference_limits[f'max_{group}_error_rad'],
                                value, args.reference_limits[f'max_{group}_error_rad']))
        for j in range(bridge.source.njnt):
            if bridge.source.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE:
                a = int(bridge.source.jnt_qposadr[j])
                name = bridge.source.body(int(bridge.source.jnt_bodyid[j])).name
                values = np.linalg.norm(observed[:, a:a + 3] - data['qpos'][:, a:a + 3], axis=1)
                errors[name] = {'max_position_error_m': float(values.max()), 'final_position_error_m': float(values[-1])}
                checks.append(check('object_reference_error:' + name, values.max() <= args.reference_limits['max_object_error_m'],
                                    errors[name], args.reference_limits['max_object_error_m']))
        goals = stage1_goal_gate(str(paths['semantic']), str(paths['manifest']), bridge.source, str(output_trajectory),
                                scene_recon_json=str(paths['reconstruction']), out_json=str(out / 'task_goal_report.json'))
        checks.append(check('independent_stage1_goals', goals['ok'], goals.get('counts'), evidence=str(out / 'task_goal_report.json')))
        physics = episode_physics_check(str(out / 'runtime_scene.xml'), str(output_trajectory))
        atomic_json(out / 'physics_report.json', physics)
        checks.append(check('episode_physics', physics['ok'], physics.get('failed'), evidence=str(out / 'physics_report.json')))
        success = all(c['status'] == 'passed' for c in checks if c.get('required', True))
        row = recorder.end(sim, {'success': success, 'close_kind': 'success' if success else 'failed'})
        recording = False
        recorder.store.append_result(row)
        recorder.close()
        tape, _, _ = validate_tape(out / 'episodes/0000', 0)
        checks.extend(tape)
        if args.render:
            checks.append(render_tape(sim, out / 'episodes/0000', out, args.fps, bridge.camera('workspace')))
            count = comparison_video(bridge, data, paths['video'], out, camera, args.fps)
            capture_video = cv2.VideoCapture(str(out / 'comparison.mp4'))
            decoded = 0
            try:
                while True:
                    ok, frame = capture_video.read()
                    if not ok:
                        break
                    if frame.shape[:2] != (camera.height, camera.width * 3):
                        raise ValueError('comparison video has invalid frame dimensions')
                    decoded += 1
            finally:
                capture_video.release()
            checks.append(check('comparison_video_decoded', decoded == count, {'decoded': decoded, 'expected': count}))
        checks.append({'id': 'upstream_reconstruction_reused', 'required': False, 'status': 'warning',
                       'evidence': 'standard reference inputs reused for backend comparison; no fresh human-video reconstruction is claimed'})
        report['metrics'].update(robot_max_error_rad=robot_error, object_errors=errors, executed_substeps=index,
            simulated_duration_s=float(times[-1]), subgoal_counts=goals.get('counts'),
            phase_reports=[str(out / f'phases/subgoal_{i}.json') for i in ordered_goals])
        atomic_json(out / 'reference_comparison.json', report['metrics'])
        atomic_json(out / 'artifact_hashes.json', {str(p.relative_to(out)): sha256_file(p)
            for p in out.rglob('*') if p.is_file() and p.name not in {'stage_report.json', 'artifact_hashes.json'}})
        finish_report(report, checks=checks, artifacts=[str(p) for p in out.rglob('*') if p.is_file()])
    except BaseException:
        if recording:
            row = recorder.end(sim, {'success': False, 'close_kind': 'failed'})
            recorder.store.append_result(row)
        raise
    finally:
        recorder.close()
        sim.close()
