"""Acceptance and fault tests for the read-only workbench boundary."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest

from agent.run_reports import make_report
from workbench.api import PREFIX, create_app


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def stage_report(path, *, status='passed', checks=None, schema='dexagent.step_report.v1'):
    data = make_report(run='sample', profile='pipeline_eval', stage=path.parent.name, status=status,
                       checks=checks if checks is not None else [{'id': 'physical_goal', 'required': True, 'status': 'passed'}])
    data.update(schema=schema, attempt_id='source-attempt')
    write(path, data)
    return data


@pytest.fixture
def workspace(tmp_path):
    run = tmp_path / 'runs/sample'
    run.mkdir(parents=True)
    write(run / 'run_summary.json', {'run': 'sample', 'status': 'scope_completed', 'ok': True,
          'scope_complete': True, 'full_pipeline_complete': False, 'requested_stages': ['stage3', 'stage4']})
    stage_report(run / 'stage3/stage_report.json')
    stage_report(run / 'stage4/stage_report.json')
    write(run / 'stage3/stage4_task/outputs/run_report.json', {
        'schema': 'dexagent.stage4_run_report.v1', 'status': 'passed', 'release_ready': False})
    write(tmp_path / 'tool_library/index.json', {'tools': [
        {'name': 'goal_gate', 'type': 'verifier', 'title': 'Goal gate', 'status': 'tested', 'inputs': [], 'outputs': []}]})
    return tmp_path, run


@pytest.fixture
def client(workspace):
    repo, _ = workspace
    with TestClient(create_app(repo, refresh_interval=0)) as test:
        yield test


def run_detail(client):
    values = client.get(PREFIX + '/runs').json()['items']
    assert len(values) == 1
    return client.get(PREFIX + '/runs/' + values[0]['id']).json()


def test_resume_scope_and_release_stay_separate(client, workspace):
    _, root = workspace
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*.json')}
    detail = run_detail(client)
    assert detail['execution'] == 'completed'
    assert detail['validation'] == 'passed'
    assert detail['scope']['scope_complete'] is True
    assert detail['scope']['single_invocation_full_pipeline_complete'] is False
    assert detail['scope']['evidence_complete_across_invocations'] is None
    assert detail['release'] == 'not_evaluated'
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}


@pytest.mark.parametrize(('status', 'checks', 'expected'), [
    ('warning', [{'id': 'paths', 'required': False, 'status': 'warning'}], 'warning'),
    ('failed', [{'id': 'goal', 'required': True, 'status': 'failed'}], 'failed'),
    ('passed', [{'id': 'goal', 'required': True, 'status': 'failed'}], 'incomplete'),
    ('passed', [], 'incomplete'),
])
def test_machine_evidence_is_not_overruled_by_summary(client, workspace, status, checks, expected):
    stage_report(workspace[1] / 'stage3/stage_report.json', status=status, checks=checks)
    assert run_detail(client)['validation'] == expected


def test_unknown_schema_and_partial_json_are_visible(client, workspace):
    stage_report(workspace[1] / 'stage3/stage_report.json', schema='future.unknown.v9')
    assert run_detail(client)['validation'] == 'incomplete'
    path = workspace[1] / 'stage3/progress.json'
    path.write_text('{"round":')
    detail = run_detail(client)
    assert any('JSONDecodeError' in i['message'] for i in detail['issues'])
    assert detail['validation'] == 'incomplete'
    write(path, {'round': 3, 'max_rounds': 10, 'status': 'finished'})
    assert not any('JSONDecodeError' in i['message'] for i in run_detail(client)['issues'])
    assert run_detail(client)['validation'] == 'incomplete'
    stage_report(workspace[1] / 'stage3/stage_report.json')
    assert not run_detail(client)['issues']


def test_finished_progress_cannot_certify_stage(client, workspace):
    (workspace[1] / 'stage3/stage_report.json').unlink()
    write(workspace[1] / 'stage3/progress.json', {'status': 'finished', 'round': 10})
    detail = run_detail(client)
    stage = next(s for s in detail['stages'] if s['id'] == 'stage3')
    assert stage['report'] is None
    assert detail['validation'] == 'incomplete'
    assert any('completed scope is missing' in issue['message'] for issue in detail['issues'])


def test_partial_progress_keeps_last_snapshot_only_within_the_same_attempt(client, workspace):
    path = workspace[1] / 'stage3/progress.json'
    write(path, {'round': 7, 'max_rounds': 10, 'status': 'running'})
    detail = run_detail(client)
    path.write_text('{"round":')
    detail = run_detail(client)
    stage = next(s for s in detail['stages'] if s['id'] == 'stage3')
    assert stage['progress']['round'] == 7 and stage['progress_stale']
    report = json.loads((workspace[1] / 'stage3/stage_report.json').read_text())
    report['attempt_id'] = 'new-attempt'
    write(workspace[1] / 'stage3/stage_report.json', report)
    stage = next(s for s in run_detail(client)['stages'] if s['id'] == 'stage3')
    assert stage['progress'] is None and not stage['progress_stale']


def test_unbound_cross_invocation_claim_is_not_accepted(client, workspace):
    write(workspace[1] / 'pipeline_validation.json', {
        'schema': 'dexagent.pipeline_validation.v1',
        'completion': {'evidence_complete_across_sessions': True}, 'stages': {}})
    detail = run_detail(client)
    assert detail['scope']['evidence_complete_across_invocations'] is None
    assert any('bindings' in i['message'] for i in detail['issues'])


def test_cross_invocation_binding_uses_producer_digest_and_detects_changed_attempt(client, workspace):
    root = workspace[1]
    bindings = {}
    for key in ('stage1', 'stage2', 'stage3'):
        path = root / key / 'stage_report.json'
        value = stage_report(path)
        bindings[key] = dict(report=str(path), status='passed', attempt_id=value['attempt_id'], checks=value['checks'])
    step_path = root / 'stage3/stage4_task/outputs/reports/render.json'
    step = stage_report(step_path)
    step['artifact_sha256'] = 'producer-output-hash'
    write(step_path, step)
    bindings['stage4'] = dict(report=str(root / 'stage3/stage4_task/outputs/run_report.json'), status='passed',
        steps={'render': dict(artifact_sha256='producer-output-hash', attempt_id=step['attempt_id'], checks=step['checks'])})
    write(root / 'pipeline_validation.json', {'schema': 'dexagent.pipeline_validation.v1',
          'completion': {'evidence_complete_across_sessions': True}, 'stages': bindings})
    assert run_detail(client)['scope']['evidence_complete_across_invocations'] is True
    changed = json.loads((root / 'stage3/stage_report.json').read_text())
    changed['attempt_id'] = 'different-attempt'
    write(root / 'stage3/stage_report.json', changed)
    assert run_detail(client)['scope']['evidence_complete_across_invocations'] is None


def test_legacy_episode_missing_metadata_is_browsable(client, workspace):
    root = workspace[1]
    write(root / 'episodes/0000/meta.json', {'schema': 'robosimstudio.episode.v1', 'success': True,
                                          'task': '', 'embodiment': '', 'recorded_steps': 4})
    detail = run_detail(client)
    result = client.get(PREFIX + '/runs/' + detail['id'] + '/episodes')
    assert result.status_code == 200
    episode = result.json()['items'][0]
    assert episode['task'] is None and episode['embodiment'] is None
    assert set(episode['missing_fields']) == {'task', 'embodiment', 'run_meta'}


def test_cross_run_episode_catalog_filters_native_metadata_and_keeps_source_links(client, workspace):
    repo, first_run = workspace
    write(first_run / 'episodes/0000/meta.json', {
        'schema': 'robosimstudio.episode.v1', 'task': 'pour cup', 'embodiment': 'Franka',
        'success': True, 'sim_s': 1.25, 'recorded_steps': 50,
    })
    write(first_run / 'episodes/0001/meta.json', {
        'schema': 'robosimstudio.episode.v1', 'task': 'place cup',
        'success': False, 'sim_s': 2.0, 'recorded_steps': 80,
    })
    write(first_run / 'episodes/run_meta.json', {'schema': 'robosimstudio.run.v1'})
    external_episode = write(repo / 'outside/0002/meta.json', {
        'schema': 'robosimstudio.episode.v1', 'task': 'must not leak',
    })
    (first_run / 'episodes/0002').symlink_to(external_episode.parent, target_is_directory=True)

    second_run = repo / 'runs/second'
    write(second_run / 'run_summary.json', {
        'run': 'second', 'status': 'scope_completed', 'ok': True,
        'requested_stages': ['stage1'],
    })
    stage_report(second_run / 'stage1/stage_report.json')
    write(second_run / 'episodes/0000/meta.json', {
        'schema': 'robosimstudio.episode.v1', 'task': 'sort blocks', 'embodiment': 'Panda',
        'success': None, 'sim_s': 3.5, 'recorded_steps': 90,
    })
    client.post(PREFIX + '/refresh')

    project = client.post(PREFIX + '/projects', json={'name': 'Catalog test'}).json()
    imported = client.post(PREFIX + '/imports', json={
        'directory': 'second', 'project_id': project['id'],
    })
    assert imported.status_code == 200

    all_items = client.get(PREFIX + '/episode-catalog', params={'limit': 10}).json()
    assert all_items['total'] == 3
    assert {item['run_name'] for item in all_items['items']} == {'sample', 'second'}
    assert all(item['project_name'] for item in all_items['items'])

    passed = client.get(PREFIX + '/episode-catalog', params={'success': 'passed'}).json()
    assert passed['total'] == 1 and passed['items'][0]['task'] == 'pour cup'
    unknown = client.get(PREFIX + '/episode-catalog', params={'success': 'unknown'}).json()
    assert unknown['total'] == 1 and unknown['items'][0]['run_name'] == 'second'
    incomplete = client.get(PREFIX + '/episode-catalog', params={'metadata': 'incomplete'}).json()
    assert incomplete['total'] == 2
    searched = client.get(PREFIX + '/episode-catalog', params={'q': 'place'}).json()
    assert searched['total'] == 1 and searched['items'][0]['success'] is False
    filtered = client.get(PREFIX + '/episode-catalog', params={
        'project_id': project['id'], 'run_id': imported.json()['id'],
    }).json()
    assert filtered['total'] == 1 and filtered['items'][0]['project_id'] == project['id']
    page = client.get(PREFIX + '/episode-catalog', params={'limit': 1, 'offset': 1}).json()
    assert page['total'] == 3 and len(page['items']) == 1


@pytest.mark.parametrize('payload', ['{broken', '{}', '{"success": true, "sim_s": "yesterday"}'])
def test_invalid_or_empty_episode_metadata_remains_visible(client, workspace, payload):
    path = workspace[1] / 'episodes/0000/meta.json'
    path.parent.mkdir(parents=True)
    path.write_text(payload)
    detail = run_detail(client)
    response = client.get(PREFIX + '/runs/' + detail['id'] + '/episodes')
    assert response.status_code == 200
    assert response.json()['total'] == 1
    episode = response.json()['items'][0]
    assert episode['success'] is None and episode['sim_s'] is None
    assert bool(episode['issues']) == (payload != '{}')
    assert client.get(PREFIX + '/artifacts/' + episode['meta']['id'] + '/content').text == payload


def test_file_ids_ranges_and_symlink_replacement(client, workspace):
    root = workspace[1]
    video = root / 'simulation.mp4'
    video.write_bytes(b'0123456789')
    artifact = client.app.state.index.artifact(video)
    result = client.get(PREFIX + f'/artifacts/{artifact.id}/content', headers={'Range': 'bytes=2-5'})
    assert result.status_code == 206 and result.content == b'2345'
    outside = workspace[0] / 'outside.mp4'
    outside.write_bytes(b'private')
    video.unlink()
    video.symlink_to(outside)
    result = client.get(PREFIX + f'/artifacts/{artifact.id}/content')
    assert result.status_code == 422 and b'private' not in result.content
    assert client.get(PREFIX + '/artifacts/arbitrary-path/content').status_code == 404
    assert client.post(PREFIX + '/imports', json={'directory': '../../', 'project_id': 'imported'}).status_code == 422


def test_background_cleanup_lists_local_video_and_reports_missing_legacy_segmenter(tmp_path):
    from workbench.api import create_app
    videos = tmp_path / 'SimFoundry/docs/assets/example_videos'
    source = videos / 'ThrowAwayTrash.mp4'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'smoke input placeholder')
    state = tmp_path / 'isolated-state'
    with TestClient(create_app(tmp_path, state_dir=state, refresh_interval=0)) as test:
        catalog = test.get(PREFIX + '/background-cleanups/catalog')
        assert catalog.status_code == 200
        video = next(item for item in catalog.json()['videos'] if item['name'] == 'ThrowAwayTrash.mp4')
        assert video['capture_type'] == 'operation_demo'
        assert video['path'].endswith('ThrowAwayTrash.mp4')
        rejected = test.post(PREFIX + '/background-cleanups', json={
            'video_id': video['id'], 'capture_type': 'scene_scan', 'proposal_backend': 'manual',
            'include_objects': ['robot arm'], 'propagation_model': 'sam3',
        })
        assert rejected.status_code == 422
        assert '不能标记为相机扫描' in rejected.json()['detail']
        unavailable = test.post(PREFIX + '/background-cleanups', json={
            'video_id': video['id'], 'capture_type': 'operation_demo', 'proposal_backend': 'manual',
            'include_objects': ['robot arm'], 'propagation_model': 'sam3',
        })
        assert unavailable.status_code == 503
        assert 'sam3' in unavailable.json()['detail']
        assert not list((state / 'background_cleanups/jobs').glob('*/job.json'))


def test_background_cleanup_build_gate_does_not_assume_operation_demo_camera_is_fixed(tmp_path):
    from workbench.background_cleanups import BackgroundCleanups

    manager = BackgroundCleanups(tmp_path, tmp_path / 'state')
    job_id = 'a' * 24
    write(manager.jobs_root / job_id / 'job.json', {
        'status': 'review_clean', 'capture_type': 'operation_demo',
    })

    with pytest.raises(ValueError, match='只有标记为相机扫描的素材才能构建 3D 场景'):
        manager.build_scene(job_id)


def test_background_cleanup_build_preflight_reports_current_upstream_gemini_gap(tmp_path, monkeypatch):
    from workbench.background_cleanups import BackgroundCleanups, CleanupUnavailable
    from workbench.models import CleanupCatalog, CleanupRequirement

    requirements = [CleanupRequirement(id=name, title=name, available=True, detail='ready') for name in (
        'video_tools', 'conda', 'simfoundry_env', 'simfoundry_sam3', 'sam2_checkpoint', 'void_env',
        'da3_env', 'nerfstudio_env', '3dgrut_env', 'mesh_env', 'auto_bg_sources',
    )]
    requirements.append(CleanupRequirement(id='gemini', title='Gemini', available=False,
                                            detail='GCP project is missing'))
    manager = BackgroundCleanups(tmp_path, tmp_path / 'state')
    monkeypatch.setattr(manager, 'catalog', lambda: CleanupCatalog(
        videos=[], requirements=requirements, preview_available=True, build_available=False))

    with pytest.raises(CleanupUnavailable, match='GCP project is missing'):
        manager._require(phase='build', proposal_backend='codex_external')


def test_gemini_preflight_requires_vertex_adc_but_accepts_api_key(tmp_path, monkeypatch):
    from workbench import background_cleanups

    for key in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GCLOUD_PROJECT', 'GOOGLE_CLOUD_PROJECT',
                'GOOGLE_APPLICATION_CREDENTIALS', 'CLOUDSDK_CONFIG', 'SIMFOUNDRY_GEMINI_BACKEND'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setattr(background_cleanups.shutil, 'which', lambda _name: None)

    monkeypatch.setenv('GCLOUD_PROJECT', 'example-project')
    available, detail = background_cleanups._gemini_capability()
    assert not available
    assert 'ADC 凭据' in detail

    adc = tmp_path / 'adc.json'
    adc.write_text('{}')
    monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS', str(adc))
    available, detail = background_cleanups._gemini_capability()
    assert not available

    adc.write_text(json.dumps({
        'type': 'service_account', 'client_email': 'test@example.invalid',
        'private_key': 'test-only-placeholder', 'token_uri': 'https://oauth2.example.invalid/token',
    }))
    available, detail = background_cleanups._gemini_capability()
    assert available
    assert 'Vertex 项目和 ADC' in detail

    monkeypatch.delenv('GCLOUD_PROJECT')
    monkeypatch.delenv('GOOGLE_APPLICATION_CREDENTIALS')
    monkeypatch.setenv('GEMINI_API_KEY', 'configured-test-key')
    available, detail = background_cleanups._gemini_capability()
    assert available
    assert 'Gemini API key' in detail


def test_gemini_preflight_honors_simfoundry_forced_backend(tmp_path, monkeypatch):
    from workbench import background_cleanups

    for key in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GCLOUD_PROJECT', 'GOOGLE_CLOUD_PROJECT',
                'GOOGLE_APPLICATION_CREDENTIALS', 'CLOUDSDK_CONFIG'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setattr(background_cleanups.shutil, 'which', lambda _name: None)
    monkeypatch.setenv('GEMINI_API_KEY', 'configured-test-key')
    monkeypatch.setenv('SIMFOUNDRY_GEMINI_BACKEND', 'vertex')

    available, detail = background_cleanups._gemini_capability()
    assert not available
    assert '强制设为 vertex' in detail

    monkeypatch.setenv('GCLOUD_PROJECT', 'example-project')
    adc = tmp_path / 'adc.json'
    adc.write_text(json.dumps({
        'type': 'service_account', 'client_email': 'test@example.invalid',
        'private_key': 'test-only-placeholder', 'token_uri': 'https://oauth2.example.invalid/token',
    }))
    monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS', str(adc))
    available, detail = background_cleanups._gemini_capability()
    assert available
    assert 'Vertex 项目和 ADC' in detail

    monkeypatch.setenv('SIMFOUNDRY_GEMINI_BACKEND', 'api_key')
    monkeypatch.delenv('GEMINI_API_KEY')
    available, detail = background_cleanups._gemini_capability()
    assert not available
    assert '强制设为 api_key' in detail


def test_background_cleanup_codex_proposal_does_not_require_gemini(tmp_path, monkeypatch):
    from workbench.background_cleanups import BackgroundCleanups
    from workbench.models import CleanupCatalog, CleanupRequirement

    requirements = [CleanupRequirement(id=name, title=name, available=True, detail='ready') for name in (
        'video_tools', 'conda', 'simfoundry_env', 'simfoundry_sam3', 'sam2_checkpoint', 'auto_bg_sources',
    )]
    requirements.extend([
        CleanupRequirement(id='gemini', title='Gemini', available=False, detail='not configured'),
        CleanupRequirement(id='void_env', title='VOID', available=False, detail='not needed for masks'),
    ])
    manager = BackgroundCleanups(tmp_path, tmp_path / 'state')
    monkeypatch.setattr(manager, 'catalog', lambda: CleanupCatalog(
        videos=[], requirements=requirements, preview_available=True, build_available=False))

    manager._require(phase='mask', proposal_backend='codex_external', propagation_model='sam3')


def test_background_cleanup_codex_external_proposal_uses_shared_agent_transport(tmp_path):
    import threading
    import time
    from agent.external import atomic_json
    from workbench import background_cleanup_worker as worker

    job_dir = tmp_path / 'jobs' / '0123456789abcdef01234567'
    job_dir.mkdir(parents=True)
    keyframe = job_dir / 'frame.png'
    keyframe.write_bytes(b'test image bytes')
    write(job_dir / 'job.json', {
        'floor_category': 'desk', 'include_objects': ['fixed robot arm'],
        'skip_objects': ['table'], 'proposal_backend': 'codex_external',
    })
    result, errors = [], []

    def request_proposal():
        try:
            result.append(worker._agent_object_proposal(job_dir, keyframe, {}))
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=request_proposal)
    thread.start()
    transport = job_dir / 'agent_external'
    deadline = time.monotonic() + 5
    status = {}
    while time.monotonic() < deadline:
        status_path = transport / 'status.json'
        if status_path.is_file():
            status = json.loads(status_path.read_text())
            if status.get('state') == 'waiting':
                break
        time.sleep(0.01)
    assert status.get('state') == 'waiting'
    request = json.loads(Path(status['request']).read_text())
    image = request['messages'][0]['content'][1]
    assert image['type'] == 'image' and Path(image['path']).is_file()
    response = [{'type': 'tool_use', 'id': 'proposal-1', 'name': 'propose_objects',
                 'input': {'objects': ['robot arm', 'gripper'], 'evidence': 'visible foreground robot'}}]
    atomic_json(transport / 'responses' / f'{request["id"]}.json', {
        'session': request['session'], 'id': request['id'], 'content': response,
    })
    thread.join(timeout=5)

    assert not thread.is_alive() and not errors
    assert result == [['robot arm', 'gripper']]
    saved = json.loads((job_dir / 'job.json').read_text())
    assert saved['proposal_transport_dir'] == str(transport)
    assert json.loads((transport / 'status.json').read_text())['state'] == 'finished'


def test_background_cleanup_build_does_not_pass_auto_bg_key_to_canonical_hydra(tmp_path, monkeypatch):
    from workbench import background_cleanup_worker as worker

    repo = tmp_path
    scene = 'cleanup_scan'
    job_dir = tmp_path / 'state/jobs/0123456789abcdef01234567'
    job_dir.mkdir(parents=True)
    source = job_dir / 'source.mp4'
    source.write_bytes(b'smoke')
    job = {
        'scene_name': scene, 'source_copy': str(source), 'floor_category': 'desk',
        'propagation_model': 'sam3', 'include_objects': ['robot arm', 'gripper'],
        'proposed_objects': ['gripper'], 'skip_objects': ['table'],
    }
    write(job_dir / 'job.json', job)
    commands = []

    def record_run(_job_dir, command, *, cwd, env):
        commands.append(command)
        if len(commands) == 2:
            output = repo / 'SimFoundry/assets/scenes' / scene / f'{scene}_scene_state_auto_bg.json'
            output.parent.mkdir(parents=True)
            output.write_text('{}')

    monkeypatch.setattr(worker, '_run', record_run)
    worker._build_phase(repo, Path('/usr/bin/conda'), job_dir, {}, job)

    assert len(commands) == 2
    assert commands[0][0] == 'bash'
    assert not any(arg.startswith('s1_quadmask.') for arg in commands[0])
    assert 's3_ground.frame_selection.mode=heuristic' in commands[0]
    assert 's3_ground.floor_categories=["desk"]' in commands[0]
    assert 's5_scene.force_categories=["robot arm", "gripper"]' in commands[0]
    assert json.loads((job_dir / 'job.json').read_text())['status'] == 'ready'


def test_background_cleanup_build_never_falls_back_to_upstream_object_proposal(tmp_path):
    from workbench import background_cleanup_worker as worker

    job_dir = tmp_path / 'state/jobs/0123456789abcdef01234567'
    job_dir.mkdir(parents=True)
    source = job_dir / 'source.mp4'
    source.write_bytes(b'smoke')
    job = {
        'scene_name': 'cleanup_scan', 'source_copy': str(source), 'floor_category': 'desk',
        'include_objects': ['table'], 'proposed_objects': ['robot arm'], 'skip_objects': ['table', 'robot arm'],
    }

    with pytest.raises(RuntimeError, match='避免静默退回上游 Gemini 提案'):
        worker._build_phase(tmp_path, Path('/usr/bin/conda'), job_dir, {}, job)


def test_bad_source_symlink_does_not_disable_other_runs(client, workspace):
    repo, root = workspace
    outside = write(repo / 'outside.json', {'secret': 'never serve'})
    path = root / 'stage3/stage_report.json'
    path.unlink()
    path.symlink_to(outside)
    result = client.post(PREFIX + '/refresh')
    assert result.status_code == 200
    detail = run_detail(client)
    assert detail['validation'] == 'incomplete'
    assert any('outside' in issue['message'] for issue in detail['issues'])


def test_log_cursor_handles_truncation_and_rotation(client, workspace):
    path = workspace[1] / 'pipeline.log'
    path.write_text('one\ntwo\nthree\n')
    artifact = client.app.state.index.artifact(path)
    url = PREFIX + f'/artifacts/{artifact.id}/text'
    initial = client.get(url, params={'limit': 4}).json()
    assert initial['text'] == 'one\n' and initial['cursor'] == 4
    following = client.get(url, params={'cursor': initial['cursor'], 'identity': initial['identity'], 'limit': 4}).json()
    assert following['text'] == 'two\n' and not following['reset']
    path.write_text('x')
    assert client.get(url, params={'cursor': 8}).json()['reset']
    path.rename(path.with_suffix('.old'))
    path.write_text('new log')
    rotated = client.get(url, params={'cursor': 4, 'identity': initial['identity']}).json()
    assert rotated['reset'] and rotated['text'] == 'new log'


def test_project_assignment_is_idempotent_and_survives_refresh(client):
    detail = run_detail(client)
    project = client.post(PREFIX + '/projects', json={'name': 'Robot experiment'}).json()
    body = {'directory': detail['name'], 'project_id': project['id']}
    assert client.post(PREFIX + '/imports', json=body).json()['project_id'] == project['id']
    assert client.post(PREFIX + '/imports', json=body).json()['project_id'] == project['id']
    client.post(PREFIX + '/refresh')
    assert run_detail(client)['project_id'] == project['id']
    assert client.get(PREFIX + '/projects').json()[-1]['run_count'] == 1


def test_tools_use_local_index_and_no_pipeline_capability_is_claimed(client):
    result = client.get(PREFIX + '/tools', params={'q': 'goal', 'tool_type': 'verifier'}).json()
    assert result['total'] == 1 and result['items'][0]['name'] == 'goal_gate'
    capabilities = {c['id']: c for c in client.get(PREFIX + '/capabilities').json()}
    assert capabilities['reports']['available'] and not capabilities['execution']['available']
    assert client.get(PREFIX + '/nonexistent').status_code == 404
    assert 'newton' not in sys.modules


def test_openapi_serialization_contract_has_required_collections(client):
    document = client.get('/openapi.json').json()
    for model, field in [('Report', 'checks'), ('Stage', 'history'), ('RunDetail', 'media'), ('Scope', 'requested_stages')]:
        assert field in document['components']['schemas'][model]['required']


def test_episode_series_uses_native_clock_and_rejects_privileged_fields(client, workspace):
    import numpy as np
    path = workspace[1] / 'episodes/0000/steps.npz'
    path.parent.mkdir(parents=True)
    np.savez(path, sim_time=np.array([0.0, .2, .4, .6]), joint_q=np.arange(8).reshape(4, 2), body_q=np.ones((4, 7)))
    artifact = client.app.state.index.artifact(path)
    url = PREFIX + f'/artifacts/{artifact.id}/series'
    result = client.get(url, params={'field': 'joint_q', 'channel': 1, 'max_points': 2})
    assert result.status_code == 200
    assert result.json()['times'] == [0, .6] and result.json()['values'] == [1, 7]
    assert result.json()['source_points'] == 4
    assert 'body_q' not in result.json()['available_fields']
    assert client.get(url, params={'field': 'body_q'}).status_code == 422
    assert client.get(url, params={'channel': 8}).status_code == 422
    np.savez(path, sim_time=[0, .2], joint_q=np.zeros((3, 2)))
    assert client.get(url).status_code == 422


def test_dexagent_trajectory_uses_its_own_clock_and_coordinate_names(client, workspace):
    import numpy as np
    path = workspace[1] / 'stage3/stage4_task/trajectory.npz'
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, time_s=[0., .034, .066], qpos=np.arange(6).reshape(3, 2),
             qvel=np.ones((3, 1)), ctrl=np.zeros((3, 1)), joint_names=['x', 'hinge'],
             actuator_names=['motor'], substep_qpos=np.ones((5, 2)))
    run = run_detail(client)
    listing = client.get(PREFIX + f'/runs/{run["id"]}/episodes').json()
    assert listing['items'] == [] and listing['total'] == 0
    url = PREFIX + f'/artifacts/{listing["recorded_trajectory"]["id"]}/series'
    result = client.get(url, params={'channel': 1, 'max_points': 2}).json()
    assert result['field'] == 'qpos' and result['channel_name'] == 'hinge'
    assert result['times'] == [0., .066] and result['values'] == [1., 5.]
    assert result['available_fields'] == ['qpos', 'qvel', 'ctrl']
    assert client.get(url, params={'field': 'ctrl'}).json()['channel_name'] == 'motor'
    assert client.get(url, params={'field': 'joint_q'}).status_code == 422
    assert client.get(url, params={'field': 'substep_qpos'}).status_code == 422
    np.savez(path, time_s=[0., .034, .02], qpos=np.ones((3, 2)))
    assert client.get(url).status_code == 422
