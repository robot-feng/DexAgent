"""Supervise bounded, derived 3D previews without running a pipeline."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

from .index import Index, preview_kind
from .models import Preview


class PreviewBusy(RuntimeError):
    pass


class Previews:
    def __init__(self, index: Index, state_dir: Path, *, timeout_s: float = 300):
        self.index = index
        self.root = state_dir.resolve() / 'previews' / 'native'
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.lock = threading.RLock()
        self.processes: dict[str, tuple[subprocess.Popen, float]] = {}

    def start(self, run_id: str, episode_id: str | None = None) -> Preview:
        run, _ = self.index.run_path(run_id)
        mode = preview_kind(run)
        if mode in {'rss', 'rss_assets'}:
            episodes = self.index.episodes(run_id)
            episode = next((e for e in episodes if e.id == episode_id), None) if episode_id else next(iter(episodes), None)
            if episode is None:
                raise ValueError('no native episode is available for this run')
            episode_id = episode.id
            episode_dir = self.index.artifact_path(episode.meta.id).parent
            sources = [episode_dir / 'steps.npz', episode_dir / 'privileged.npz']
            if mode == 'rss_assets':
                from agent.rss_simfoundry import snapshot_assets
                _, assets = snapshot_assets(run)
                sources += [run / 'simfoundry_import.json', episode_dir / 'meta.json', *assets]
            else:
                sources += [run / 'original_source_scene.xml', run / 'sharpa_manifest.json']
        elif mode == 'dexagent':
            if episode_id:
                raise ValueError('DexAgent trajectory preview does not take an EpisodeStore ID')
            episode_dir = run / 'stage3/stage4_task'
            sources = [episode_dir / name for name in ('scene.xml', 'trajectory.npz', 'outputs/run_report.json')]
        else:
            raise ValueError('this run has no supported recorded 3D source')
        if not all(p.is_file() for p in sources):
            raise ValueError('recorded 3D source is incomplete; scene identity and recorded states are required')
        digest = hashlib.sha256(Path(__file__).with_name('native_preview.py').read_bytes())
        if mode == 'rss_assets':
            digest.update((self.index.repo / 'agent/rss_simfoundry.py').read_bytes())
        digest.update(run_id.encode())
        versions = {}
        for path in sources:
            path = self.index.permitted(path)
            digest.update(str(path).encode())
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            stat = path.stat()
            versions[str(path)] = [stat.st_mtime_ns, stat.st_size]
        key = digest.hexdigest()[:24]
        output = self.root / key
        with self.lock:
            if (output / 'job.json').is_file():
                # The full content key matches. Refresh cheap version markers, also for legacy caches.
                job = json.loads((output / 'job.json').read_text())
                job['source_versions'] = versions
                (output / 'job.json').write_text(json.dumps(job))
                previous = self.state(key)
                if previous.status in {'ready', 'generating'}:
                    return previous
            for other in list(self.processes):
                if self.state(other).status == 'generating':
                    raise PreviewBusy('another 3D preview is being generated; retry when it finishes')
            output.mkdir(parents=True, exist_ok=True)
            (output / 'job.json').write_text(json.dumps({'run_id': run_id, 'episode_id': episode_id,
                                                       'mode': mode, 'source_versions': versions}))
            (output / 'result.json').unlink(missing_ok=True)
            environment = dict(os.environ, PYTHONUNBUFFERED='1')
            environment['PYTHONPATH'] = str(self.index.repo) + (os.pathsep + environment['PYTHONPATH'] if environment.get('PYTHONPATH') else '')
            with (output / 'generate.log').open('wb') as log:
                process = subprocess.Popen([sys.executable, '-m', 'workbench.native_preview', '--run', str(run),
                    '--episode', str(episode_dir), '--output', str(output), '--mode', mode], cwd=output,
                    stdout=log, stderr=subprocess.STDOUT, env=environment, start_new_session=True)
            self.processes[key] = process, time.monotonic()
            return self.state(key)

    def directory(self, key: str) -> Path:
        if not re.fullmatch('[a-f0-9]{24}', key):
            raise KeyError(key)
        directory = self.root / key
        if not (directory / 'job.json').is_file():
            raise KeyError(key)
        return directory

    def state(self, key: str) -> Preview:
        with self.lock:
            directory = self.directory(key)
            job = json.loads((directory / 'job.json').read_text())
            versions = job.get('source_versions') or {}
            stale = not versions
            for name, expected in versions.items():
                try:
                    stat = self.index.permitted(Path(name)).stat()
                    stale = stale or expected != [stat.st_mtime_ns, stat.st_size]
                except (OSError, ValueError):
                    stale = True
            handle = self.processes.get(key)
            result_path = directory / 'result.json'
            if handle and handle[0].poll() is None and time.monotonic() - handle[1] > self.timeout_s:
                self._stop(handle[0])
                result_path.write_text(json.dumps({'status': 'failed', 'message': '3D preview generation timed out'}))
            if result_path.is_file():
                result = json.loads(result_path.read_text())
                if (result.get('status') == 'ready' and (directory / 'viewer.html').is_file()
                        and result.get('mode') == 'native_recorded_state_replay'
                        and result.get('model_identity_verified') is True
                        and result.get('physics_steps_executed') == 0
                        and result.get('preview_frames', 0) >= 2):
                    return Preview(id=key, run_id=job['run_id'], status='ready', message=result.get('message', ''),
                                   url=f'/api/workbench/v1/previews/{key}/viewer', source_frames=result.get('source_frames'),
                                   preview_frames=result.get('preview_frames'), duration_s=result.get('duration_s'),
                                   state_source=result.get('state_source'), stale=stale)
                return Preview(id=key, run_id=job['run_id'], status='failed',
                               message=result.get('message', 'generation failed') if result.get('status') == 'failed'
                               else 'preview output is missing its model, clock or no-physics validation', stale=stale)
            if handle and handle[0].poll() is None:
                return Preview(id=key, run_id=job['run_id'], status='generating',
                               message='正在核对模型身份并生成原生 3D 回放，首次加载需要编译 CPU 可视化内核。', stale=stale)
            return Preview(id=key, run_id=job['run_id'], status='failed',
                           message='preview worker ended without a result; see the local generation log', stale=stale)

    @staticmethod
    def _stop(process: subprocess.Popen):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait(timeout=5)
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)

    def close(self):
        with self.lock:
            for process, _ in self.processes.values():
                self._stop(process)

    def poll(self):
        with self.lock:
            for key in list(self.processes):
                self.state(key)
