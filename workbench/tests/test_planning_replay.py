"""Recorded failures are inspectable without simulation or successful packaging."""
import hashlib
import json
import subprocess
import sys

import mujoco
import numpy as np
import pytest

from tool_library.common.episode import joint_names
from workbench.planning_replay import read_recording, recording_sources


@pytest.fixture
def recording(tmp_path, monkeypatch):
    run = tmp_path / 'run'
    attempt = run / 'stage3/.candidates/attempt'
    out = attempt / 'failed_execution'
    out.mkdir(parents=True)
    scene = out / 'scene_physics.xml'
    scene.write_text('<mujoco><option timestep=".1"/><worldbody><body><joint name="axis"/>'
                     '<geom type="sphere" size=".1"/></body></worldbody><keyframe><key/></keyframe></mujoco>')
    model = mujoco.MjModel.from_xml_path(str(scene))
    sha = hashlib.sha256(scene.read_bytes()).hexdigest()
    (attempt / 'input_contract.json').write_text(json.dumps({
        'schema': 'dexagent.input_contract.v1', 'sha256': {}}))
    report = dict(ok=False, dry_run=False, n_subgoals=2, n_frames=5, subgoals=[])
    for i in range(2):
        tag = f'subgoal_{i + 1:02d}'
        directory = out / tag
        directory.mkdir()
        start = i * .4
        steps = (start + np.arange(1, 5) * .1).reshape(-1, 1)
        q = np.concatenate([[[start]], steps[[1, 3]]])
        v = np.ones((3, 1))
        states = []
        for t, pos in ((start, q[0]), (start + .4, q[-1])):
            d = mujoco.MjData(model)
            d.qpos[:], d.qvel[:], d.time = pos, 1, t
            state = np.zeros(mujoco.mj_stateSize(model, mujoco.mjtState.mjSTATE_INTEGRATION))
            mujoco.mj_getState(model, d, state, mujoco.mjtState.mjSTATE_INTEGRATION)
            states.append(state)
        np.savez(directory / 'rollout_runtime.npz',
                 robot_schema='dexagent.rigid_robot.v1', scene_sha256=sha, joint_names=joint_names(model),
                 time_s=[0, .2, .4], qpos=q, qvel=v, initial_qpos=q[0], initial_qvel=v[0],
                 substep_time_s=np.arange(1, 5) * .1, substep_qpos=steps,
                 state_spec=int(mujoco.mjtState.mjSTATE_INTEGRATION), initial_time_s=start,
                 initial_state=states[0], final_state=states[1])
        report['subgoals'].append(dict(index=i + 1, tag=tag, motion='carry', ok=i == 0,
            failed=[] if i == 0 else ['slip'], n_frames=3, frames=[0, 2] if i == 0 else [3, 4]))
    path = out / 'execution_report.json'
    path.write_text(json.dumps(report))
    def no_simulation(*args, **kwargs):
        raise AssertionError('diagnostic replay must not simulate')
    monkeypatch.setattr(mujoco, 'mj_step', no_simulation)
    return run, path, model


def test_failed_trial_clock_and_pose_join_is_readonly(recording):
    run, path, model = recording
    original = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob('*') if p.is_file()}
    sources = recording_sources(run, path)
    times, poses = read_recording(sources, model)
    assert times == pytest.approx([0, .2, .4, .6, .8])
    assert poses[:, 0] == pytest.approx(times)
    assert original == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in original}
    assert not list(run.rglob('trajectory.npz'))
    assert not (run / 'episodes').exists()


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('scene_sha256', 'wrong-scene', 'identity'),
    ('joint_names', ['different'], 'identity'),
    ('time_s', [0, .1, .4], 'timestamps'),
    ('qpos', [[0], [999], [.4]], 'timestamps'),
    ('qpos', [[0], [float('nan')], [.4]], 'nonfinite'),
    ('substep_time_s', [.1, .2, .2, .4], 'clock shape'),
    ('state_spec', 0, 'integration state'),
])
def test_bad_physical_evidence_rejected(recording, field, value, message):
    run, path, model = recording
    runtime = path.parent / 'subgoal_01/rollout_runtime.npz'
    with np.load(runtime) as archive:
        data = dict(archive)
    data[field] = value
    np.savez(runtime, **data)
    with pytest.raises(ValueError, match=message):
        read_recording(recording_sources(run, path), model)


