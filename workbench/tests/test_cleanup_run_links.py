"""Explicit cleanup associations never infer scan identity or alter source evidence."""
import json

import pytest
from fastapi.testclient import TestClient

from agent.run_reports import make_report
from workbench.api import PREFIX, create_app


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / 'runs/reconstruction'
    root.mkdir(parents=True)
    report_path = root / 'stage_report.json'
    report_path.write_text(json.dumps(make_report(
        run='reconstruction', profile='backend_eval', stage='backend', status='passed',
        checks=[{'id': 'fixture', 'status': 'passed'}])))
    with TestClient(create_app(tmp_path, refresh_interval=0)) as client:
        run_id = client.get(PREFIX + '/runs').json()['items'][0]['id']
        manager = client.app.state.background_cleanups
        for index in range(33):
            job_id = f'{index:024x}'
            directory = manager.jobs_root / job_id
            directory.mkdir()
            source = directory / 'source.mp4'
            source.write_bytes(b'unchanged original capture')
            manager._write(directory, {
                'id': job_id, 'scene_name': f'fixture_{index}', 'source_copy': str(source),
                'source_video': {'id': 'source', 'name': 'capture.mp4', 'path': str(source), 'size': source.stat().st_size},
                'capture_type': 'scene_scan', 'status': 'review_mask', 'current_step': 'review', 'message': 'awaiting review',
                'include_objects': ['robot'], 'skip_objects': [], 'propagation_model': 'litetext',
            })
        yield client, manager, run_id, report_path


def test_link_and_unlink_are_explicit_and_retained_beyond_recent_global_jobs(workspace):
    client, manager, run_id, report = workspace
    job_id = '0' * 24
    source = manager.directory(job_id) / 'source.mp4'
    before = source.read_bytes(), report.read_bytes()
    assert client.get(PREFIX + '/background-cleanups/jobs', params={'run_id': run_id}).json() == []
    assert client.get(PREFIX + f'/background-cleanups/{job_id}').json()['run_id'] is None
    response = client.post(PREFIX + f'/background-cleanups/{job_id}/run', json={'run_id': run_id})
    assert response.status_code == 200
    assert response.json()['run_id'] == run_id
    assert response.json()['status'] == 'review_mask'  # association does not approve a mask
    linked = client.get(PREFIX + '/background-cleanups/jobs', params={'run_id': run_id}).json()
    assert [job['id'] for job in linked] == [job_id]
    assert before == (source.read_bytes(), report.read_bytes())
    assert json.loads((manager.directory(job_id) / 'job.json').read_text())['run_id'] == run_id
    assert client.post(PREFIX + f'/background-cleanups/{job_id}/run', json={'run_id': None}).status_code == 200
    assert client.get(PREFIX + '/background-cleanups/jobs', params={'run_id': run_id}).json() == []


def test_invalid_run_is_rejected_before_any_start_or_link(workspace, monkeypatch):
    client, manager, _, _ = workspace
    monkeypatch.setattr(manager, 'start', lambda *_: pytest.fail('invalid association must not start a worker'))
    job_id = '0' * 24
    path = manager.directory(job_id) / 'job.json'
    original = path.read_bytes()
    assert client.post(PREFIX + f'/background-cleanups/{job_id}/run', json={'run_id': 'missing'}).status_code == 404
    assert client.post(PREFIX + '/background-cleanups', json={'video_id': 'source', 'run_id': 'missing'}).status_code == 404
    assert client.get(PREFIX + '/background-cleanups/jobs', params={'run_id': 'missing'}).status_code == 404
    assert path.read_bytes() == original


def test_linking_cannot_race_a_live_phase_or_a_still_exiting_worker(workspace):
    client, manager, run_id, _ = workspace
    job_id = '0' * 24
    directory = manager.directory(job_id)
    job = manager._read(directory)
    job['status'] = 'inpainting'
    manager._write(directory, job)
    original = (directory / 'job.json').read_bytes()
    assert client.post(PREFIX + f'/background-cleanups/{job_id}/run', json={'run_id': run_id}).status_code == 409
    assert (directory / 'job.json').read_bytes() == original
    job['status'] = 'review_mask'
    manager._write(directory, job)
    class LiveProcess:
        def poll(self):
            return None
    manager.processes[job_id] = LiveProcess(), 0
    try:
        assert client.post(PREFIX + f'/background-cleanups/{job_id}/run', json={'run_id': run_id}).status_code == 409
    finally:
        manager.processes.clear()


def test_start_retains_requested_association_without_running_models(workspace, monkeypatch):
    from workbench.models import CleanupVideo
    client, manager, run_id, _ = workspace
    source = manager.directory('0' * 24) / 'source.mp4'
    video = CleanupVideo(id='source', name=source.name, path=str(source), size=source.stat().st_size)
    monkeypatch.setattr(manager, '_source_path', lambda *_: (video, source))
    monkeypatch.setattr(manager, '_require', lambda **_: None)
    launches = []
    monkeypatch.setattr(manager, '_launch', lambda job, phase: launches.append((job, phase)))
    response = client.post(PREFIX + '/background-cleanups', json={
        'video_id': 'source', 'run_id': run_id, 'propagation_model': 'sam3',
        'proposal_backend': 'manual', 'include_objects': ['robot'], 'capture_type': 'scene_scan',
    })
    assert response.status_code == 202
    job = response.json()
    assert job['run_id'] == run_id
    assert launches == [(job['id'], 'mask')]
    assert manager.job(job['id']).run_id == run_id
    assert (manager.directory(job['id']) / 'input/source.mp4').read_bytes() == source.read_bytes()
