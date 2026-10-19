"""Process lifecycle tests; native rendering is verified separately in the browser."""
import json
import subprocess
import sys

import pytest

from workbench.index import Index
from workbench.previews import PreviewBusy, Previews


@pytest.fixture
def preview_manager(tmp_path, monkeypatch):
    run = tmp_path / 'runs/recorded'
    run.mkdir(parents=True)
    (run / 'stage_report.json').write_text(json.dumps({'schema': 'dexagent.step_report.v1', 'status': 'passed',
                                                     'checks': [{'id': 'goal', 'status': 'passed'}]}))
    for name in ('original_source_scene.xml', 'sharpa_manifest.json'):
        (run / name).write_text('lifecycle fixture only')
    for episode_id in ('0000', '0001'):
        episode = run / 'episodes' / episode_id
        episode.mkdir(parents=True)
        (episode / 'meta.json').write_text(json.dumps({'success': True}))
        for name in ('steps.npz', 'privileged.npz'):
            (episode / name).write_bytes(b'process test fixture; not a rendering input')
    index = Index(tmp_path, tmp_path / 'runs', tmp_path / 'state')
    index.refresh()
    manager = Previews(index, tmp_path / 'state')
    spawn = subprocess.Popen

    def sleeper(argv, **options):
        assert argv[1:3] == ['-m', 'workbench.native_preview']
        return spawn([sys.executable, '-c', 'import time; time.sleep(60)'], **options)

    monkeypatch.setattr('workbench.previews.subprocess.Popen', sleeper)
    try:
        yield manager, index.runs()[0].id
    finally:
        manager.close()


def test_preview_creation_is_idempotent_and_other_jobs_are_serialized(preview_manager):
    manager, run_id = preview_manager
    first = manager.start(run_id)
    assert first.status == 'generating'
    second = manager.start(run_id)
    assert second.id == first.id and len(manager.processes) == 1
    other = manager.index.episodes(run_id)[1]
    with pytest.raises(PreviewBusy):
        manager.start(run_id, other.id)


def test_preview_timeout_releases_its_real_process(preview_manager):
    manager, run_id = preview_manager
    first = manager.start(run_id)
    process, _ = manager.processes[first.id]
    assert process.poll() is None
    manager.timeout_s = 0
    manager.poll()
    state = manager.state(first.id)
    assert state.status == 'failed' and 'timed out' in state.message
    assert process.poll() is not None


def test_incomplete_preview_output_cannot_be_served_as_ready(preview_manager):
    manager, run_id = preview_manager
    first = manager.start(run_id)
    directory = manager.directory(first.id)
    (directory / 'viewer.html').write_text('<html>incomplete</html>')
    (directory / 'result.json').write_text(json.dumps({'status': 'ready'}))
    assert manager.state(first.id).status == 'failed'
    with pytest.raises(KeyError):
        manager.directory('../../source')


def test_preview_marks_changed_source_as_a_snapshot(preview_manager):
    manager, run_id = preview_manager
    first = manager.start(run_id)
    directory = manager.directory(first.id)
    (directory / 'viewer.html').write_text('<html>metadata fixture only</html>')
    (directory / 'result.json').write_text(json.dumps({'status': 'ready', 'mode': 'native_recorded_state_replay',
        'model_identity_verified': True, 'physics_steps_executed': 0, 'preview_frames': 2}))
    assert not manager.state(first.id).stale
    run, _ = manager.index.run_path(run_id)
    (run / 'sharpa_manifest.json').write_text('changed source version')
    assert manager.state(first.id).stale
