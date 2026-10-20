"""Native downloads preserve evidence and never imply training eligibility."""
import hashlib
import io
import json
import os
from zipfile import ZipFile

from fastapi.testclient import TestClient
import numpy as np
import pytest

from workbench.api import PREFIX, create_app
from workbench.episode_bundles import BundleBusy, EpisodeBundles, STATE_FILES
from workbench.index import Index


@pytest.fixture
def source(tmp_path):
    run = tmp_path / 'runs/native'
    episode = run / 'episodes/0000'
    episode.mkdir(parents=True)
    (run / 'run_summary.json').write_text(json.dumps({'run': 'native', 'status': 'completed'}))
    (episode / 'meta.json').write_text(json.dumps({
        'schema': 'robosimstudio.episode.v1', 'episode_id': 0, 'success': False,
        'recorded_steps': 2, 'captured_steps': 3, 'task': 'place cup', 'embodiment': 'test',
        'metrics': {'task_success_evaluated': False}}))
    np.savez(episode / 'steps.npz', action=[[1, np.nan], [2, 3]], action_len=[1, 2],
             joint_q=[[0, 1], [2, 3]], sim_time=[.1, .2])
    np.savez(episode / 'privileged.npz', body_q=np.zeros((2, 1, 7)), static_anchor=[1])
    np.savez(episode / 'capture.npz', body_q=np.zeros((3, 1, 7)), sim_time=[0, .1, .2])
    (episode / 'capture_manifest.json').write_text('{"schema":"native-capture"}')
    for name in ('events.jsonl', 'spans.jsonl', 'contacts.jsonl'):
        (episode / name).write_text('{"step":0}\n')
    (episode / 'cameras').mkdir()
    (episode / 'cameras/ego.mp4').write_bytes(b'video-byte-preservation-fixture; not render evidence')
    (episode / 'not-native.txt').write_text('do not export arbitrary files')
    (episode.parent / 'run_meta.json').write_text('{"episodes":99,"finished":""}')
    (episode.parent / 'episode_results.jsonl').write_text('{"episode_id":0}\n{"episode_id":1}\n')
    index = Index(tmp_path, tmp_path / 'runs', tmp_path / 'state')
    index.refresh()
    run_id = index.runs()[0].id
    episode_id = index.episodes(run_id)[0].id
    return tmp_path, run, episode, index, run_id, episode_id


