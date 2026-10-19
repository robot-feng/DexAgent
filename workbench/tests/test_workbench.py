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
