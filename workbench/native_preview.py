"""Build a native, standalone Viser replay in an isolated process.

Recorded states are rendered, never simulated. Keep the Newton/RSS imports out
of the management service, and keep every derived file out of source runs.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import traceback


def recorded_rig_translation(manifest: dict) -> tuple[float, float, float]:
    """Rebuild the recorded placement, never infer it from the current scene."""
    if manifest.get('schema') != 'dexagent.rss_sharpa.v1':
        raise ValueError('unsupported recorded scene manifest')
    pose = manifest.get('rig_to_world_xyzw')
    if (not isinstance(pose, list) or len(pose) != 7
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in pose)
            or any(abs(v) > 1e-12 for v in pose[3:6]) or abs(abs(pose[6]) - 1) > 1e-12):
        raise ValueError('recorded rig placement must be a finite translation with identity rotation')
    ground = manifest.get('additional_ground_z_m')
    if type(ground) not in (int, float) or not math.isfinite(ground) or not math.isclose(
            ground, pose[2] - 1.0, rel_tol=0, abs_tol=1e-9):
        raise ValueError('recorded extra ground does not match rig placement')
    return tuple(float(v) for v in pose[:3])


def read_dexagent_recording(path: Path, *, scene_sha256: str, joint_names: list[str],
                           asset_hashes: dict[str, str], timestep: float) -> tuple:
    """Check display identities and recorded-state timing, without scoring task success."""
    import numpy as np
    report = json.loads((path.parent / 'outputs/run_report.json').read_text())
    if report.get('schema') != 'dexagent.stage4_run_report.v1':
        raise ValueError('unsupported source identity report')
    input_hashes = (report.get('inputs') or {}).get('files', {})
    if any(input_hashes.get(name) != digest for name, digest in asset_hashes.items()):
        raise ValueError('source mesh/texture identities do not match the recorded input snapshot')
    with np.load(path, allow_pickle=False) as data:
        if str(data['scene_sha256'].item()) != scene_sha256 or str(data['robot_schema'].item()) != 'dexagent.rigid_robot.v1':
            raise ValueError('recorded trajectory does not match this rigid scene')
        if data['joint_names'].tolist() != joint_names:
            raise ValueError('recorded coordinate identities do not match this scene')
        times, qpos = data['time_s'], data['qpos']
        substeps = data['substep_qpos']
        if (times.ndim != 1 or len(times) < 2 or times[0] != 0 or not np.all(np.diff(times) > 0)
                or qpos.shape != (len(times), len(joint_names)) or not np.isfinite(qpos).all()
                or not np.isfinite(times).all() or substeps.ndim != 2 or substeps.shape[1] != len(joint_names)):
            raise ValueError('recorded coordinate and clock shapes do not match')
        indices = np.rint(times / timestep).astype(int)
        if (not np.allclose(indices * timestep, times, atol=1e-7) or indices[-1] > len(substeps)
                or np.any(indices[1:] < 1) or not np.array_equal(qpos[0], data['initial_qpos'])
                or not np.allclose(qpos[1:], substeps[indices[1:] - 1], rtol=0, atol=1e-8)):
            raise ValueError('display poses are not bound to the recorded physics-state timestamps')
        return times, qpos


def build(run: Path, episode: Path, output: Path, *, mode: str = 'rss', max_frames: int = 301) -> dict:
    import numpy as np
    from agent.rss_sharpa import SharpaBridge
    from robosimstudio.core.render.offline import StateReplay
    from robosimstudio_studio.garments import ViewerStudio
    from robosimstudio_studio.lighting import LightingRig
    from zipfile import ZipFile
    from agent.run_reports import sha256_file

    diagnostic = None
    if mode == 'diagnostic':
        from .planning_replay import recording_sources
        diagnostic = recording_sources(run, episode / 'execution_report.json')
    body_replay = mode in {'rss', 'rss_assets'}
    source = (diagnostic['scene'] if diagnostic else
              run / 'original_source_scene.xml' if mode == 'rss' else episode / 'scene.xml')
    recordings = ([p for _, p in diagnostic['segments']] if diagnostic else
                  (episode / 'steps.npz', episode / 'privileged.npz') if body_replay else (episode / 'trajectory.npz',))
    for path in recordings:
        with ZipFile(path) as archive:
            if sum(info.file_size for info in archive.infolist()) > 128 * 1024 * 1024:
                raise ValueError('recorded arrays exceed the 128 MiB interactive preview limit')
    bridge = None
    manifest = None
    translation = (0.0, 0.0, 0.0)
    if mode == 'rss':
        manifest = json.loads((run / 'sharpa_manifest.json').read_text())
        translation = recorded_rig_translation(manifest)
    if mode == 'rss_assets':
        from agent.rss_simfoundry import rebuild_snapshot
        sim, camera, body_labels = rebuild_snapshot(run, episode)
    else:
        bridge = SharpaBridge(output / 'model', source=source,
                              device='cpu', physics_device='cpu', rig_translation=translation)
        sim, camera = bridge.sim, bridge.camera('ego')
    viewer = None
    try:
        initial_steps = int(sim.step_count)
        if initial_steps != 0:
            raise ValueError('visualization scene unexpectedly executed physics during construction')
        actual = bridge.manifest() if bridge else None
        if mode == 'rss':
            for key in ('source_scene_sha256', 'asset_sha256', 'body_labels'):
                if not manifest.get(key) or actual[key] != manifest[key]:
                    raise ValueError(f'recorded model identity does not match rebuilt scene: {key}')
            body_labels = manifest['body_labels']
        if body_replay:
            with np.load(episode / 'steps.npz', allow_pickle=False) as archive:
                times = archive['sim_time']
            with np.load(episode / 'privileged.npz', allow_pickle=False) as archive:
                poses = archive['body_q']
            if (times.ndim != 1 or len(times) < 2 or not np.all(np.diff(times) > 0)
                    or poses.shape != (len(times), len(body_labels), 7)
                    or not np.isfinite(times).all() or not np.isfinite(poses).all()):
                raise ValueError('recorded body poses do not match the model and simulation clock')
            if not np.allclose(np.linalg.norm(poses[:, :, 3:], axis=2), 1.0, atol=1e-3):
                raise ValueError('recorded xyzw body orientations are not normalized')
        elif diagnostic:
            import mujoco
            from agent.artifacts import model_digest
            from .planning_replay import read_recording
            source_model = mujoco.MjModel.from_xml_path(str(source))
            if model_digest(source_model) != model_digest(bridge.source):
                raise ValueError('rebuilt diagnostic scene differs from the recorded physical model')
            if actual['asset_sha256'] != {str(p): sha for p, sha in diagnostic['assets'].items()}:
                raise ValueError('rebuilt diagnostic assets differ from the native input contract')
            times, poses = read_recording(diagnostic, source_model)
        else:
            from tool_library.common.episode import joint_names
            times, poses = read_dexagent_recording(episode / 'trajectory.npz', scene_sha256=sha256_file(source),
                joint_names=joint_names(bridge.source), asset_hashes=actual['asset_sha256'], timestep=bridge.source.opt.timestep)
        viewer = ViewerStudio(port=0, share=False, verbose=False)
        serializer = viewer._server.get_scene_serializer()
        viewer._server.initial_camera.position = camera.eye
        viewer._server.initial_camera.look_at = camera.look_at
        viewer._server.initial_camera.up = camera.up
        viewer._server.initial_camera.fov = float(np.radians(camera.fov_y))
        viewer.set_model(sim.model)
        # Reuse RSS lighting without downloading an HDRI or substituting an unrelated room.
        # The source MJCF geometry remains the authority for the recorded scene.
        LightingRig(environment='').apply(viewer._server)
        replay = StateReplay(sim.model, strict=True)
        indices = np.unique(np.linspace(0, len(times) - 1, min(max_frames, len(times)), dtype=int))
        previous_time = float(times[0])
        for index in indices:
            recorded_time = float(times[index])
            serializer.insert_sleep(recorded_time - previous_time)
            state = replay.write({'body_q': poses[index]} if body_replay else
                                 {'joint_q': bridge.source_q_to_import(poses[index])})
            viewer.begin_frame(recorded_time)
            viewer.log_state(state)
            viewer.end_frame()
            previous_time = recorded_time
        html = serializer.as_html(dark_mode=True)
        executed_steps = int(sim.step_count) - initial_steps
        if executed_steps != 0:
            raise ValueError('recorded-state preview must not execute physics')
        temporary = output / 'viewer.tmp.html'
        temporary.write_text(html)
        temporary.replace(output / 'viewer.html')
        return {'status': 'ready', 'source_frames': len(times), 'preview_frames': len(indices),
                'duration_s': float(times[-1] - times[0]), 'mode': 'native_recorded_state_replay',
                'state_source': 'body_q' if body_replay else 'recorded_qpos_with_forward_kinematics',
                'model_identity_verified': True, 'physics_steps_executed': executed_steps,
                'message': ('规划试验实际记录回放；不代表任务成功。未运行物理。' if diagnostic else
                            '原生记录回放；未运行物理。可旋转、缩放并使用内置播放时间轴。')}
    finally:
        if viewer:
            viewer._server.stop()
        sim.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--episode', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--mode', choices=['rss', 'rss_assets', 'dexagent', 'diagnostic'], default='rss')
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        if args.mode == 'diagnostic':
            from agent.run_reports import sha256_file
            expected = json.loads((args.output / 'job.json').read_text()).get('source_hashes')
            if not expected or any(sha256_file(name) != sha for name, sha in expected.items()):
                raise ValueError('diagnostic preview inputs changed before generation')
        result = build(args.run.resolve(), args.episode.resolve(), args.output.resolve(), mode=args.mode)
        if args.mode == 'diagnostic' and any(sha256_file(name) != sha for name, sha in expected.items()):
            (args.output / 'viewer.html').unlink(missing_ok=True)
            raise ValueError('diagnostic preview inputs changed during generation')
    except Exception as exc:
        traceback.print_exc()
        result = {'status': 'failed', 'message': f'{type(exc).__name__}: {exc}'}
    temporary = args.output / 'result.tmp.json'
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(args.output / 'result.json')
    return 0 if result['status'] == 'ready' else 1


if __name__ == '__main__':
    sys.exit(main())
