"""Snapshot a native episode for download; never convert or certify policy data."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from .index import Index
from .models import EpisodeBundle
from .reports import MAX_REPORT_BYTES

BASE_FILES = ('meta.json', 'steps.npz')
STATE_FILES = ('privileged.npz', 'capture.npz', 'capture_manifest.json',
               'contacts.jsonl', 'events.jsonl', 'spans.jsonl')
MAX_BYTES = 2 * 1024**3
MAX_FILES = 1024
MAX_ARRAY_BYTES = 512 * 1024**2


class BundleBusy(RuntimeError):
    pass


def _stat_identity(value) -> tuple:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _identity(path: Path) -> tuple:
    return _stat_identity(path.stat())


def _regular(root: Path, path: Path) -> None:
    if not path.is_relative_to(root):
        raise ValueError('episode input escapes its source run')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f'episode bundle does not follow symbolic links: {path.name}')
    if not path.resolve().is_relative_to(root):
        raise ValueError('episode input escapes its source run')
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f'episode input is not a regular file: {path.name}')
    if any('\\' in part for part in path.relative_to(root).parts):
        raise ValueError('episode input contains a nonportable filename')


def _validate(path: Path, source) -> None:
    """Check readability without normalizing native data, clocks or NaN padding."""
    if path.suffix == '.json':
        if os.fstat(source.fileno()).st_size > MAX_REPORT_BYTES:
            raise ValueError(f'{path.name} exceeds the 32 MiB JSON limit')
        if not isinstance(json.load(source), dict):
            raise ValueError(f'expected a JSON object: {path.name}')
    elif path.suffix == '.jsonl':
        while line := source.readline(MAX_REPORT_BYTES + 1):
            if len(line) > MAX_REPORT_BYTES:
                raise ValueError(f'{path.name} has a JSONL row larger than 32 MiB')
            if line.strip() and not isinstance(json.loads(line), dict):
                raise ValueError(f'cannot package malformed JSONL: {path.name}')
    elif path.suffix == '.npz':
        import numpy as np
        with ZipFile(source) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_ARRAY_BYTES:
                raise ValueError(f'{path.name} exceeds the 512 MiB decoded-array limit')
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise ValueError(f'duplicate array members: {path.name}')
            for info in archive.infolist():
                if not info.filename.endswith('.npy'):
                    raise ValueError(f'non-array member in {path.name}')
                with archive.open(info) as member:
                    version = np.lib.format.read_magic(member)
                    reader = {(1, 0): np.lib.format.read_array_header_1_0,
                              (2, 0): np.lib.format.read_array_header_2_0}.get(version)
                    if reader is None:
                        raise ValueError(f'unsupported NPY header version: {version}')
                    shape, _, dtype = reader(member)
                    size = math.prod(shape) * dtype.itemsize
                    if (dtype.hasobject or any(n < 0 for n in shape)
                            or size > MAX_ARRAY_BYTES or size != info.file_size - member.tell()):
                        raise ValueError(f'invalid array shape, dtype or byte count in {path.name}')
                    # Consume the payload for CRC validation without allocating
                    # an ndarray from an untrusted or concurrently changed header.
                    remaining = size
                    for block in iter(lambda: member.read(1024 * 1024), b''):
                        remaining -= len(block)
                    if remaining:
                        raise ValueError(f'truncated array payload in {path.name}')


class EpisodeBundles:
    """One bounded archive build at a time; immutable downloads reuse their digest."""

    def __init__(self, index: Index, state_dir: Path):
        self.index = index
        self.root = state_dir.resolve() / 'episode_bundles'
        self.lock = threading.Lock()

    def directory(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError('episode bundle directory must not be a symbolic link')
        return self.root

    def path(self, bundle_id: str) -> Path:
        if not re.fullmatch('[a-f0-9]{64}', bundle_id):
            raise KeyError(bundle_id)
        path = self.directory() / f'{bundle_id}.zip'
        _regular(self.root, path)
        return path

    def _sources(self, run: Path, episode: Path, include_state: bool) -> tuple[dict, list[str]]:
        files = {}
        names = BASE_FILES + (STATE_FILES if include_state else ())
        for name in names:
            path = episode / name
            if path.exists() or path.is_symlink():
                _regular(run, path)
                files[f'episodes/{episode.name}/{name}'] = path
        cameras = episode / 'cameras'
        if cameras.is_symlink():
            raise ValueError('episode cameras directory must not be a symbolic link')
        for path in sorted(cameras.glob('*.mp4')):
            _regular(run, path)
            files[f'episodes/{episode.name}/cameras/{path.name}'] = path
        # Preserve whole-run provenance outside the subset's native store: its
        # aggregate counts must not masquerade as the exported episode count.
        for name in ('run_meta.json', 'episode_results.jsonl'):
            path = episode.parent / name
            if path.exists() or path.is_symlink():
                _regular(run, path)
                files[f'provenance/{name}'] = path
        if len(files) > MAX_FILES or sum(p.stat().st_size for p in files.values()) > MAX_BYTES:
            raise ValueError('episode bundle exceeds the 1024-file / 2 GiB limit')
        included = set(files.values())
        omitted = sorted(p.name + ('/' if p.is_dir() else '') for p in episode.iterdir()
                         if p not in included and p.name != 'cameras')
        if cameras.is_dir():
            omitted.extend('cameras/' + p.name + ('/' if p.is_dir() else '')
                           for p in sorted(cameras.iterdir()) if p not in included)
        return files, omitted

    def create(self, run_id: str, episode_id: str, *, include_simulator_state: bool = False) -> EpisodeBundle:
        if not self.lock.acquire(blocking=False):
            raise BundleBusy('another episode bundle is being prepared; retry when it finishes')
        temporary = None
        try:
            run, _ = self.index.run_path(run_id)
            if self.index.run(run_id).source_status in {'running', 'waiting'}:
                raise BundleBusy('source report still marks the run as active; check it after recording finishes')
            episode = next((e for e in self.index.episodes(run_id) if e.id == episode_id), None)
            if episode is None:
                raise KeyError(episode_id)
            directory = run / 'episodes' / episode.name
            _regular(run, directory / 'meta.json')
            files, omitted = self._sources(run, directory, include_simulator_state)
            before = {name: _identity(path) for name, path in files.items()}
            if sum(value[2] for value in before.values()) > MAX_BYTES:
                raise ValueError('episode bundle exceeds the 2 GiB limit')
            fd, name = tempfile.mkstemp(prefix='.building-', suffix='.zip', dir=self.directory())
            temporary = Path(name)
            os.close(fd)
            entries = []
            with ZipFile(temporary, 'w', compression=ZIP_STORED, allowZip64=True) as archive:
                for name, path in files.items():
                    _regular(run, path)
                    if _identity(path) != before[name]:
                        raise BundleBusy('source files changed; retry after recording finishes')
                    digest = hashlib.sha256()
                    size = 0
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(fd, 'rb') as source, archive.open(ZipInfo(name), 'w', force_zip64=True) as target:
                        if _stat_identity(os.fstat(source.fileno())) != before[name]:
                            raise BundleBusy('opened source differs from the selected snapshot')
                        _validate(path, source)
                        source.seek(0)
                        for block in iter(lambda: source.read(1024 * 1024), b''):
                            size += len(block)
                            if size > before[name][2]:
                                raise BundleBusy('source file grew during export; retry after recording finishes')
                            digest.update(block)
                            target.write(block)
                        if _stat_identity(os.fstat(source.fileno())) != before[name]:
                            raise BundleBusy('opened source changed during export')
                    if size != before[name][2] or _identity(path) != before[name]:
                        raise BundleBusy('source files changed during export; retry after recording finishes')
                    entries.append(dict(path=name, size=size, sha256=digest.hexdigest()))
                current, current_omitted = self._sources(run, directory, include_simulator_state)
                if current != files or current_omitted != omitted or any(_identity(p) != before[n] for n, p in files.items()):
                    raise BundleBusy('source file set changed during export; retry after recording finishes')
                manifest = dict(schema='embodiforge.episode_bundle.v1', run_id=run_id, run_name=run.name,
                    episode_id=episode_id, episode_name=episode.name, exported_episode_count=1,
                    includes_simulator_state=include_simulator_state, omitted_episode_entries=omitted,
                    training_format=None, training_eligibility='not_evaluated',
                    files=entries, provenance_scope='original whole run, not the exported subset')
                archive.writestr(ZipInfo('manifest.json'), json.dumps(manifest, ensure_ascii=False, indent=2))
                archive.writestr(ZipInfo('README.txt'),
                    'Native EpisodeStore snapshot, not a training-format conversion or task certification.\n'
                    'Read with EpisodeStore(<extracted>/episodes). Original source bytes are preserved.\n'
                    'provenance/ contains whole-run metadata/results; their counts are NOT subset counts.\n'
                    'meta.json and steps.npz may contain simulator observations and metrics even when raw\n'
                    'simulator-state files are omitted. Do not assume these are policy-safe observations.\n'
                    'Scene assets are not included. See manifest.json for omissions and SHA-256 values.\n')
            with temporary.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            output = self.directory() / f'{digest}.zip'
            temporary.replace(output)
            temporary = None
            return EpisodeBundle(id=digest, run_id=run_id, episode_id=episode_id,
                url=f'/api/workbench/v1/episode-bundles/{digest}/download', size=output.stat().st_size,
                file_count=len(entries), sha256=digest, includes_simulator_state=include_simulator_state,
                omitted_episode_entries=omitted)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            self.lock.release()