def test_segment_discontinuity_and_report_span_rejected(recording):
    run, path, model = recording
    runtime = path.parent / 'subgoal_02/rollout_runtime.npz'
    original = runtime.read_bytes()
    with np.load(runtime) as archive:
        data = dict(archive)
    # A changed integration-state component can be invisible in qpos.
    data['initial_state'][-1] += 1
    np.savez(runtime, **data)
    with pytest.raises(ValueError, match='discontinuous'):
        read_recording(recording_sources(run, path), model)
    runtime.write_bytes(original)
    report = json.loads(path.read_text())
    report['subgoals'][1]['frames'] = [2, 4]
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='span'):
        read_recording(recording_sources(run, path), model)


def test_missing_records_dry_runs_and_escaping_tags_rejected(recording):
    run, path, _ = recording
    original = path.read_bytes()
    report = json.loads(original)
    report['dry_run'] = True
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='physical'):
        recording_sources(run, path)
    report['dry_run'] = False
    report['subgoals'][0]['tag'] = '../../outside'
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='tag'):
        recording_sources(run, path)
    path.write_bytes(original)
    (path.parent / 'subgoal_02/rollout_runtime.npz').unlink()
    with pytest.raises(ValueError, match='missing'):
        recording_sources(run, path)


def test_asset_identity_and_symlink_checked_before_rendering(recording):
    run, path, _ = recording
    asset = run / 'texture.png'
    asset.write_bytes(b'identity fixture only')
    scene = path.parent / 'scene_physics.xml'
    scene.write_text(scene.read_text().replace('<worldbody>',
        f'<asset><texture name="surface" type="2d" file="{asset}"/></asset><worldbody>'))
    contract = path.parent.parent / 'input_contract.json'
    contract.write_text(json.dumps({'schema': 'dexagent.input_contract.v1',
        'sha256': {str(asset): hashlib.sha256(asset.read_bytes()).hexdigest()}}))
    assert asset in recording_sources(run, path)['files']
    asset.write_bytes(b'changed texture')
    with pytest.raises(ValueError, match='asset identity'):
        recording_sources(run, path)
    asset.unlink()
    asset.symlink_to(scene)
    with pytest.raises(ValueError, match='regular files'):
        recording_sources(run, path)


def test_cubemap_face_files_cannot_bypass_asset_binding(recording):
    run, path, _ = recording
    scene = path.parent / 'scene_physics.xml'
    scene.write_text(scene.read_text().replace('<worldbody>',
        '<asset><texture type="skybox" fileleft="/unbound/left.png"/></asset><worldbody>'))
    with pytest.raises(ValueError, match='cubemap'):
        recording_sources(run, path)


def test_preview_manager_binds_trial_and_only_actual_recordings(recording, monkeypatch):
    from workbench.index import Index
    from workbench.previews import Previews

    run, path, _ = recording
    (run / 'run_summary.json').write_text(json.dumps({'run': run.name, 'status': 'failed'}))
    helper = run.parent / 'agent/rss_sharpa.py'
    helper.parent.mkdir()
    helper.write_text('# process identity fixture')
    index = Index(run.parent, run.parent, run.parent / 'state')
    index.refresh()
    run_id = index.runs()[0].id
    artifact = index.artifact(path, run_id)
    spawn = subprocess.Popen
    def sleeper(argv, **options):
        assert argv[-2:] == ['--mode', 'diagnostic']
        return spawn([sys.executable, '-c', 'import time; time.sleep(60)'], **options)
    monkeypatch.setattr('workbench.previews.subprocess.Popen', sleeper)
    manager = Previews(index, run.parent / 'state')
    try:
        job = manager.start(run_id, trial_report_id=artifact.id)
        assert job.trial_report_id == artifact.id and job.status == 'generating'
        saved = json.loads((manager.directory(job.id) / 'job.json').read_text())
        assert len([p for p in saved['source_hashes'] if p.endswith('rollout_runtime.npz')]) == 2
        assert not any('trajectory_proposal' in p for p in saved['source_hashes'])
        assert manager.start(run_id, trial_report_id=artifact.id).id == job.id
        with pytest.raises(ValueError, match='not both'):
            manager.start(run_id, episode_id='some-episode', trial_report_id=artifact.id)
    finally:
        manager.close()
