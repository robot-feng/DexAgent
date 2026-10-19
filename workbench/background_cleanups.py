"""Supervise SimFoundry's optional video background-cleanup workflow."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

from .index import identity
from .models import CleanupCatalog, CleanupCreate, CleanupJob, CleanupRequirement, CleanupVideo

VIDEO_SUFFIXES = {'.mp4', '.mov', '.mkv', '.webm'}
ACTIVE = {'preparing', 'inpainting', 'building'}


def _gemini_capability() -> tuple[bool, str]:
    """Check configured Gemini auth without treating a Vertex project as credentials."""
    for key in ('GEMINI_API_KEY', 'GOOGLE_API_KEY'):
        if os.environ.get(key, '').strip():
            return True, '检测到 Gemini API key；模型配额和权限仍需运行时验证。'

    project = (os.environ.get('GCLOUD_PROJECT') or os.environ.get('GOOGLE_CLOUD_PROJECT') or '').strip()
    if not project and shutil.which('gcloud'):
        try:
            result = subprocess.run(['gcloud', 'config', 'get-value', 'project'],
                                    capture_output=True, text=True, timeout=3, check=True)
            project = result.stdout.strip()
            if project == '(unset)':
                project = ''
        except (OSError, subprocess.SubprocessError):
            pass

    credential_paths = []
    application_credentials = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if application_credentials:
        credential_paths.append(Path(application_credentials).expanduser())
    cloudsdk_config = os.environ.get('CLOUDSDK_CONFIG')
    if cloudsdk_config:
        credential_paths.append(Path(cloudsdk_config).expanduser() / 'application_default_credentials.json')
    config_home = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')).expanduser()
    credential_paths.append(config_home / 'gcloud/application_default_credentials.json')
    has_adc_file = any(_is_adc_credential_file(path) for path in credential_paths)

    if project and has_adc_file:
        return True, '检测到 Vertex 项目和 ADC 凭据文件；模型权限仍需运行时验证。'
    if not project:
        return False, '缺少 Gemini API key；Vertex AI 还需要项目 ID 和 ADC 凭据。'
    return False, '检测到 Vertex 项目，但未找到 ADC 凭据文件；请配置 GOOGLE_APPLICATION_CREDENTIALS 或 gcloud ADC。'


def _is_adc_credential_file(path: Path) -> bool:
    """Recognize common ADC JSON formats without exposing credential contents."""
    formats = {
        'service_account': ('client_email', 'private_key', 'token_uri'),
        'authorized_user': ('client_id', 'client_secret', 'refresh_token'),
        'external_account': ('audience', 'subject_token_type', 'token_url', 'credential_source'),
        'external_account_authorized_user': ('audience', 'subject_token_type', 'token_url', 'refresh_token'),
        'impersonated_service_account': ('service_account_impersonation_url', 'source_credentials'),
    }
    try:
        credentials = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(credentials, dict):
        return False
    required = formats.get(credentials.get('type'))
    return bool(required and all(credentials.get(key) for key in required))


class CleanupBusy(RuntimeError):
    pass


class CleanupUnavailable(RuntimeError):
    pass


class BackgroundCleanups:
    """Run the native SimFoundry masks, VOID and auto-background stages in isolation."""

    def __init__(self, repo: Path, state_dir: Path, *, source_roots=(), timeout_s: float = 48 * 3600):
        self.repo = repo.resolve()
        self.simfoundry = self.repo / 'SimFoundry'
        self.state_dir = state_dir.resolve()
        self.root = self.state_dir / 'background_cleanups'
        self.jobs_root = self.root / 'jobs'
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.example_root = self.simfoundry / 'docs/assets/example_videos'
        self.source_roots = tuple(dict.fromkeys(p.resolve() for p in (
            self.example_root, self.simfoundry / 'Data/Scene_Video', *source_roots)))
        self.timeout_s = timeout_s
        self.lock = threading.RLock()
        self.processes: dict[str, tuple[subprocess.Popen, float]] = {}

    def _conda(self) -> Path | None:
        configured = os.environ.get('CONDA_EXE')
        found = (shutil.which('mamba') or shutil.which('conda') or
                 (configured if configured and Path(configured).is_file() else None))
        return Path(found).resolve() if found else None

    def _env_dirs(self) -> list[Path]:
        values = []
        conda = self._conda()
        if conda:
            base = conda.parent.parent if conda.parent.name in {'bin', 'condabin'} else conda.parent
            values.append(base / 'envs')
        prefix = os.environ.get('CONDA_PREFIX')
        if prefix:
            path = Path(prefix).resolve()
            values.append(path.parent if path.parent.name == 'envs' else path / 'envs')
        return [p for p in dict.fromkeys(values) if p.is_dir()]

    def _has_env(self, name: str) -> bool:
        return any((root / name / 'conda-meta').is_dir() for root in self._env_dirs())

    def _requirements(self) -> list[CleanupRequirement]:
        auto_bg = self.simfoundry / 'scripts/pipeline/A_reconstruction/stages/auto_bg_reconstruction'
        void_root = self.simfoundry / 'deps/void-model'
        sam2_paths = [
            self.simfoundry / 'deps/Any6D/sam2/checkpoints/sam2.1_hiera_large.pt',
            self.simfoundry / 'checkpoints/sam2.1_hiera_large.pt',
        ]
        has_sam2 = any(p.is_file() for p in sam2_paths)
        has_void = ((void_root / 'inference/cogvideox_fun/inference_with_pass1_warped_noise.py').is_file()
                    and (void_root / 'void_pass1.safetensors').is_file()
                    and (void_root / 'void_pass2.safetensors').is_file())
        has_gemini_auth, gemini_detail = _gemini_capability()
        values = [
            CleanupRequirement(id='video_tools', title='视频工具', available=bool(shutil.which('ffmpeg')),
                               detail='需要 ffmpeg 处理输入和预览视频。' if not shutil.which('ffmpeg') else 'ffmpeg 可用。'),
            CleanupRequirement(id='conda', title='Conda/Mamba', available=self._conda() is not None,
                               detail='需要 conda 或 mamba 启动 SimFoundry 环境。' if not self._conda() else f'检测到 {self._conda()}。'),
            CleanupRequirement(id='simfoundry_env', title='SimFoundry 环境', available=self._has_env('simfoundry'),
                               detail='需要 simfoundry 环境运行抽帧、SAM3 和场景流程。' if not self._has_env('simfoundry') else '环境已安装。'),
            CleanupRequirement(id='sam2_checkpoint', title='SAM2 视频传播权重', available=has_sam2,
                               detail='缺少 sam2.1_hiera_large.pt；按 SimFoundry 默认路径检查。' if not has_sam2 else 'SAM2 权重已找到。'),
            CleanupRequirement(id='void_env', title='VOID 环境与权重', available=self._has_env('void') and has_void,
                               detail='需要 void 环境、VOID 源码和 pass1/pass2 权重。' if not (self._has_env('void') and has_void) else 'VOID 环境与权重已找到。'),
            CleanupRequirement(id='gemini', title='上游 Gemini 后端（兼容项）', available=has_gemini_auth,
                               detail=(f'当前全量 A 仍有对象图像编辑、模型分类等 Gemini 调用。{gemini_detail}'
                                       if not has_gemini_auth else gemini_detail)),
            CleanupRequirement(id='da3_env', title='DA3 环境', available=self._has_env('da3'),
                               detail='完整 3D 场景生成需要 da3 环境。' if not self._has_env('da3') else '环境已安装。'),
            CleanupRequirement(id='nerfstudio_env', title='Nerfstudio 环境', available=self._has_env('nerfstudio_simfoundry'),
                               detail='完整背景高斯训练需要 nerfstudio_simfoundry 环境。' if not self._has_env('nerfstudio_simfoundry') else '环境已安装。'),
            CleanupRequirement(id='3dgrut_env', title='3DGRUT 环境', available=self._has_env('3dgrut'),
                               detail='完整场景资产生成需要 3dgrut 环境。' if not self._has_env('3dgrut') else '环境已安装。'),
            CleanupRequirement(id='mesh_env', title='网格生成环境', available=self._has_env('hunyuan'),
                               detail='完整 A 重建需要 hunyuan 环境。' if not self._has_env('hunyuan') else '环境已安装。'),
            CleanupRequirement(id='auto_bg_sources', title='AutoBG 源码', available=(auto_bg / 'run_auto_align.sh').is_file(),
                               detail='本地 checkout 未包含 AutoBG 入口。' if not (auto_bg / 'run_auto_align.sh').is_file() else 'SimFoundry AutoBG 入口已找到。'),
        ]
        return values

    def catalog(self) -> CleanupCatalog:
        requirements = self._requirements()
        by_id = {item.id: item.available for item in requirements}
        preview_ids = ('video_tools', 'conda', 'simfoundry_env', 'auto_bg_sources')
        # VOID is an explicit second action after the mask review gate.
        build_ids = (*preview_ids, 'void_env', 'gemini', 'da3_env', 'nerfstudio_env', '3dgrut_env', 'mesh_env')
        videos = []
        seen = set()
        for root in self.source_roots:
            if not root.is_dir():
                continue
            try:
                candidates = sorted(root.rglob('*'))
            except OSError:
                continue
            for path in candidates:
                if len(videos) >= 300:
                    break
                if path.suffix.lower() not in VIDEO_SUFFIXES or path.is_symlink() or not path.is_file():
                    continue
                try:
                    resolved = path.resolve()
                    if not resolved.is_relative_to(root) or resolved in seen:
                        continue
                    seen.add(resolved)
                    stat = resolved.stat()
                except OSError:
                    continue
                capture_type = 'unknown'
                if root == self.example_root.resolve():
                    capture_type = 'scene_scan' if path.name == 'Fruits.mp4' else 'operation_demo'
                elif root == (self.simfoundry / 'Data/Scene_Video').resolve():
                    capture_type = 'scene_scan'
                duration, width, height = self._video_metadata(resolved)
                videos.append(CleanupVideo(id=identity(str(resolved)), name=path.name,
                                           path=self._label(resolved), size=stat.st_size,
                                           duration_s=duration, width=width, height=height,
                                           capture_type=capture_type))
        videos.sort(key=lambda video: (video.capture_type != 'operation_demo', video.name.casefold()))
        return CleanupCatalog(videos=videos, requirements=requirements,
                              preview_available=all(by_id.get(key, False) for key in preview_ids),
                              build_available=all(by_id.get(key, False) for key in build_ids))

    def _label(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.repo))
        except ValueError:
            return str(path)

    @staticmethod
    def _video_metadata(path: Path) -> tuple[float | None, int | None, int | None]:
        ffprobe = shutil.which('ffprobe')
        if not ffprobe:
            return None, None, None
        try:
            result = subprocess.run([ffprobe, '-v', 'error', '-show_entries',
                                     'format=duration:stream=width,height', '-of', 'json', str(path)],
                                    capture_output=True, text=True, timeout=4, check=True)
            payload = json.loads(result.stdout)
            stream = next((item for item in payload.get('streams', []) if item.get('width')), {})
            duration = float(payload['format']['duration']) if payload.get('format', {}).get('duration') else None
            return duration, stream.get('width'), stream.get('height')
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
            return None, None, None

    def _source_path(self, video_id: str) -> tuple[CleanupVideo, Path]:
        for video in self.catalog().videos:
            if video.id != video_id:
                continue
            for root in self.source_roots:
                path = Path(video.path) if Path(video.path).is_absolute() else self.repo / video.path
                try:
                    resolved = path.resolve()
                    if (not path.is_symlink() and resolved.is_file() and resolved.is_relative_to(root)
                            and identity(str(resolved)) == video.id):
                        return video, resolved
                except OSError:
                    continue
        raise KeyError(video_id)

    def _require(self, *, phase: str, proposal_backend: str = 'codex_external', propagation_model: str = 'sam3'):
        catalog = self.catalog()
        requirements = {item.id: item for item in catalog.requirements}
        required = ['video_tools', 'conda', 'simfoundry_env', 'sam2_checkpoint', 'auto_bg_sources']
        if propagation_model == 'sam3':
            required.remove('sam2_checkpoint')
        if phase in {'inpaint', 'build'}:
            required.append('void_env')
        # Transitional compatibility guard: the current upstream full A build
        # still instantiates Gemini directly in several stages. This is an
        # upstream adapter gap, not a product-level requirement; remove the
        # Gemini capability gate only after those stages use the provider
        # contract (for example, DexAgent's Codex external transport).
        if proposal_backend == 'gemini' or phase == 'build':
            required.append('gemini')
        if phase == 'build':
            required.extend(['da3_env', 'nerfstudio_env', '3dgrut_env', 'mesh_env'])
        missing = [requirements[item].detail for item in required if not requirements[item].available]
        if missing:
            raise CleanupUnavailable('；'.join(missing))

    def start(self, body: CleanupCreate) -> CleanupJob:
        video, source = self._source_path(body.video_id)
        if video.capture_type == 'operation_demo' and body.capture_type == 'scene_scan':
            raise ValueError('SimFoundry 示例中的操作演示视频不能标记为相机扫描视频。')
        include_objects = self._clean_names(body.include_objects)
        skip_objects = self._clean_names(body.skip_objects)
        if body.proposal_backend == 'manual' and not include_objects:
            raise ValueError('手动提案模式需要至少填写一个要从背景移除的对象名。')
        self._require(phase='mask', proposal_backend=body.proposal_backend,
                      propagation_model=body.propagation_model)
        with self.lock:
            self._ensure_idle()
            job_id = uuid.uuid4().hex[:24]
            scene = f'workbench_bg_{job_id[:10]}'
            directory = self.jobs_root / job_id
            directory.mkdir(parents=True)
            input_dir = directory / 'input'
            input_dir.mkdir()
            staged_source = input_dir / f'source{source.suffix.lower()}'
            shutil.copy2(source, staged_source)
            job = {
                'id': job_id, 'scene_name': scene, 'video_id': video.id,
                'source_video': video.model_dump(), 'source_copy': str(staged_source),
                'capture_type': body.capture_type if body.capture_type != 'unknown' else video.capture_type,
                'floor_category': body.floor_category.strip(),
                'include_objects': include_objects,
                'skip_objects': skip_objects,
                'propagation_model': body.propagation_model,
                'proposal_backend': body.proposal_backend,
                'status': 'preparing', 'current_step': '准备同一组 400 帧并生成关键帧 mask',
                'message': '正在启动 SimFoundry mask 流程。',
                'updated_at': self._now(), 'pid': None,
            }
            self._write(directory, job)
            self._launch(job_id, 'mask')
            return self.job(job_id)

    @staticmethod
    def _clean_names(values: list[str]) -> list[str]:
        result = []
        seen = set()
        for value in values:
            name = ' '.join(value.split())
            if name and name.casefold() not in seen:
                result.append(name)
                seen.add(name.casefold())
        return result

    def _ensure_idle(self):
        for path in self.jobs_root.glob('*/job.json'):
            try:
                if json.loads(path.read_text()).get('status') in ACTIVE:
                    raise CleanupBusy('已有背景清理任务运行中；同一时刻只允许一个 GPU 清理任务。')
            except (OSError, json.JSONDecodeError):
                continue

    def _launch(self, job_id: str, phase: str):
        directory = self.directory(job_id)
        job = self._read(directory)
        manager = self._conda()
        if manager is None:
            raise CleanupUnavailable('找不到 conda 或 mamba；未启动清理进程。')
        environment = dict(os.environ, PYTHONUNBUFFERED='1', SIMFOUNDRY_ENV_RUNNER=str(manager),
                           PYTHONPATH=str(self.repo) + os.pathsep + os.environ.get('PYTHONPATH', ''))
        with (directory / 'worker.log').open('ab') as log:
            try:
                process = subprocess.Popen([sys.executable, '-m', 'workbench.background_cleanup_worker',
                                            '--repo', str(self.repo), '--job-dir', str(directory), '--phase', phase],
                                           cwd=self.repo, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
            except OSError as exc:
                current = self._read(directory)
                current.update(status='failed', current_step='启动失败',
                               message=f'无法启动清理 worker：{exc}；日志和中间产物已保留。')
                self._write(directory, current)
                raise CleanupUnavailable(f'无法启动背景清理 worker：{exc}') from exc
        # Re-read after Popen: the worker may already have advanced a fast phase.
        # Updating the stale pre-launch copy here could erase its review state.
        job = self._read(directory)
        job['pid'] = process.pid
        self._write(directory, job)
        self.processes[job_id] = (process, time.monotonic())

    def approve_mask(self, job_id: str) -> CleanupJob:
        with self.lock:
            self._ensure_idle()
            job = self._read(self.directory(job_id))
            if job.get('status') != 'review_mask':
                raise ValueError('只有关键帧 mask 已生成并等待检查时，才能开始 VOID 补洞。')
            self._require(phase='inpaint')
            job.update(status='inpainting', current_step='VOID Pass 1/2 视频补洞',
                       message='关键帧 mask 已确认，正在生成时序一致的背景补全视频。', updated_at=self._now())
            self._write(self.directory(job_id), job)
            self._launch(job_id, 'inpaint')
            return self.job(job_id)

    def build_scene(self, job_id: str) -> CleanupJob:
        with self.lock:
            self._ensure_idle()
            directory = self.directory(job_id)
            job = self._read(directory)
            if job.get('status') != 'review_clean':
                raise ValueError('只有补洞视频检查通过后，才能构建 3D 背景。')
            if job.get('capture_type') != 'scene_scan':
                raise ValueError('固定机位操作演示只用于 mask/补洞冒烟测试，不能用于相机视差重建。')
            self._require(phase='build')
            job.update(status='building', current_step='SimFoundry A 重建与 AutoBG 场景对齐',
                       message='正在运行完整场景重建和背景高斯对齐。', updated_at=self._now())
            self._write(directory, job)
            self._launch(job_id, 'build')
            return self.job(job_id)

    def stop(self, job_id: str) -> CleanupJob:
        with self.lock:
            directory = self.directory(job_id)
            job = self._read(directory)
            handle = self.processes.get(job_id)
            if handle and handle[0].poll() is None:
                self._stop_process(handle[0])
            elif job.get('status') in ACTIVE and isinstance(job.get('pid'), int):
                try:
                    os.killpg(job['pid'], signal.SIGTERM)
                except ProcessLookupError:
                    pass
            job.update(status='stopped', current_step='已停止', message='任务已停止；保留已有日志与中间产物。',
                       updated_at=self._now(), pid=None)
            self._write(directory, job)
            self.processes.pop(job_id, None)
            return self.job(job_id)

    def _now(self):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def directory(self, job_id: str) -> Path:
        if len(job_id) != 24 or any(c not in '0123456789abcdef' for c in job_id):
            raise KeyError(job_id)
        path = self.jobs_root / job_id
        if not (path / 'job.json').is_file():
            raise KeyError(job_id)
        return path

    @staticmethod
    def _read(directory: Path) -> dict:
        return json.loads((directory / 'job.json').read_text())

    @staticmethod
    def _write(directory: Path, job: dict):
        job['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        temp = directory / 'job.json.tmp'
        temp.write_text(json.dumps(job, ensure_ascii=False, indent=2))
        temp.replace(directory / 'job.json')

    def job(self, job_id: str) -> CleanupJob:
        directory = self.directory(job_id)
        job = self._read(directory)
        scene = job['scene_name']
        data = self.simfoundry / 'Data' / scene
        mask = data / 'auto_bg/void/input/keyframe_masks_debug/all_objects_overlay.png'
        mask_video = data / 'auto_bg/void/input/quadmask_0.mp4'
        source = Path(job['source_copy'])
        clean = data / 'auto_bg/void/pass2/pass2.mp4'
        final_scene = self.simfoundry / 'assets/scenes' / scene / f'{scene}_scene_state_auto_bg.json'
        status = job.get('status')
        return CleanupJob(
            id=job_id, scene_name=scene, source_video=CleanupVideo.model_validate(job['source_video']),
            capture_type=job['capture_type'], status=status, current_step=job['current_step'],
            message=job['message'], include_objects=job['include_objects'], skip_objects=job['skip_objects'],
            propagation_model=job['propagation_model'],
            proposal_backend=job.get('proposal_backend', 'gemini' if job.get('use_gemini_proposal') else 'manual'),
            proposal_transport_dir=job.get('proposal_transport_dir'),
            proposed_objects=job.get('proposed_objects', []),
            detected_objects=job.get('detected_objects', []),
            mask_preview_url=f'/api/workbench/v1/background-cleanups/{job_id}/media/mask' if mask.is_file() else None,
            mask_video_url=f'/api/workbench/v1/background-cleanups/{job_id}/media/quadmask' if mask_video.is_file() else None,
            source_video_url=f'/api/workbench/v1/background-cleanups/{job_id}/media/source' if source.is_file() else '',
            cleaned_video_url=f'/api/workbench/v1/background-cleanups/{job_id}/media/cleaned' if clean.is_file() else None,
            scene_path=str(final_scene) if final_scene.is_file() else None,
            can_build_scene=status == 'review_clean' and job['capture_type'] == 'scene_scan',
            log_url=f'/api/workbench/v1/background-cleanups/{job_id}/log', updated_at=job['updated_at'])

    def jobs(self) -> list[CleanupJob]:
        paths = sorted(self.jobs_root.glob('*/job.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        result = []
        for path in paths[:30]:
            try:
                result.append(self.job(path.parent.name))
            except (OSError, ValueError, KeyError):
                continue
        return result

    def media(self, job_id: str, name: str) -> Path:
        directory = self.directory(job_id)
        job = self._read(directory)
        scene = job['scene_name']
        data = self.simfoundry / 'Data' / scene / 'auto_bg/void'
        options = {
            'source': Path(job['source_copy']),
            'mask': data / 'input/keyframe_masks_debug/all_objects_overlay.png',
            'quadmask': data / 'input/quadmask_0.mp4',
            'cleaned': data / 'pass2/pass2.mp4',
        }
        if name not in options or not options[name].is_file():
            raise KeyError(name)
        return options[name].resolve()

    def log_path(self, job_id: str) -> Path:
        path = self.directory(job_id) / 'worker.log'
        if not path.is_file():
            raise KeyError(job_id)
        return path

    def poll(self):
        with self.lock:
            for job_id, (process, started) in list(self.processes.items()):
                try:
                    directory = self.directory(job_id)
                    job = self._read(directory)
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    self.processes.pop(job_id, None)
                    continue
                if process.poll() is None and time.monotonic() - started > self.timeout_s:
                    self._stop_process(process)
                    job.update(status='failed', current_step='超时', message='清理任务超过 48 小时；日志与中间文件已保留。')
                    self._write(directory, job)
                elif process.poll() is not None:
                    if job.get('status') in ACTIVE:
                        job.update(status='failed', current_step='子进程异常退出',
                                   message=f'后台 worker 已退出，exit code={process.returncode}；查看日志定位原因。')
                        self._write(directory, job)
                    self.processes.pop(job_id, None)

    @staticmethod
    def _stop_process(process: subprocess.Popen):
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
            for job_id, (process, _) in list(self.processes.items()):
                self._stop_process(process)
                try:
                    directory = self.directory(job_id)
                    job = self._read(directory)
                    if job.get('status') in ACTIVE:
                        job.update(status='stopped', current_step='工作台已关闭',
                                   message='工作台关闭时停止了后台进程；中间产物和日志已保留。')
                        self._write(directory, job)
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    pass
            self.processes.clear()

    def _active_jobs(self):
        for path in self.jobs_root.glob('*/job.json'):
            try:
                job = self._read(path.parent)
                if job.get('status') in ACTIVE:
                    return True
            except (OSError, json.JSONDecodeError):
                continue
        return False