def test_bundle_preserves_bytes_native_layout_and_diagnostic_opt_in(source):
    repo, run, episode, index, run_id, episode_id = source
    original = {p: p.read_bytes() for p in run.rglob('*') if p.is_file()}
    manager = EpisodeBundles(index, repo / 'state')
    result = manager.create(run_id, episode_id)
    assert result.id == hashlib.sha256(manager.path(result.id).read_bytes()).hexdigest()
    with ZipFile(manager.path(result.id)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['training_eligibility'] == 'not_evaluated'
        assert manifest['training_format'] is None and manifest['exported_episode_count'] == 1
        assert archive.read('provenance/run_meta.json') == original[episode.parent / 'run_meta.json']
        assert 'episodes/run_meta.json' not in archive.namelist()
        for item in manifest['files']:
            payload = archive.read(item['path'])
            assert hashlib.sha256(payload).hexdigest() == item['sha256']
            assert len(payload) == item['size']
        for name in STATE_FILES:
            assert f'episodes/0000/{name}' not in archive.namelist()
            assert name in result.omitted_episode_entries
        with np.load(io.BytesIO(archive.read('episodes/0000/steps.npz')), allow_pickle=False) as data:
            assert np.isnan(data['action'][0, 1])  # Native variable-action padding is legitimate.
            assert data['sim_time'].tolist() == [.1, .2]
    assert manager.create(run_id, episode_id).id == result.id
    full = manager.create(run_id, episode_id, include_simulator_state=True)
    assert full.id != result.id and full.includes_simulator_state
    with ZipFile(manager.path(full.id)) as archive:
        for name in STATE_FILES:
            assert archive.read(f'episodes/0000/{name}') == original[episode / name]
        assert 'episodes/0000/not-native.txt' not in archive.namelist()
    assert original == {p: p.read_bytes() for p in original}


@pytest.mark.parametrize('name', ['steps.npz', 'cameras', '../run_meta.json', 'capture.npz'])
def test_source_symlinks_rejected_even_inside_permitted_root(source, name):
    repo, _, episode, index, run_id, episode_id = source
    path = episode / name
    other = repo / 'runs/elsewhere'
    other.mkdir()
    if path.is_dir():
        (path / 'ego.mp4').unlink()
        path.rmdir()
        path.symlink_to(other, target_is_directory=True)
    else:
        target = other / path.name
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    with pytest.raises(ValueError, match='symbolic link'):
        EpisodeBundles(index, repo / 'state').create(run_id, episode_id, include_simulator_state=True)


@pytest.mark.parametrize('kind', ['corrupt_npz', 'object_array', 'corrupt_json', 'corrupt_jsonl'])
def test_malformed_native_files_do_not_produce_downloads(source, kind):
    repo, _, episode, index, run_id, episode_id = source
    if kind == 'corrupt_npz':
        (episode / 'steps.npz').write_bytes(b'broken')
    elif kind == 'object_array':
        np.savez(episode / 'steps.npz', value=np.array([{'arbitrary': 1}], dtype=object))
    elif kind == 'corrupt_json':
        (episode / 'meta.json').write_text('{')
    else:
        (episode / 'events.jsonl').write_text('not json\n')
    with TestClient(create_app(repo, state_dir=repo / 'state', refresh_interval=0)) as client:
        response = client.post(f'{PREFIX}/runs/{run_id}/episodes/{episode_id}/bundle',
                               json={'include_simulator_state': True})
    assert response.status_code == 422
    assert not list((repo / 'state/episode_bundles').glob('*.zip'))


def test_source_change_during_copy_discards_temporary_output(source, monkeypatch):
    import workbench.episode_bundles as bundles
    repo, _, episode, index, run_id, episode_id = source
    original = bundles._identity
    calls = 0
    def changed(path):
        nonlocal calls
        if path == episode / 'steps.npz':
            calls += 1
            if calls == 3:
                path.write_bytes(path.read_bytes() + b'changed after copy')
        return original(path)
    monkeypatch.setattr(bundles, '_identity', changed)
    with pytest.raises(BundleBusy, match='changed'):
        EpisodeBundles(index, repo / 'state').create(run_id, episode_id)
    assert not list((repo / 'state/episode_bundles').iterdir())


def test_download_api_binding_limits_and_active_run(source, monkeypatch):
    import workbench.episode_bundles as bundles
    repo, run, _, _, run_id, episode_id = source
    app = create_app(repo, state_dir=repo / 'state', refresh_interval=0)
    with TestClient(app) as client:
        url = f'{PREFIX}/runs/{run_id}/episodes/{episode_id}/bundle'
        assert client.post(url.replace(episode_id, 'wrong-id'), json={}).status_code == 404
        response = client.post(url, json={})
        assert response.status_code == 200, response.text
        bundle = response.json()
        downloaded = client.get(bundle['url'])
        assert downloaded.headers['content-type'] == 'application/zip'
        assert downloaded.headers['content-disposition'].startswith('attachment;')
        assert hashlib.sha256(downloaded.content).hexdigest() == bundle['sha256']
        assert client.get(f'{PREFIX}/episode-bundles/invalid/download').status_code == 404
        monkeypatch.setattr(app.state.executions, 'is_run_active', lambda _: True)
        assert client.post(url, json={}).status_code == 409
        monkeypatch.setattr(app.state.executions, 'is_run_active', lambda _: False)
        monkeypatch.setattr(bundles, 'MAX_BYTES', 1)
        assert client.post(url, json={}).status_code == 422
        monkeypatch.setattr(bundles, 'MAX_BYTES', 2 * 1024**3)
        (run / 'run_summary.json').write_text('{"status":"running"}')
        assert client.post(url, json={}).status_code == 409


def test_capture_only_and_legacy_missing_sidecars_are_preserved(source):
    repo, _, episode, index, run_id, episode_id = source
    for path in (episode / 'steps.npz', episode.parent / 'run_meta.json', episode.parent / 'episode_results.jsonl'):
        path.unlink()
    result = EpisodeBundles(index, repo / 'state').create(run_id, episode_id, include_simulator_state=True)
    with ZipFile(repo / 'state/episode_bundles' / f'{result.id}.zip') as archive:
        assert 'episodes/0000/capture.npz' in archive.namelist()
        assert 'episodes/0000/steps.npz' not in archive.namelist()


def test_declared_array_shape_cannot_bypass_memory_limit(source, monkeypatch):
    repo, _, episode, index, run_id, episode_id = source
    member = io.BytesIO()
    np.lib.format.write_array_header_1_0(member, {
        'descr': '<f8', 'fortran_order': False, 'shape': (10**12,)})
    with ZipFile(episode / 'steps.npz', 'w') as archive:
        archive.writestr('action.npy', member.getvalue())
    def no_allocation(*args, **kwargs):
        raise AssertionError('invalid header must be rejected before np.load allocates')
    monkeypatch.setattr(np, 'load', no_allocation)
    with pytest.raises(ValueError, match='array shape'):
        EpisodeBundles(index, repo / 'state').create(run_id, episode_id)
    assert not list((repo / 'state/episode_bundles').iterdir())


def test_opened_inode_must_match_selected_source(source, monkeypatch):
    repo, _, episode, index, run_id, episode_id = source
    real_open = os.open
    def substituted(path, flags, *args, **kwargs):
        # Simulate parent substitution between pathname check and open, without
        # mutating the selected path (its later stat would still look correct).
        if str(path) == str(episode / 'steps.npz'):
            path = episode / 'privileged.npz'
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', substituted)
    with pytest.raises(BundleBusy, match='opened source differs'):
        EpisodeBundles(index, repo / 'state').create(run_id, episode_id)
    assert not list((repo / 'state/episode_bundles').iterdir())


def test_non_mp4_camera_entries_are_reported_as_omissions(source):
    repo, _, episode, index, run_id, episode_id = source
    (episode / 'cameras/depth').mkdir()
    (episode / 'cameras/frames.json').write_text('{}')
    bundle = EpisodeBundles(index, repo / 'state').create(run_id, episode_id)
    assert {'cameras/depth/', 'cameras/frames.json'}.issubset(bundle.omitted_episode_entries)
