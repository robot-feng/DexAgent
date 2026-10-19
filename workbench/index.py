"""A local index over existing outputs. Source directories remain read-only."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid

from .models import Artifact, Episode, EpisodeCatalogItem, Issue, Project, RunDetail, RunSummary, Scope, Stage
from .reports import STAGE_TITLES, merge_validation, parse_report, read_object

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}
VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov'}
MODEL_EXTENSIONS = {'.xml', '.urdf', '.usd', '.usda', '.glb', '.obj', '.stl'}
TEXT_EXTENSIONS = {'.json', '.jsonl', '.log', '.txt', '.yaml', '.yml', '.py', '.csv'}
ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | MODEL_EXTENSIONS | TEXT_EXTENSIONS | {'.npz'}


def preview_kind(directory: Path) -> str | None:
    if ((directory / 'original_source_scene.xml').is_file() and (directory / 'sharpa_manifest.json').is_file()
            and any((directory / 'episodes').glob('*/privileged.npz'))):
        return 'rss'
    task = directory / 'stage3/stage4_task'
    if all((task / name).is_file() for name in ('scene.xml', 'trajectory.npz', 'outputs/run_report.json')):
        return 'dexagent'
    return None


def timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def identity(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


class Index:
    def __init__(self, repo: Path, runs_root: Path, state_dir: Path,
                 artifact_roots: tuple[Path, ...] = ()):
        self.repo = repo.resolve()
        self.runs_root = runs_root.resolve()
        self.roots = (self.runs_root, (self.repo / 'stage1_semantic_understanding/examples').resolve(),
                      *(p.resolve() for p in artifact_roots))
        state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = state_dir / 'metadata.sqlite'
        self.lock = threading.RLock()
        self._scanned_artifacts: set[str] = set()
        self._progress_cache: dict[str, tuple[str, dict]] = {}
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    fingerprint TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS run_artifacts (
                    run_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
                    PRIMARY KEY (run_id, artifact_id));
            ''')
            db.execute('INSERT OR IGNORE INTO projects VALUES (?, ?, ?)',
                       ('imported', '已有运行', datetime.now(timezone.utc).isoformat()))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def permitted(self, path: Path) -> Path:
        resolved = path.resolve()
        if not any(resolved.is_relative_to(root) for root in self.roots):
            raise ValueError('file is outside the configured artifact roots')
        return resolved

    def label(self, path: Path) -> str:
        return str(path.relative_to(self.repo)) if path.is_relative_to(self.repo) else str(path)

    def artifact(self, path: Path, run_id: str | None = None) -> Artifact:
        path = self.permitted(path)
        if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ValueError('file is missing or its type is not available for preview')
        stat = path.stat()
        key = identity(str(path))
        extension = path.suffix.lower()
        kind = ('image' if extension in IMAGE_EXTENSIONS else 'video' if extension in VIDEO_EXTENSIONS
                else 'model' if extension in MODEL_EXTENSIONS else 'array' if extension == '.npz' else 'text')
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO artifacts VALUES (?, ?)', (key, str(path)))
            if run_id:
                db.execute('INSERT OR IGNORE INTO run_artifacts VALUES (?, ?)', (run_id, key))
        return Artifact(id=key, name=path.name, path=self.label(path), kind=kind, size=stat.st_size,
                        modified=timestamp(stat.st_mtime), privileged=path.name == 'privileged.npz')

    def artifact_path(self, artifact_id: str) -> Path:
        with self.connection() as db:
            row = db.execute('SELECT path FROM artifacts WHERE id=?', (artifact_id,)).fetchone()
        if not row:
            raise KeyError(artifact_id)
        # Recheck after registration: a file or its parent may have become a symlink.
        path = self.permitted(Path(row['path']))
        if not path.is_file():
            raise KeyError(artifact_id)
        return path

    def _sources(self, directory: Path) -> list[Path]:
        fixed = [directory / n for n in ('run_summary.json', 'stage_report.json', 'pipeline_validation.json')]
        fixed += [directory / 'reports/preflight.json']
        for stage in ('stage1', 'stage2', 'stage3', 'stage4'):
            fixed += [directory / stage / name for name in ('stage_report.json', 'progress.json')]
        fixed += [directory / 'stage1/scene_semantics.json',
                  directory / 'stage3/stage4_task/outputs/run_report.json']
        return [p for p in fixed if p.is_file()]

    def refresh(self, *, force: bool = False) -> int:
        with self.lock:
            if not self.runs_root.is_dir():
                return 0
            found = []
            for directory in sorted(self.runs_root.iterdir()):
                if not directory.is_dir() or directory.is_symlink() or directory.name.startswith('.'):
                    continue
                sources = self._sources(directory)
                if not sources:
                    continue
                run_id = identity(str(directory.resolve()))
                signature = identity('|'.join(f'{p}:{p.lstat().st_size}:{p.lstat().st_mtime_ns}' for p in sources))
                found.append(run_id)
                with self.connection() as db:
                    old = db.execute('SELECT fingerprint, project_id FROM runs WHERE id=?', (run_id,)).fetchone()
                if not force and old and old['fingerprint'] == signature:
                    continue
                detail = self._read_run(directory, run_id, old['project_id'] if old else 'imported')
                summary = RunSummary.model_validate(detail.model_dump())
                with self.connection() as db:
                    db.execute('''INSERT INTO runs VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET fingerprint=excluded.fingerprint, payload=excluded.payload''',
                               (run_id, str(directory.resolve()), summary.project_id, signature, summary.model_dump_json()))
                self._scanned_artifacts.discard(run_id)
            # Remove stale projections only. User-created projects remain intact.
            with self.connection() as db:
                for row in db.execute('SELECT id FROM runs').fetchall():
                    if row['id'] not in found:
                        db.execute('DELETE FROM runs WHERE id=?', (row['id'],))
            return len(found)

    def runs(self, *, query: str = '', validation: str | None = None,
             project_id: str | None = None) -> list[RunSummary]:
        with self.connection() as db:
            rows = db.execute('SELECT payload, project_id FROM runs').fetchall()
        values = [RunSummary.model_validate_json(row['payload']).model_copy(update={'project_id': row['project_id']})
                  for row in rows]
        return sorted((v for v in values if (not query or query.casefold() in v.name.casefold())
                       and (not validation or v.validation == validation)
                       and (not project_id or v.project_id == project_id)),
                      key=lambda v: v.updated_at, reverse=True)

    def run_path(self, run_id: str) -> tuple[Path, str]:
        with self.connection() as db:
            row = db.execute('SELECT path, project_id FROM runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        directory = self.permitted(Path(row['path']))
        if not directory.is_dir():
            raise KeyError(run_id)
        return directory, row['project_id']

    def run(self, run_id: str) -> RunDetail:
        directory, project = self.run_path(run_id)
        return self._read_run(directory, run_id, project)

    def _read_run(self, directory: Path, run_id: str, project: str) -> RunDetail:
        issues: list[Issue] = []
        register = lambda p: self.artifact(p, run_id)

        def read(path: Path):
            try:
                return read_object(self.permitted(path), issues)
            except (OSError, ValueError) as exc:
                issues.append(Issue(path=self.label(path), message=str(exc)))
                return None

        def report(path: Path):
            if not path.is_file():
                return None
            try:
                return parse_report(self.permitted(path), register)
            except (OSError, ValueError, TypeError) as exc:
                issues.append(Issue(path=self.label(path), message=f'invalid report contract: {exc}'))
                return None

        native = read(directory / 'run_summary.json') or {}
        aggregate = read(directory / 'pipeline_validation.json') or {}
        completion = aggregate.get('completion') or {}
        if not isinstance(completion, dict):
            completion = {}
            issues.append(Issue(path=str(directory / 'pipeline_validation.json'), message='invalid completion object'))
        preflight = report(directory / 'reports/preflight.json')
        stages = [Stage(id='preflight', title=STAGE_TITLES['preflight'], report=preflight)] if preflight else []
        for key in ('stage1', 'stage2', 'stage3', 'stage4'):
            current = report(directory / key / 'stage_report.json')
            progress = read(directory / key / 'progress.json')
            progress_path = directory / key / 'progress.json'
            attempt = (current.attempt_id or current.sha256) if current else None
            cache_key = str(progress_path)
            progress_stale = False
            with self.lock:
                cached = self._progress_cache.get(cache_key)
                if progress is not None and attempt:
                    self._progress_cache[cache_key] = attempt, progress
                elif attempt and progress_path.is_file() and cached and cached[0] == attempt:
                    progress = cached[1]
                    progress_stale = True
            if not current and not progress:
                continue
            history = []
            for path in sorted((directory / key / 'reports').glob('attempts/*/stage_report.json')):
                item = report(path)
                if item and (not current or item.sha256 != current.sha256):
                    history.append(item)
            stages.append(Stage(id=key, title=STAGE_TITLES[key], report=current, progress=progress,
                                progress_stale=progress_stale, history=history))
        backend = report(directory / 'stage_report.json')
        if backend:
            stages.append(Stage(id='backend', title=STAGE_TITLES['backend'], report=backend))
        stage4_path = directory / 'stage3/stage4_task/outputs/run_report.json'
        stage4 = read(stage4_path) or {}
        if stage4:
            stage = next((s for s in stages if s.id == 'stage4'), None)
            if stage is None:
                stage = Stage(id='stage4', title=STAGE_TITLES['stage4'])
                stages.append(stage)
            if stage.report is None:
                stage.report = report(stage4_path)
            # Embedded steps are invocation snapshots; read only their actual report files.
            for path in sorted((stage4_path.parent / 'reports').glob('*.json')):
                item = report(path)
                if item:
                    stage.steps.append(item)
            for path in sorted((stage4_path.parent / 'reports/history').rglob('*.json')):
                item = report(path)
                if item:
                    stage.history.append(item)
        semantics = read(directory / 'stage1/scene_semantics.json')
        input_info = aggregate.get('input') if isinstance(aggregate.get('input'), dict) else {}
        preflight_data = read(directory / 'reports/preflight.json') or {}
        preflight_inputs = preflight_data.get('inputs') if isinstance(preflight_data.get('inputs'), dict) else {}
        video = input_info.get('human_video') or preflight_inputs.get('video') or {}
        input_video = None
        if isinstance(video, dict) and isinstance(video.get('path'), str):
            try:
                input_video = register(Path(video['path']))
            except ValueError as exc:
                issues.append(Issue(path=video['path'], message=str(exc)))
        media = []
        for path in [*directory.glob('*.mp4'), *directory.glob('preview*.png'),
                     *directory.glob('stage4/*contact_sheet*.jpg'),
                     *directory.glob('stage3/stage4_task/outputs/samples/*/*.mp4')]:
            try:
                media.append(register(path))
            except ValueError as exc:
                issues.append(Issue(path=self.label(path), message=str(exc)))
        source_status = native.get('status') or (backend.source_status if backend else None)
        execution = {'running': 'unknown', 'failed': 'failed', 'partial': 'interrupted',
                     'blocked': 'blocked', 'cancelled': 'cancelled'}.get(source_status, 'unknown')
        if source_status in {'passed', 'warning', 'passed_with_warnings', 'scope_completed',
                             'scope_completed_with_warnings', 'smoke_complete', 'release_review_pending'}:
            execution = 'completed'
        states = [s.report.validation if s.report else 'not_evaluated' for s in stages]
        states += [r.validation for s in stages for r in s.steps]
        if native.get('scope_complete') is True or native.get('ok') is True:
            for key in native.get('requested_stages', []):
                stage = next((s for s in stages if s.id == key), None)
                if not stage or not stage.report or stage.report.schema_id != 'dexagent.step_report.v1':
                    issues.append(Issue(path=str(directory / str(key) / 'stage_report.json'),
                                        message='completed scope is missing its native stage acceptance report'))
        evidence_complete = completion.get('evidence_complete_across_sessions')
        if evidence_complete is True:
            # An aggregate is a snapshot. Do not carry its approval onto replaced reports.
            bindings = aggregate.get('stages', {})
            verified = aggregate.get('schema') == 'dexagent.pipeline_validation.v1' and isinstance(bindings, dict)
            for key in ('stage1', 'stage2', 'stage3', 'stage4'):
                bound = bindings.get(key, {}) if isinstance(bindings, dict) else {}
                stage = next((s for s in stages if s.id == key), None)
                if not isinstance(bound, dict) or stage is None or stage.report is None:
                    verified = False
                    continue
                target = stage4_path if key == 'stage4' else directory / key / 'stage_report.json'
                if bound.get('report') != str(target) or bound.get('status') != (stage4.get('status') if key == 'stage4' else stage.report.source_status):
                    verified = False
                if key != 'stage4':
                    current = read(target) or {}
                    if not bound.get('attempt_id') or bound.get('attempt_id') != current.get('attempt_id') or bound.get('checks') != current.get('checks'):
                        verified = False
                else:
                    step_bindings = bound.get('steps')
                    if not isinstance(step_bindings, dict) or not stage.steps:
                        verified = False
                        continue
                    for step in stage.steps:
                        saved = step_bindings.get(Path(step.artifact.name).stem, {})
                        current = read(self.artifact_path(step.artifact.id)) or {}
                        # artifact_sha256 names the producer's output digest, not the report's file hash.
                        if (not isinstance(saved, dict) or not saved.get('artifact_sha256')
                                or saved.get('artifact_sha256') != current.get('artifact_sha256')
                                or saved.get('attempt_id') != current.get('attempt_id')
                                or saved.get('checks') != current.get('checks')):
                            verified = False
            if not verified:
                evidence_complete = None
                issues.append(Issue(path=str(directory / 'pipeline_validation.json'),
                                    message='aggregate report bindings are missing or no longer match current stage reports'))
        updated = max((p.stat().st_mtime for p in self._sources(directory)), default=directory.stat().st_mtime)
        scope = Scope(requested_stages=native.get('requested_stages', []),
                      scope_complete=native.get('scope_complete'),
                      single_invocation_full_pipeline_complete=native.get('full_pipeline_complete'),
                      evidence_complete_across_invocations=evidence_complete,
                      explanation=completion.get('explanation'))
        reports = []
        for path in (directory / 'run_summary.json', directory / 'pipeline_validation.json', stage4_path):
            if path.is_file():
                try:
                    reports.append(register(path))
                except (OSError, ValueError) as exc:
                    issues.append(Issue(path=self.label(path), message=str(exc)))
        release = 'ready' if stage4.get('release_ready') is True else 'not_evaluated'
        return RunDetail(id=run_id, name=directory.name, project_id=project,
                         source='robosimstudio' if backend else 'dexagent', profile=native.get('profile') or (read(directory / 'stage_report.json') or {}).get('profile'),
                         source_status=source_status, execution=execution,
                         validation=merge_validation(states, has_errors=bool(issues)), release=release, scope=scope,
                         updated_at=timestamp(updated), stage_count=len(stages), issue_count=len(issues),
                         stages=stages, reports=reports, issues=issues, semantics=semantics,
                         task=input_info.get('task') or (semantics or {}).get('task_success'),
                         input_video=input_video, sessions=aggregate.get('sessions', []), media=media,
                         recorded_3d_available=preview_kind(directory) is not None)

    def artifacts(self, run_id: str) -> list[Artifact]:
        directory, _ = self.run_path(run_id)
        with self.lock:
            if run_id not in self._scanned_artifacts:
                for parent, dirs, files in os.walk(directory, followlinks=False):
                    dirs[:] = [d for d in dirs if not d.startswith('.') and d not in {'__pycache__'}
                               and not (Path(parent) / d).is_symlink()]
                    for name in files:
                        path = Path(parent) / name
                        if path.suffix.lower() in ALLOWED_EXTENSIONS:
                            try:
                                self.artifact(path, run_id)
                            except (ValueError, OSError):
                                # Entries outside allowed roots are never served, including source symlinks.
                                continue
                self._scanned_artifacts.add(run_id)
        with self.connection() as db:
            rows = db.execute('SELECT a.path FROM artifacts a JOIN run_artifacts r ON a.id=r.artifact_id WHERE r.run_id=?',
                              (run_id,)).fetchall()
        values = []
        for row in rows:
            try:
                values.append(self.artifact(Path(row['path'])))
            except (ValueError, OSError):
                continue
        return sorted(values, key=lambda v: v.path)

    def episodes(self, run_id: str) -> list[Episode]:
        directory, _ = self.run_path(run_id)
        episodes = []
        episode_root = directory / 'episodes'
        if episode_root.is_symlink():
            return episodes
        for path in sorted(episode_root.glob('*/meta.json')):
            # Never follow source symlinks into an unconfigured artifact root.
            if path.is_symlink() or path.parent.is_symlink():
                continue
            issues: list[Issue] = []
            data = read_object(self.permitted(path), issues) or {}
            episode_id = identity(str(path.resolve()))
            steps = path.parent / 'steps.npz'
            step_artifact = None
            if steps.is_file():
                try:
                    step_artifact = self.artifact(steps, run_id)
                except (OSError, ValueError) as exc:
                    issues.append(Issue(path=self.label(steps), message=f'cannot access episode steps: {exc}'))
            videos = []
            for video in sorted((path.parent / 'cameras').glob('*.mp4')):
                if video.is_symlink():
                    continue
                try:
                    videos.append(self.artifact(video, run_id))
                except (OSError, ValueError) as exc:
                    issues.append(Issue(path=self.label(video), message=f'cannot access episode video: {exc}'))
            files = dict(id=episode_id, run_id=run_id, name=path.parent.name,
                meta=self.artifact(path, run_id), steps=step_artifact, videos=videos,
                missing_fields=[key for key in ('task', 'embodiment') if not data.get(key)]
                               + ([] if (path.parent.parent / 'run_meta.json').is_file() else ['run_meta']))
            try:
                episode = Episode.model_validate(dict(files,
                    task=data.get('task') or None, embodiment=data.get('embodiment') or None,
                    success=data.get('success'), sim_s=data.get('sim_s'),
                    recorded_steps=data.get('recorded_steps'), issues=issues), strict=True)
            except ValueError as exc:
                # Preserve the native files for diagnosis, without certifying invalid metadata.
                issues.append(Issue(path=self.label(path), message=f'invalid episode metadata: {exc}'))
                episode = Episode(**files, issues=issues)
            episodes.append(episode)
        return episodes

    def episode_catalog(self, *, query: str = '', project_id: str | None = None,
                        run_id: str | None = None, success: str | None = None,
                        metadata: str | None = None) -> list[EpisodeCatalogItem]:
        """Build a cross-run view from native EpisodeStore metadata without changing it."""
        projects = {project.id: project.name for project in self.projects()}
        runs = self.runs(project_id=project_id)
        if run_id:
            runs = [run for run in runs if run.id == run_id]
        needle = query.casefold().strip()
        values: list[EpisodeCatalogItem] = []
        for run in runs:
            try:
                episodes = self.episodes(run.id)
            except (KeyError, OSError, ValueError):
                # A source directory may disappear between refresh and this read.
                continue
            for episode in episodes:
                if success == 'passed' and episode.success is not True:
                    continue
                if success == 'failed' and episode.success is not False:
                    continue
                if success == 'unknown' and episode.success is not None:
                    continue
                has_metadata = not episode.missing_fields and not episode.issues
                if metadata == 'complete' and not has_metadata:
                    continue
                if metadata == 'incomplete' and has_metadata:
                    continue
                if needle and not needle in ' '.join(filter(None, (
                    episode.name, episode.task, episode.embodiment, run.name
                ))).casefold():
                    continue
                values.append(EpisodeCatalogItem(
                    **episode.model_dump(), run_name=run.name, project_id=run.project_id,
                    project_name=projects.get(run.project_id, '未知项目'),
                ))
        return values

    def projects(self) -> list[Project]:
        with self.connection() as db:
            rows = db.execute('''SELECT p.*, COUNT(r.id) run_count FROM projects p
                LEFT JOIN runs r ON p.id=r.project_id GROUP BY p.id ORDER BY p.created_at''').fetchall()
        return [Project(**dict(row)) for row in rows]

    def create_project(self, name: str) -> Project:
        name = name.strip()
        if not name:
            raise ValueError('project name cannot be blank')
        project = Project(id=uuid.uuid4().hex, name=name, created_at=datetime.now(timezone.utc).isoformat())
        with self.connection() as db:
            db.execute('INSERT INTO projects VALUES (?, ?, ?)', (project.id, name, project.created_at))
        return project

    def import_run(self, directory: str, project_id: str) -> RunDetail:
        selected = (self.runs_root / directory).resolve()
        if selected.parent != self.runs_root or not selected.is_dir():
            raise ValueError('select an immediate run directory inside the configured runs root')
        self.refresh()
        run_id = identity(str(selected))
        with self.connection() as db:
            if not db.execute('SELECT id FROM projects WHERE id=?', (project_id,)).fetchone():
                raise KeyError(project_id)
            if not db.execute('SELECT id FROM runs WHERE id=?', (run_id,)).fetchone():
                raise ValueError('directory contains no supported run reports')
            db.execute('UPDATE runs SET project_id=? WHERE id=?', (project_id, run_id))
        return self.run(run_id)
