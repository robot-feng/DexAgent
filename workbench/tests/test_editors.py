"""Exercise native editor boundaries independently of expensive scene generation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import threading
import urllib.error
import urllib.request

import pytest
from fastapi.testclient import TestClient

from workbench.api import PREFIX, create_app
from workbench.editors import Editors, EditorBusy

REPO = Path(__file__).resolve().parents[2]
EDITOR_PYTHON = REPO / '.workbench/editor-venv/bin/python'


def write_scene(root, name='sample', *, asset='object.usda'):
    path = root / f'{name}_scene_state_latest.json'
    root.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'versions': {'omnigibson': {'version': 'fixture'}},
        'metadata': {}, 'state': {'registry': {'object_registry': {'prop': {
            'root_link': {'pos': [0, 0, 0], 'ori': [0, 0, 0, 1], 'lin_vel': [1, 2, 3], 'ang_vel': [1, 0, 0]}}}}},
        'init_info': {}, 'objects_info': {'init_info': {'prop': {
            'class_name': 'USDObject', 'class_module': 'omnigibson.objects.usd_object',
            'args': {'name': 'prop', 'usd_path': asset, 'scale': [1, 1, 1]}}}}}))
    (root / asset).write_text('''#usda 1.0
(
    defaultPrim = "Object"
    metersPerUnit = 1
    upAxis = "Z"
)
def Xform "Object" {
    def Mesh "mesh" {
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(0, 0, 0), (0.1, 0, 0), (0, 0.1, 0.1)]
        uniform token subdivisionScheme = "none"
    }
}
''')
    return path


def test_scene_catalog_keeps_corrupt_entries_and_excludes_external_symlinks(tmp_path):
    source = write_scene(tmp_path / 'scenes')
    corrupt = source.with_name('broken_scene_state_latest.json')
    corrupt.write_text('{broken')
    outsider = write_scene(tmp_path / 'outside', 'external')
    (source.parent / 'symlink_scene_state_latest.json').symlink_to(outsider)
    (source.parent / 'not_scene.json').write_text('{}')
    manager = Editors(tmp_path, tmp_path / 'state', scene_roots=(source.parent,))
    values = manager.scenes()
    assert len(values) == 2
    assert next(s for s in values if s.name.startswith('broken')).issue
    with pytest.raises(ValueError):
        manager.scene(next(s for s in values if s.issue).id)
    with pytest.raises(KeyError):
        manager.directory('../../private')


def test_editor_venv_path_and_process_lifecycle_preserve_source(tmp_path, monkeypatch):
    source = write_scene(tmp_path / 'scenes')
    before = source.read_bytes()
    venv = tmp_path / 'venv/bin/python'
    venv.parent.mkdir(parents=True)
    venv.symlink_to(sys.executable)
    native = tmp_path / 'SimFoundry/scripts/interactive/light_editor/server.py'
    native.parent.mkdir(parents=True)
    native.touch()
    spawn = subprocess.Popen
    commands = []

    def sleeper(argv, **kwargs):
        commands.append(argv)
        return spawn([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)

    monkeypatch.setattr('workbench.editors.subprocess.Popen', sleeper)
    manager = Editors(tmp_path, tmp_path / 'state', scene_roots=(source.parent,), python=venv)
    try:
        scene = manager.scenes()[0]
        first = manager.start(scene.id)
        assert first.status == 'starting' and commands[0][0] == str(venv)
        assert manager.start(scene.id).id == first.id
        working = json.loads((manager.directory(first.id) / 'scene_scene_state_draft.json').read_text())
        assert working['objects_info']['init_info']['prop']['args']['usd_path'] == str(source.parent / 'object.usda')
        write_scene(source.parent, 'second')
        with pytest.raises(EditorBusy):
            manager.start(next(s.id for s in manager.scenes() if s.id != scene.id))
        source.write_text(source.read_text() + '\n')
        assert manager.session(first.id).source_changed
        process = manager.processes[first.id][0]
        manager.timeout_s = 0
        manager.poll()
        assert manager.session(first.id).status == 'failed' and process.poll() is not None
        assert source.read_bytes() == before + b'\n'
    finally:
        manager.close()


@pytest.mark.skipif(not EDITOR_PYTHON.is_file(), reason='optional isolated Light Editor environment is not installed')
def test_real_editor_proxy_saves_candidates_and_enforces_native_revision(tmp_path):
    source = write_scene(tmp_path / 'scenes')
    original = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source.parent.iterdir()}
    with TestClient(create_app(REPO, runs_root=tmp_path / 'runs', state_dir=tmp_path / 'state',
                    scene_roots=(source.parent,), editor_python=EDITOR_PYTHON, refresh_interval=0)) as client:
        scene = client.get(PREFIX + '/scenes').json()['items'][0]
        result = client.post(PREFIX + '/editors', json={'scene_id': scene['id']})
        assert result.status_code == 201
        key = result.json()['id']
        manager = client.app.state.editors
        process = manager.processes[key][0]
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            state = client.get(PREFIX + '/editors/' + key).json()
            if state['status'] != 'starting':
                break
            time.sleep(.2)
        assert state['status'] == 'ready', state
        url = state['url']
        html = client.get(url)
        assert html.status_code == 200 and html.headers['x-frame-options'] == 'SAMEORIGIN'
        token = re.search(r'name="editor-token" content="([^"]+)"', html.text).group(1)
        assert client.get(url + 'app.js').status_code == 200
        assert 'editorFetch' in client.get(url + 'app.js').text
        manifest = client.get(url + 'data/manifest.json').json()
        assert manifest['complete'] and manifest['objects'][0]['status'] == 'ok'
        assert client.get(url + 'data/' + manifest['objects'][0]['glb']).content[:4] == b'glTF'
        headers = {'Origin': 'http://testserver', 'X-Editor-Token': token}
        current = client.get(url + 'api/scene_state').json()
        obj = {k: current['objects']['prop'][k] for k in ('position', 'orientation', 'scale')}
        obj['position'] = [.05, 0, 0]
        payload = {'edits': {'prop': obj}, 'remove': [], 'complete_snapshot': True,
                   'scene_revision': current['scene_revision'], 'base_scene_sha256': manifest['base_scene_sha256']}
        assert client.post(url + 'api/save', json=payload).status_code == 403
        assert client.post(url + 'api/save', json=payload, headers={**headers, 'Origin': 'http://external.test'}).status_code == 403
        result = client.post(url + 'api/save', json=payload, headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()['promoted'] is False
        assert client.post(url + 'api/save', json=payload, headers=headers).status_code == 409
        payload['scene_revision'] = result.json()['scene_revision']
        assert client.post(url + 'api/save', json={**payload, 'promote_latest': True}, headers=headers).status_code == 409
        for endpoint in ['open', 'save_table', 'save_cameras', 'task_cfg', 'export']:
            assert client.post(url + 'api/' + endpoint, json={}, headers=headers).status_code == 409
        versions = client.get(PREFIX + '/editors/' + key).json()['revisions']
        assert len(versions) == 1
        history_url = PREFIX + '/scenes/' + scene['id'] + '/revisions'
        history = client.get(history_url).json()
        assert history['total'] == 1 and history['items'] == versions
        candidate = client.get(versions[0]['url']).json()
        root = candidate['state']['registry']['object_registry']['prop']['root_link']
        assert root['pos'] == [.05, 0, 0] and root['lin_vel'] == [0, 0, 0] and root['ang_vel'] == [0, 0, 0]
        assert original == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in original}
        # Native API requests must use the same configured prefix as static resources.
        native = manager.endpoint(key)
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(native + '/api/session', timeout=2)
        assert exc.value.code == 404
        assert client.post(PREFIX + '/editors/' + key + '/stop').json()['status'] == 'stopped'
        assert process.poll() is not None
        assert client.get(url).status_code == 409
        assert client.get(versions[0]['url']).status_code == 200
        ref = {'editor_id': key, 'revision_id': versions[0]['id'], 'sha256': versions[0]['sha256']}
        reopen = {'scene_id': scene['id'], 'revision': ref}
        assert client.post(PREFIX + '/editors', json={**reopen, 'revision': {**ref, 'sha256': '0' * 64}}).status_code == 422
        other = write_scene(source.parent, 'other')
        other_id = next(s.id for s in manager.scenes() if s.path == str(other))
        assert client.post(PREFIX + '/editors', json={**reopen, 'scene_id': other_id}).status_code == 422
        # A new original version must not replace the candidate's recorded basis or edited pose.
        changed_original = source.read_bytes() + b'\n'
        source.write_bytes(changed_original)
        resumed = client.post(PREFIX + '/editors', json=reopen)
        assert resumed.status_code == 201, resumed.text
        resumed_key = resumed.json()['id']
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            state = client.get(PREFIX + '/editors/' + resumed_key).json()
            if state['status'] != 'starting':
                break
            time.sleep(.2)
        assert state['status'] == 'ready', state
        assert state['parent_revision'] == ref and state['source_changed']
        assert state['source_sha256'] == scene['sha256']
        assert client.post(PREFIX + '/editors', json=reopen).json()['id'] == resumed_key
        resumed_url = state['url']
        current = client.get(resumed_url + 'api/scene_state').json()
        assert current['objects']['prop']['position'] == [.05, 0, 0]
        manifest = client.get(resumed_url + 'data/manifest.json').json()
        token = re.search(r'name="editor-token" content="([^"]+)"', client.get(resumed_url).text).group(1)
        obj = {k: current['objects']['prop'][k] for k in ('position', 'orientation', 'scale')}
        obj['position'] = [.08, 0, 0]
        result = client.post(resumed_url + 'api/save', json={**payload, 'edits': {'prop': obj},
            'scene_revision': current['scene_revision'], 'base_scene_sha256': manifest['base_scene_sha256']},
            headers={**headers, 'X-Editor-Token': token})
        assert result.status_code == 200, result.text
        versions = client.get(PREFIX + '/editors/' + resumed_key).json()['revisions']
        next_candidate = client.get(versions[0]['url']).json()
        assert next_candidate['metadata']['workbench_source']['parent_revision'] == ref
        assert next_candidate['state']['registry']['object_registry']['prop']['root_link']['pos'] == [.08, 0, 0]
        assert versions[0]['parent_revision'] == ref
        assert client.get(history_url + '?limit=1').json()['total'] == 2
        assert len(client.get(history_url + '?limit=1&offset=1').json()['items']) == 1
        assert client.post(PREFIX + '/editors/' + resumed_key + '/stop').json()['status'] == 'stopped'
        restored = Editors(REPO, tmp_path / 'state', scene_roots=(source.parent,), python=EDITOR_PYTHON)
        assert len(restored.scene_revisions(scene['id'])) == 2
        assert source.read_bytes() == changed_original
        assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest
                   for path, digest in original.items() if path != source)
        # Source damage must not hide previously saved candidates and their downloads.
        source.write_text('{broken')
        assert client.get(history_url).json()['total'] == 2
        assert client.post(PREFIX + '/editors', json=reopen).status_code == 422
        source.unlink()
        assert client.get(history_url).json()['total'] == 2
        assert client.get(versions[0]['url']).status_code == 200
        assert client.get(PREFIX + '/scenes/' + '0' * 24 + '/revisions').status_code == 404


@pytest.mark.skipif(not EDITOR_PYTHON.is_file(), reason='optional isolated Light Editor environment is not installed')
def test_native_editor_survives_the_request_threads_exit(tmp_path):
    source = write_scene(tmp_path / 'scenes')
    manager = Editors(REPO, tmp_path / 'state', scene_roots=(source.parent,), python=EDITOR_PYTHON)
    created = []
    worker = threading.Thread(target=lambda: created.append(manager.start(manager.scenes()[0].id)))
    try:
        worker.start()
        worker.join(timeout=10)
        assert not worker.is_alive() and created
        key = created[0].id
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            state = manager.session(key)
            if state.status != 'starting':
                break
            time.sleep(.2)
        assert state.status == 'ready', state
        assert manager.processes[key][0].poll() is None
        original_http = manager.http
        class Unresponsive:
            def open(self, *args, **kwargs):
                raise TimeoutError('temporary stall')
        manager.http = Unresponsive()
        delayed = manager.session(key)
        assert delayed.status == 'unresponsive' and delayed.url == state.url
        assert manager.processes[key][0].poll() is None
        manager.http = original_http
        assert manager.session(key).status == 'ready'
    finally:
        manager.close()
