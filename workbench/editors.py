"""Supervise an isolated native Light Editor over a copy of a registered scene."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

from .index import identity, timestamp
from .models import EditorSession, SceneFile, SceneRevision, SceneRevisionRef
from .reports import load_object


class EditorBusy(RuntimeError):
    pass


class Editors:
    def __init__(self, repo: Path, state: Path, *, scene_roots=(), python: Path | None = None,
                 robot_assets: Path | None = None, timeout_s: float = 300):
        self.repo = repo.resolve()
        self.state_dir = state.resolve()
        self.root = self.state_dir / 'editors'
        self.root.mkdir(parents=True, exist_ok=True)
        self.roots = tuple(p.resolve() for p in (
            self.repo / 'SimFoundry/assets/scenes', self.repo / 'SimFoundry/Data',
            self.state_dir / 'resources/simfoundry/assets/scenes', *scene_roots))
        # Keep the venv's bin/python spelling: resolving its symlink selects the base environment.
        self.python = Path(os.path.abspath(python or self.state_dir / 'editor-venv/bin/python'))
        self.robot_assets = (robot_assets or self.state_dir / 'resources/simfoundry/robot-assets').resolve()
        self.timeout_s = timeout_s
        self.lock = threading.RLock()
        self.processes: dict[str, tuple[subprocess.Popen, float]] = {}
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @property
    def available(self):
        return self.python.is_file() and (self.repo / 'SimFoundry/scripts/interactive/light_editor/server.py').is_file()

    @staticmethod
    def _validate_scene(data):
        objects = data.get('objects_info')
        if (not isinstance(data.get('state'), dict) or not isinstance(objects, dict)
                or not isinstance(objects.get('init_info'), dict)):
            raise ValueError('native scene needs state and objects_info.init_info objects')
        for entry in data['objects_info']['init_info'].values():
            if not isinstance(entry, dict) or not isinstance(entry.get('args'), dict):
                raise ValueError('native object entries need an args object')
            if entry['args'].get('usd_path') is not None and not isinstance(entry['args']['usd_path'], str):
                raise ValueError('native usd_path must be a string')

    def scenes(self) -> list[SceneFile]:
        values = []
        seen = set()
        for root in self.roots:
            if not root.is_dir():
                continue
            for candidate in sorted(root.rglob('*.json')):
                path = candidate.resolve()
                if path in seen or not any(path.is_relative_to(r) for r in self.roots):
                    continue
                seen.add(path)
                expected = '_scene_state_' in path.name or path.name == 'reconstructed_og_scene.json'
                issue = None
                digest = ''
                objects = 0
                try:
                    data, payload = load_object(path)
                    if not expected and not {'objects_info', 'state'} <= data.keys():
                        continue
                    self._validate_scene(data)
                    digest = hashlib.sha256(payload).hexdigest()
                    objects = len(data['objects_info']['init_info'])
                except (OSError, ValueError, AttributeError) as exc:
                    if not expected:
                        continue
                    issue = f'{type(exc).__name__}: {exc}'
                values.append(SceneFile(id=identity(str(path)), name=path.stem, path=str(path),
                                        sha256=digest, objects=objects, issue=issue))
        return values

    def scene(self, scene_id: str):
        for item in self.scenes():
            if item.id == scene_id:
                if item.issue:
                    raise ValueError(item.issue)
                return item
        raise KeyError(scene_id)

    def start(self, scene_id: str, revision: SceneRevisionRef | None = None) -> EditorSession:
        scene = self.scene(scene_id)
        if not self.available:
            raise ValueError('native editor environment is missing; configure --editor-python')
        with self.lock:
            parent = revision.model_dump() if revision else None
            source = Path(scene.path)
            source_sha256 = scene.sha256
            if revision:
                directory = self.directory(revision.editor_id)
                origin = json.loads((directory / 'job.json').read_text())
                if origin['scene_id'] != scene_id or origin['source'] != scene.path:
                    raise ValueError('candidate belongs to a different scene')
                source = self.revision_path(revision.editor_id, revision.revision_id)
                source_sha256 = origin['source_sha256']
            data, payload = load_object(source)
            expected = revision.sha256 if revision else scene.sha256
            if hashlib.sha256(payload).hexdigest() != expected:
                raise ValueError('scene version changed while opening it; refresh the history and retry')
            self._validate_scene(data)
            for key, (process, _) in self.processes.items():
                if process.poll() is None:
                    state = self.session(key)
                    if (state.scene_id == scene_id and state.source_sha256 == source_sha256
                            and state.parent_revision == revision):
                        return state
                    raise EditorBusy('请先保存并停止当前编辑会话，再打开其他场景。')
            # Scene copies keep the same assets, with paths resolved against the original document.
            # Geometry and original scene files are never rewritten by the managed editor.
            for entry in data['objects_info']['init_info'].values():
                args = entry.get('args', {})
                reference = args.get('usd_path')
                if reference:
                    args['usd_path'] = str((source.parent / reference).resolve())
            metadata = data.setdefault('metadata', {})
            if not isinstance(metadata, dict):
                raise ValueError('native scene metadata must be an object')
            metadata['workbench_source'] = {'scene_id': scene_id, 'path': scene.path, 'sha256': source_sha256,
                                            'parent_revision': parent}
            key = uuid.uuid4().hex[:24]
            directory = self.root / key
            directory.mkdir()
            working = directory / 'scene_scene_state_draft.json'
            working.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
            prefix = f'/api/workbench/v1/editors/{key}/view'
            job = {'scene_id': scene_id, 'source': scene.path, 'source_sha256': source_sha256,
                   'parent_revision': parent,
                   'working': str(working), 'working_sha256': hashlib.sha256(working.read_bytes()).hexdigest(),
                   'base_path': prefix}
            (directory / 'job.json').write_text(json.dumps(job))
            environment = dict(os.environ, PYTHONUNBUFFERED='1')
            environment['PYTHONPATH'] = str(self.repo) + (os.pathsep + environment['PYTHONPATH'] if environment.get('PYTHONPATH') else '')
            command = [str(self.python), '-m', 'workbench.editors', '--parent', str(os.getpid()),
                       '--native', str(self.repo / 'SimFoundry/scripts/interactive/light_editor'),
                       '--scene', str(working), '--host', '127.0.0.1', '--port', '0',
                       '--base-path', prefix, '--cache-root', str(directory / 'cache'),
                       '--ready-file', str(directory / 'ready.json'), '--fixed-scene',
                       '--recents-file', str(directory / 'recents.json'),
                       '--compose-root', str(directory / 'composed'), '--scene-root', str(directory)]
            if self.robot_assets.is_dir():
                command += ['--robot-asset-dir', str(self.robot_assets)]
            asset_root = Path(scene.path).parent / 'objects'
            if asset_root.is_dir():
                command += ['--asset-root', str(asset_root)]
            with (directory / 'server.log').open('wb') as log:
                process = subprocess.Popen(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT,
                                           env=environment, start_new_session=True)
            self.processes[key] = process, time.monotonic()
            return self.session(key)

    def directory(self, key: str) -> Path:
        if (not re.fullmatch('[a-f0-9]{24}', key) or (self.root / key).resolve().parent != self.root
                or not (self.root / key / 'job.json').is_file()):
            raise KeyError(key)
        return self.root / key

    def revisions(self, key: str) -> list[SceneRevision]:
        directory = self.directory(key)
        job = json.loads((directory / 'job.json').read_text())
        values = []
        for path in sorted(directory.glob('*_scene_state_light_edit_*.json'), reverse=True):
            if path.resolve().parent != directory:
                continue
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            values.append(SceneRevision(id=identity(str(path)), editor_id=key, name=path.name,
                sha256=digest, source_sha256=job['source_sha256'], parent_revision=job.get('parent_revision'),
                modified=timestamp(path.stat().st_mtime),
                url=f'/api/workbench/v1/editors/{key}/revisions/{identity(str(path))}'))
        return values

    def scene_revisions(self, scene_id: str) -> list[SceneRevision]:
        values = []
        found = False
        for path in self.root.glob('*/job.json'):
            directory = self.directory(path.parent.name)
            if json.loads((directory / 'job.json').read_text())['scene_id'] == scene_id:
                found = True
                values.extend(self.revisions(directory.name))
        if not found and not any(scene.id == scene_id for scene in self.scenes()):
            raise KeyError(scene_id)
        return sorted(values, key=lambda item: (item.modified, item.id), reverse=True)

    def revision_path(self, key: str, revision_id: str) -> Path:
        directory = self.directory(key)
        for revision in self.revisions(key):
            if revision.id == revision_id:
                return directory / revision.name
        raise KeyError(revision_id)

    def session(self, key: str) -> EditorSession:
        with self.lock:
            directory = self.directory(key)
            job = json.loads((directory / 'job.json').read_text())
            changed = True
            source = Path(job['source']).resolve()
            if any(source.is_relative_to(root) for root in self.roots) and source.is_file():
                changed = hashlib.sha256(source.read_bytes()).hexdigest() != job['source_sha256']
            base = dict(id=key, scene_id=job['scene_id'], source_sha256=job['source_sha256'],
                        parent_revision=job.get('parent_revision'), source_changed=changed, revisions=self.revisions(key))
            handle = self.processes.get(key)
            if not handle or handle[0].poll() is not None:
                failure = directory / 'failure.txt'
                if handle and handle[0].returncode and not (directory / 'stopped.txt').exists() and not failure.exists():
                    log = directory / 'server.log'
                    with log.open('rb') as stream:
                        stream.seek(max(0, log.stat().st_size - 4096))
                        failure.write_text(f'editor process exited ({handle[0].returncode})\n' + stream.read().decode('utf-8', errors='replace'))
                return EditorSession(**base, status='failed' if failure.is_file() else 'stopped',
                    message=failure.read_text() if failure.is_file() else '编辑进程已停止；已保存的候选版本仍可下载。')
            ready = directory / 'ready.json'
            if ready.is_file():
                endpoint = json.loads(ready.read_text())
                if (not isinstance(endpoint.get('port'), int) or not 1 <= endpoint['port'] <= 65535
                        or endpoint.get('pid') != handle[0].pid or endpoint.get('base_path') != job['base_path']
                        or endpoint.get('scene') != job['working'] or endpoint.get('scene_sha256') != job['working_sha256']):
                    self._fail(key, 'editor readiness does not match the managed scene and process')
                    return self.session(key)
                try:
                    with self.http.open(f'http://127.0.0.1:{endpoint["port"]}{job["base_path"]}/api/session', timeout=1) as response:
                        current = json.load(response)
                    if current.get('scene') != job['working']:
                        self._fail(key, 'native editor changed its bound scene')
                        return self.session(key)
                    return EditorSession(**base, status='ready', message='原生离线布局编辑；保存为新候选，不代表物理验收通过。',
                                         url=job['base_path'] + '/')
                except (OSError, ValueError):
                    return EditorSession(**base, status='unresponsive', url=job['base_path'] + '/',
                                         message='编辑进程仍存活，暂未应答；保留页面以保护未保存修改。')
            if time.monotonic() - handle[1] > self.timeout_s:
                self._fail(key, 'native editor startup timed out; inspect the generation log')
                return self.session(key)
            return EditorSession(**base, status='starting', message='正在核对场景并提取原生 USD 代理…')

    def endpoint(self, key: str) -> str:
        state = self.session(key)
        if state.status not in {'ready', 'unresponsive'}:
            raise EditorBusy(state.message)
        ready = json.loads((self.directory(key) / 'ready.json').read_text())
        return f'http://127.0.0.1:{ready["port"]}'

    @staticmethod
    def _terminate(process):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)

    def _fail(self, key, message):
        (self.directory(key) / 'failure.txt').write_text(message)
        self._terminate(self.processes[key][0])

    def stop(self, key: str) -> EditorSession:
        with self.lock:
            self.directory(key)
            if key in self.processes:
                (self.directory(key) / 'stopped.txt').write_text('stopped by the workspace')
                self._terminate(self.processes[key][0])
            return self.session(key)

    def poll(self):
        for key in list(self.processes):
            self.session(key)

    def close(self):
        with self.lock:
            for process, _ in self.processes.values():
                self._terminate(process)


def main():
    import argparse
    import sys
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent', type=int, required=True)
    parser.add_argument('--native', type=Path, required=True)
    args, native_args = parser.parse_known_args()
    if os.getppid() != args.parent:
        raise SystemExit('editor supervisor exited before startup')
    def watch_parent():
        # Linux PDEATHSIG tracks the spawning *thread*, which a web worker may retire.
        # The process parent survives thread retirement; a real API exit reparents this child.
        while True:
            time.sleep(1)
            if os.getppid() != args.parent:
                os.kill(os.getpid(), signal.SIGTERM)
                return
    threading.Thread(target=watch_parent, daemon=True, name='editor-parent').start()
    sys.path.insert(0, str(args.native))
    import server
    sys.argv = [str(args.native / 'server.py'), *native_args]
    server.main()


if __name__ == '__main__':
    main()
