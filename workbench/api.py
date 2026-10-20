"""Local visualization API; this module never imports a simulation engine."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from .index import Index, TEXT_EXTENSIONS
from .models import (Artifact, ArtifactPage, Capability, CleanupCatalog, CleanupCreate, CleanupJob, EpisodeCatalogPage, EpisodePage, Health, ImportRequest,
                     LogChunk, Preview, PreviewRequest, Project, ProjectCreate, RunDetail, RunPage, Series, Tool, ToolPage,
                     ScenePage, SceneRevisionPage, EditorSession, EditorCreate, StudioLiveStatus)
from .editors import Editors, EditorBusy
from .previews import PreviewBusy, Previews
from .reports import read_object
from .rss_live import RssLiveInspector
from .background_cleanups import BackgroundCleanups, CleanupBusy, CleanupUnavailable
from .executions import Executions, ExecutionBusy
from .models import CandidateScene, Execution, ExecutionCatalog, ExecutionCreate, ExecutionLog

PREFIX = '/api/workbench/v1'
LOGGER = logging.getLogger(__name__)


def create_app(repo: Path | None = None, *, runs_root: Path | None = None,
               state_dir: Path | None = None, artifact_roots: tuple[Path, ...] = (),
               scene_roots: tuple[Path, ...] = (), editor_python: Path | None = None,
               robot_assets: Path | None = None,
               rss_run_dir: str | Path | None = None,
               cleanup_video_roots: tuple[Path, ...] = (),
               frontend_dist: Path | None = None,
               refresh_interval: float = 5.0) -> FastAPI:
    repo = (repo or Path(__file__).resolve().parents[1]).resolve()
    index = Index(repo, runs_root or repo / 'runs', state_dir or repo / '.workbench', artifact_roots)
    previews = Previews(index, state_dir or repo / '.workbench')
    editors = Editors(repo, state_dir or repo / '.workbench', scene_roots=scene_roots,
                      python=editor_python, robot_assets=robot_assets)
    rss_live = RssLiveInspector(repo, rss_run_dir)
    cleanups = BackgroundCleanups(repo, state_dir or repo / '.workbench', source_roots=cleanup_video_roots)
    executions = Executions(index, state_dir or repo / '.workbench', input_roots=cleanup_video_roots)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import httpx
        proxy = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=2), trust_env=False)
        app.state.editor_http = proxy
        app.state.index_error = None

        async def refresh(*, force=False):
            try:
                await asyncio.to_thread(index.refresh, force=force)
                app.state.index_error = None
            except Exception as exc:
                app.state.index_error = f'{type(exc).__name__}: {exc}'
                LOGGER.exception('Workbench index refresh failed')

        async def watch():
            while True:
                await asyncio.sleep(refresh_interval)
                await refresh()
                await asyncio.to_thread(previews.poll)
                await asyncio.to_thread(editors.poll)
                await asyncio.to_thread(cleanups.poll)
                await asyncio.to_thread(executions.poll)

        await refresh(force=True)
        watcher = asyncio.create_task(watch()) if refresh_interval > 0 else None
        try:
            yield
        finally:
            if watcher:
                watcher.cancel()
                try:
                    await watcher
                except asyncio.CancelledError:
                    pass
            await asyncio.to_thread(previews.close)
            await asyncio.to_thread(editors.close)
            await asyncio.to_thread(cleanups.close)
            await asyncio.to_thread(executions.close)
            await proxy.aclose()

    app = FastAPI(title='EmbodiForge Studio', version='0.1.0', lifespan=lifespan)
    app.state.index = index
    app.state.previews = previews
    app.state.editors = editors
    app.state.rss_live = rss_live
    app.state.background_cleanups = cleanups
    app.state.executions = executions
    app.state.index_error = None

    @app.exception_handler(KeyError)
    async def missing(request: Request, exc: KeyError):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={'detail': 'record or source file no longer exists'})

    @app.exception_handler(ValueError)
    async def invalid(request: Request, exc: ValueError):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422, content={'detail': str(exc)})

    @app.get(f'{PREFIX}/health', response_model=Health)
    def health():
        return Health(status='degraded' if app.state.index_error else 'ok',
                      index_error=app.state.index_error, run_count=len(index.runs()))

    @app.get(f'{PREFIX}/capabilities', response_model=list[Capability])
    def capabilities():
        from importlib.util import find_spec
        replay_environment = all(find_spec(name) is not None for name in
                                 ('numpy', 'mujoco', 'newton', 'viser', 'robosimstudio_studio'))
        return [
            Capability(id='reports', title='运行与验收', available=True, level='implemented',
                       reason='读取本机原生报告；不改变 pipeline 的执行和验收结果。'),
            Capability(id='media', title='视频与产物', available=True, level='implemented',
                       reason='按文件 ID 预览现有图片、视频、日志和报告。'),
            Capability(id='background_cleanup', title='背景 mask 与视频补洞', available=True, level='integrated',
                       reason='显式启动 SimFoundry Stage 1b、SAM3/SAM2 mask 和 VOID 两遍补洞；具体环境与权重状态见背景清理页预检。'),
            Capability(id='episodes', title='原生 Episode', available=True, level='implemented',
                       reason='兼容历史元信息缺失；未接入训练格式转换。'),
            Capability(id='recorded_3d', title='原生 3D 回放', available=replay_environment,
                       level='environment_detected' if replay_environment else 'missing_dependencies',
                       reason='对支持的 RSS / DexAgent 刚体记录生成交互式 Viser 回放；逐次核对场景与记录身份，不执行物理。'
                       if replay_environment else '需要已有 RSS/Newton、Viser、MuJoCo 环境；普通报告与视频浏览不受影响。'),
            Capability(id='studio_feedback', title='RSS 实时状态反馈', available=rss_live.configured,
                       level='configured' if rss_live.configured else 'not_configured',
                       reason='只读连接 RoboSimStudio 状态 socket；环境、步数、时间和实体位姿可视化，不发控制命令。'
                       if rss_live.configured else '设置 WORKBENCH_RSS_RUN_DIR 指向 RSS live 页面的 run 目录后启用。'),
            Capability(id='studio', title='实时仿真控制', available=False, level='not_integrated',
                       reason='任务执行页已接入任务与 MJCF 候选的离线 RSS 验收；实时交互控制命令尚未接入。'),
            Capability(id='scene_editor', title='原生场景布局编辑', available=editors.available,
                       level='environment_detected' if editors.available else 'missing_dependencies',
                       reason='使用独立 Light Editor 进程编辑场景副本并保存候选；逐次启动核对模型和代理，物理验收另行执行。'
                       if editors.available else '需要独立编辑器解释器；见工作台安装说明。'),
            Capability(id='execution', title='任务执行', available=executions.catalog().available, level='integrated',
                       reason='复用既有 DexAgent/RSS CLI 启动、停止和创建新运行重试；运行环境与验收结果由原生报告判断。'),
            Capability(id='training_export', title='训练格式导出', available=False, level='not_verified',
                       reason='原生 episode 记录不等于 LeRobot/RLDS 格式已验收。'),
        ]

    @app.get(f'{PREFIX}/studio/live', response_model=StudioLiveStatus)
    def studio_live():
        """Read the configured RSS live session status without controlling it."""
        return StudioLiveStatus(**rss_live.read())

    @app.get(f'{PREFIX}/executions/catalog', response_model=ExecutionCatalog)
    def execution_catalog():
        return executions.catalog()

    @app.get(f'{PREFIX}/executions/scenes/{{run_id}}', response_model=CandidateScene)
    def execution_scene(run_id: str, config_id: str = Query(pattern='^[a-f0-9]{24}$')):
        return executions.inspect_scene(run_id, config_id)

    @app.get(f'{PREFIX}/executions', response_model=list[Execution])
    def execution_jobs():
        return executions.jobs()

    @app.post(f'{PREFIX}/executions', response_model=Execution, status_code=202)
    def start_execution(body: ExecutionCreate):
        try:
            return executions.start(body)
        except ExecutionBusy as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get(f'{PREFIX}/executions/{{key}}', response_model=Execution)
    def execution(key: str):
        return executions.state(key)

    @app.post(f'{PREFIX}/executions/{{key}}/stop', response_model=Execution)
    def stop_execution(key: str):
        return executions.stop(key)

    @app.post(f'{PREFIX}/executions/{{key}}/retry', response_model=Execution, status_code=202)
    def retry_execution(key: str):
        try:
            return executions.retry(key)
        except ExecutionBusy as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get(f'{PREFIX}/executions/{{key}}/log', response_model=ExecutionLog)
    def execution_log(key: str):
        return ExecutionLog(text=executions.log(key))

    @app.get(f'{PREFIX}/background-cleanups/catalog', response_model=CleanupCatalog)
    def background_cleanup_catalog():
        return cleanups.catalog()

    @app.get(f'{PREFIX}/background-cleanups/jobs', response_model=list[CleanupJob])
    def background_cleanup_jobs():
        cleanups.poll()
        return cleanups.jobs()

    @app.post(f'{PREFIX}/background-cleanups', response_model=CleanupJob, status_code=202)
    def create_background_cleanup(body: CleanupCreate):
        try:
            return cleanups.start(body)
        except CleanupBusy as exc:
            raise HTTPException(409, str(exc))
        except CleanupUnavailable as exc:
            raise HTTPException(503, str(exc))

    @app.get(f'{PREFIX}/background-cleanups/{{job_id}}', response_model=CleanupJob)
    def background_cleanup(job_id: str):
        cleanups.poll()
        return cleanups.job(job_id)

    @app.post(f'{PREFIX}/background-cleanups/{{job_id}}/approve-mask', response_model=CleanupJob)
    def approve_background_mask(job_id: str):
        try:
            return cleanups.approve_mask(job_id)
        except CleanupBusy as exc:
            raise HTTPException(409, str(exc))
        except CleanupUnavailable as exc:
            raise HTTPException(503, str(exc))

    @app.post(f'{PREFIX}/background-cleanups/{{job_id}}/build-scene', response_model=CleanupJob)
    def build_background_scene(job_id: str):
        try:
            return cleanups.build_scene(job_id)
        except CleanupBusy as exc:
            raise HTTPException(409, str(exc))
        except CleanupUnavailable as exc:
            raise HTTPException(503, str(exc))

    @app.post(f'{PREFIX}/background-cleanups/{{job_id}}/stop', response_model=CleanupJob)
    def stop_background_cleanup(job_id: str):
        return cleanups.stop(job_id)

    @app.get(f'{PREFIX}/background-cleanups/{{job_id}}/media/{{name}}', include_in_schema=False)
    def background_cleanup_media(job_id: str, name: str):
        path = cleanups.media(job_id, name)
        return FileResponse(path, media_type='video/mp4' if path.suffix.lower() == '.mp4' else None,
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get(f'{PREFIX}/background-cleanups/{{job_id}}/log', include_in_schema=False)
    def background_cleanup_log(job_id: str):
        path = cleanups.log_path(job_id)
        return FileResponse(path, media_type='text/plain; charset=utf-8', filename='background-cleanup.log',
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get(f'{PREFIX}/projects', response_model=list[Project])
    def projects():
        return index.projects()

    @app.post(f'{PREFIX}/projects', response_model=Project, status_code=201)
    def create_project(body: ProjectCreate):
        return index.create_project(body.name)

    @app.post(f'{PREFIX}/imports', response_model=RunDetail)
    def import_run(body: ImportRequest):
        return index.import_run(body.directory, body.project_id)

    @app.get(f'{PREFIX}/runs', response_model=RunPage)
    def runs(q: str = '', validation: str | None = None, project_id: str | None = None,
             offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        values = index.runs(query=q, validation=validation, project_id=project_id)
        return RunPage(items=values[offset:offset + limit], total=len(values))

    @app.get(f'{PREFIX}/runs/{{run_id}}', response_model=RunDetail)
    def run(run_id: str):
        return index.run(run_id)

    @app.post(f'{PREFIX}/refresh', response_model=Health)
    def refresh():
        count = index.refresh(force=True)
        app.state.index_error = None
        return Health(status='ok', run_count=count)

    @app.get(f'{PREFIX}/runs/{{run_id}}/artifacts', response_model=ArtifactPage)
    def run_artifacts(run_id: str, kind: str | None = None, q: str = '',
                      offset: int = Query(0, ge=0), limit: int = Query(60, ge=1, le=200)):
        values = [a for a in index.artifacts(run_id)
                  if (not kind or a.kind == kind) and (not q or q.casefold() in a.path.casefold())]
        return ArtifactPage(items=values[offset:offset + limit], total=len(values))

    @app.get(f'{PREFIX}/artifacts/{{artifact_id}}', response_model=Artifact)
    def artifact(artifact_id: str):
        return index.artifact(index.artifact_path(artifact_id))

    @app.get(f'{PREFIX}/artifacts/{{artifact_id}}/content')
    def content(artifact_id: str, download: bool = False):
        path = index.artifact_path(artifact_id)
        extension = path.suffix.lower()
        text = extension in TEXT_EXTENSIONS or extension in {'.xml', '.urdf', '.usda'}
        return FileResponse(path, media_type='text/plain; charset=utf-8' if text else None,
                            filename=path.name, content_disposition_type='attachment' if download else 'inline',
                            headers={'X-Content-Type-Options': 'nosniff'})

    @app.get(f'{PREFIX}/artifacts/{{artifact_id}}/text', response_model=LogChunk)
    def text(artifact_id: str, cursor: int = Query(0, ge=-1), identity: str | None = None,
             limit: int = Query(65536, ge=1, le=262144)):
        path = index.artifact_path(artifact_id)
        if path.suffix.lower() not in TEXT_EXTENSIONS | {'.xml', '.urdf', '.usda'}:
            raise HTTPException(415, 'this artifact is not text')
        with path.open('rb') as stream:
            stat = path.stat()
            current_identity = f'{stat.st_dev}:{stat.st_ino}'
            reset = cursor > stat.st_size or identity is not None and identity != current_identity
            start = max(0, stat.st_size - limit) if cursor == -1 else 0 if reset else cursor
            stream.seek(start)
            value = stream.read(limit)
            end = stream.tell()
        return LogChunk(text=value.decode('utf-8', errors='replace'), cursor=end,
                        identity=current_identity, reset=reset, eof=end >= stat.st_size)

    @app.get(f'{PREFIX}/artifacts/{{artifact_id}}/series', response_model=Series)
    def series(artifact_id: str, field: Literal['joint_q', 'joint_qd', 'action', 'reward', 'qpos', 'qvel', 'ctrl'] | None = None,
               channel: int = Query(0, ge=0), max_points: int = Query(1000, ge=2, le=2000)):
        from zipfile import BadZipFile, ZipFile
        try:
            import numpy as np
        except ImportError:
            raise HTTPException(503, 'install numpy from requirements-workbench.txt to read episode arrays')
        path = index.artifact_path(artifact_id)
        if path.name not in {'steps.npz', 'trajectory.npz'}:
            raise HTTPException(415, 'series are available only for native steps.npz or trajectory.npz')
        if path.name == 'steps.npz':
            fields, clock = ('joint_q', 'joint_qd', 'action', 'reward'), 'sim_time'
        else:
            fields, clock = ('qpos', 'qvel', 'ctrl'), 'time_s'
        try:
            with ZipFile(path) as archive:
                if sum(info.file_size for info in archive.infolist()) > 128 * 1024 * 1024:
                    raise HTTPException(413, 'recording arrays exceed the 128 MiB interactive preview limit')
            with np.load(path, allow_pickle=False) as data:
                available = [key for key in fields if key in data]
                field = field or next(iter(available), None)
                if field not in available or clock not in data:
                    raise HTTPException(422, 'the requested channel or simulation clock is missing')
                times = data[clock]
                values = data[field]
                if times.ndim != 1 or values.ndim not in (1, 2) or len(times) != len(values) or not len(times):
                    raise HTTPException(422, 'recording clock and channel shapes do not match')
                if not np.isfinite(times).all() or not np.all(np.diff(times) > 0) or not np.isfinite(values).all():
                    raise HTTPException(422, 'recording contains invalid values or a non-increasing simulation clock')
                channels = values.shape[1] if values.ndim == 2 else 1
                if channel >= channels:
                    raise HTTPException(422, 'channel index is out of range')
                indices = np.unique(np.linspace(0, len(times) - 1, min(max_points, len(times)), dtype=int))
                selected = values[indices, channel] if values.ndim == 2 else values[indices]
                channel_name = None
                names_key = {'qpos': 'joint_names', 'ctrl': 'actuator_names'}.get(field)
                if names_key and names_key in data:
                    names = data[names_key]
                    if names.shape == (channels,) and names.dtype.kind in {'U', 'S'}:
                        channel_name = names[channel].decode('utf-8') if names.dtype.kind == 'S' else str(names[channel])
                return Series(field=field, channel=channel, channels=channels, available_fields=available,
                              source_points=len(times), times=times[indices].astype(float).tolist(),
                              values=selected.astype(float).tolist(), channel_name=channel_name,
                              value_unit='原生记录单位；不同关节/控制通道可能使用不同单位')
        except (BadZipFile, OSError, ValueError, TypeError) as exc:
            raise HTTPException(422, f'cannot read native recording: {exc}')

    @app.post(f'{PREFIX}/runs/{{run_id}}/preview', response_model=Preview)
    def create_preview(run_id: str, body: PreviewRequest):
        try:
            return previews.start(run_id, body.episode_id)
        except PreviewBusy as exc:
            raise HTTPException(409, str(exc))

    @app.get(f'{PREFIX}/previews/{{preview_id}}', response_model=Preview)
    def preview_state(preview_id: str):
        return previews.state(preview_id)

    @app.get(f'{PREFIX}/previews/{{preview_id}}/viewer', include_in_schema=False)
    def preview_viewer(preview_id: str):
        state = previews.state(preview_id)
        if state.status != 'ready':
            raise HTTPException(409, state.message)
        return FileResponse(previews.directory(preview_id) / 'viewer.html', media_type='text/html',
                            headers={'X-Content-Type-Options': 'nosniff'})

    @app.get(f'{PREFIX}/runs/{{run_id}}/episodes', response_model=EpisodePage)
    def episodes(run_id: str):
        values = index.episodes(run_id)
        directory, _ = index.run_path(run_id)
        trajectory = directory / 'stage3/stage4_task/trajectory.npz'
        return EpisodePage(items=values, total=len(values),
                           recorded_trajectory=index.artifact(trajectory, run_id) if trajectory.is_file() else None)

    @app.get(f'{PREFIX}/episode-catalog', response_model=EpisodeCatalogPage)
    def episode_catalog(q: str = '', project_id: str | None = None, run_id: str | None = None,
                        success: Literal['passed', 'failed', 'unknown'] | None = None,
                        metadata: Literal['complete', 'incomplete'] | None = None,
                        offset: int = Query(0, ge=0), limit: int = Query(24, ge=1, le=100)):
        values = index.episode_catalog(query=q, project_id=project_id, run_id=run_id,
                                       success=success, metadata=metadata)
        return EpisodeCatalogPage(items=values[offset:offset + limit], total=len(values))

    @app.get(f'{PREFIX}/runs/{{run_id}}/logs', response_model=ArtifactPage)
    def logs(run_id: str):
        directory, _ = index.run_path(run_id)
        paths = [*directory.glob('*.log'), *directory.glob('stage*/trace.jsonl')]
        values = [index.artifact(p, run_id) for p in sorted(paths)]
        return ArtifactPage(items=values, total=len(values))

    @app.get(f'{PREFIX}/tools', response_model=ToolPage)
    def tools(q: str = '', tool_type: str | None = None, category: str | None = None,
              offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        issues = []
        document = read_object(repo / 'tool_library/index.json', issues)
        if issues or document is None or not isinstance(document.get('tools'), list):
            raise HTTPException(503, 'local tool index is unavailable or malformed')
        values = []
        for item in document['tools']:
            if not isinstance(item, dict) or not item.get('name') or not item.get('type'):
                raise HTTPException(503, 'local tool index contains an invalid entry')
            if tool_type and item['type'] != tool_type or category and item.get('category') != category:
                continue
            if q and q.casefold() not in json_search(item):
                continue
            values.append(Tool.model_validate(item))
        return ToolPage(items=values[offset:offset + limit], total=len(values))

    @app.get(f'{PREFIX}/scenes', response_model=ScenePage)
    def scene_files():
        values = editors.scenes()
        return ScenePage(items=values, total=len(values))

    @app.get(f'{PREFIX}/scenes/{{scene_id}}/revisions', response_model=SceneRevisionPage)
    def scene_revisions(scene_id: str, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        values = editors.scene_revisions(scene_id)
        return SceneRevisionPage(items=values[offset:offset + limit], total=len(values))

    @app.get(f'{PREFIX}/editors', response_model=list[EditorSession])
    def editor_sessions():
        directories = sorted(editors.root.glob('*/job.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        return [editors.session(path.parent.name) for path in directories[:20]]

    @app.post(f'{PREFIX}/editors', response_model=EditorSession, status_code=201)
    def create_editor(body: EditorCreate):
        try:
            return editors.start(body.scene_id, body.revision)
        except EditorBusy as exc:
            raise HTTPException(409, str(exc))

    @app.get(f'{PREFIX}/editors/{{editor_id}}', response_model=EditorSession)
    def editor_session(editor_id: str):
        return editors.session(editor_id)

    @app.post(f'{PREFIX}/editors/{{editor_id}}/stop', response_model=EditorSession)
    def stop_editor(editor_id: str):
        return editors.stop(editor_id)

    @app.get(f'{PREFIX}/editors/{{editor_id}}/log', include_in_schema=False)
    def editor_log(editor_id: str):
        path = editors.directory(editor_id) / 'server.log'
        if not path.is_file():
            raise KeyError(editor_id)
        return FileResponse(path, media_type='text/plain', filename='editor.log')

    @app.get(f'{PREFIX}/editors/{{editor_id}}/revisions/{{revision_id}}', include_in_schema=False)
    def editor_revision(editor_id: str, revision_id: str):
        path = editors.revision_path(editor_id, revision_id)
        return FileResponse(path, media_type='application/json', filename=path.name)

    @app.api_route(f'{PREFIX}/editors/{{editor_id}}/view/{{path:path}}', methods=['GET', 'HEAD', 'POST'], include_in_schema=False)
    async def editor_proxy(editor_id: str, path: str, request: Request):
        import httpx
        from urllib.parse import urlsplit
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403, 'cross-origin editor request rejected')
        try:
            endpoint = await asyncio.to_thread(editors.endpoint, editor_id)
        except EditorBusy as exc:
            raise HTTPException(409, str(exc))
        chunks = []
        length = 0
        async for chunk in request.stream():
            length += len(chunk)
            if length > 32 * 1024 * 1024:
                raise HTTPException(413, 'editor request exceeds 32 MiB')
            chunks.append(chunk)
        content = b''.join(chunks)
        # Validate the browser origin before translating it to the native loopback origin.
        # The native mutation token remains required; no cookies or arbitrary destination pass through.
        headers = {name: value for name, value in request.headers.items()
                   if name.lower() in {'content-type', 'x-editor-token', 'range'}}
        if origin:
            headers['origin'] = endpoint
        url = endpoint + str(request.url.path)
        if request.url.query:
            url += '?' + request.url.query
        client = app.state.editor_http
        try:
            response = await client.send(client.build_request(request.method, url, headers=headers, content=content), stream=True)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f'native editor connection failed: {type(exc).__name__}')
        forwarded = {name: value for name, value in response.headers.items() if name.lower() in {
            'content-type', 'content-length', 'content-encoding', 'content-disposition', 'cache-control',
            'x-content-type-options', 'x-frame-options', 'content-security-policy', 'accept-ranges', 'content-range'}}
        return StreamingResponse(response.aiter_raw(), status_code=response.status_code,
                                 headers=forwarded, background=BackgroundTask(response.aclose))

    dist = (frontend_dist or repo / 'workbench/frontend/dist').resolve()

    @app.get('/{path:path}', include_in_schema=False)
    def frontend(path: str):
        target = (dist / path).resolve()
        if target.is_relative_to(dist.resolve()) and target.is_file():
            return FileResponse(target)
        if path.startswith('api/') or '.' in Path(path).name:
            raise HTTPException(404, 'not found')
        if not (dist / 'index.html').is_file():
            raise HTTPException(503, 'frontend is not built; run npm ci and npm run build in workbench/frontend')
        return FileResponse(dist / 'index.html')

    return app


def json_search(value: dict) -> str:
    import json
    return json.dumps(value, ensure_ascii=False).casefold()
