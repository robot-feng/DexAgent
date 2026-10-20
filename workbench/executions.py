"""Launch existing CLI entry points; native reports remain the validation authority."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

from agent.run_reports import atomic_json, sha256_file

from .index import Index, identity
from .models import Execution, ExecutionCatalog, ExecutionConfig, ExecutionCreate


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def process_birth(pid: int) -> str | None:
    """Identify a Linux process lifetime, including after a service restart."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, IndexError):
        return None


def owned_processes(data: dict) -> dict[int, str]:
    """Find tagged workers even when a native renderer creates a new session.

    Environments are inspected only for our launch marker and never returned
    in an API or written to logs. Birth times are rechecked before signalling.
    """
    marker = f"EMBODIFORGE_EXECUTION_ID={data['id']}".encode()
    result = {}
    for path in Path("/proc").glob("[0-9]*"):
        try:
            if path.stat().st_uid != os.getuid():
                continue
            pid = int(path.name)
            birth = process_birth(pid)
            if birth and marker in (path / "environ").read_bytes().split(b"\0"):
                result[pid] = birth
        except (OSError, ValueError):
            continue
    if (
        data.get("pid")
        and data.get("birth")
        and process_birth(data["pid"]) == data["birth"]
    ):
        result[data["pid"]] = data["birth"]
    return result


class ExecutionBusy(RuntimeError):
    pass


class Executions:
    def __init__(
        self, index: Index, state_dir: Path, *, input_roots: tuple[Path, ...] = ()
    ):
        self.index = index
        self.repo = index.repo
        self.root = state_dir.resolve() / "executions"
        self.root.mkdir(parents=True, exist_ok=True)
        self.input_roots = (
            self.repo,
            *index.roots,
            *(p.resolve() for p in input_roots),
        )
        self.lock = threading.RLock()
        self.processes: dict[str, subprocess.Popen] = {}
        self.closed = False

    def config_paths(self) -> dict[str, Path]:
        return {
            identity(str(p.resolve())): p.resolve()
            for pattern in ("*.yaml", "*.yml")
            for p in self.repo.glob(pattern)
            if p.is_file() and p.resolve().is_relative_to(self.repo)
        }

    def catalog(self) -> ExecutionCatalog:
        configs = [
            ExecutionConfig(id=key, name=path.name)
            for key, path in self.config_paths().items()
        ]
        modes = []
        if (self.repo / "run_dexagent.py").is_file():
            modes.append("pipeline")
        if (self.repo / "run_robosimstudio.py").is_file():
            modes += ["rigid", "towel", "sharpa", "grasp", "reference", "simfoundry"]
        ready = bool(configs and modes and importlib.util.find_spec("yaml"))
        return ExecutionCatalog(
            available=ready,
            configs=sorted(configs, key=lambda c: c.name),
            modes=modes,
            detail="使用已有 CLI 执行；环境、输入和任务结果由原生预检与验收报告判断。"
            if ready
            else "需要本地运行入口、项目根目录 YAML 配置及 PyYAML。",
        )

    def directory(self, key: str) -> Path:
        if not re.fullmatch("[a-f0-9]{24}", key):
            raise KeyError(key)
        path = self.root / key
        if not (path / "launch.json").is_file():
            raise KeyError(key)
        return path

    def _read(self, key: str) -> dict:
        return json.loads((self.directory(key) / "launch.json").read_text())

    def _write(self, data: dict):
        path = self.root / data["id"] / "launch.json"
        atomic_json(path, data)
        path.chmod(0o600)

    def _input(
        self, value: str, *, directory=False, extra_root: Path | None = None
    ) -> Path:
        path = Path(value).expanduser()
        path = (path if path.is_absolute() else self.repo / path).resolve()
        roots = (*self.input_roots, *((extra_root,) if extra_root else ()))
        if not any(path.is_relative_to(root) for root in roots):
            raise ValueError(
                "输入必须位于项目、已配置产物/视频目录或当前配置的视频目录内"
            )
        if not (path.is_dir() if directory else path.is_file()):
            raise ValueError(f"输入{'目录' if directory else '文件'}不存在：{path}")
        return path

    def start(
        self, request: ExecutionCreate, *, previous: dict | None = None
    ) -> Execution:
        from agent.config import load_config
        import yaml

        with self.lock:
            if self.closed:
                raise ExecutionBusy("工作台正在关闭，不能启动新运行。")
            if any(job.status == "running" for job in self.jobs()):
                raise ExecutionBusy("已有执行任务正在运行；可先查看或停止当前任务。")
            if request.mode not in self.catalog().modes:
                raise ValueError("当前项目没有对应的运行入口")
            if previous:
                source = self.directory(previous["id"]) / "config.yaml"
                if sha256_file(source) != previous["config_sha256"]:
                    raise ValueError("原运行配置快照发生变化，不能用于重试")
                cfg = yaml.safe_load(source.read_text())
                config_name = previous["config_name"]
                source_sha = previous["source_config_sha256"]
            else:
                source = self.config_paths().get(request.config_id)
                if source is None:
                    raise ValueError("配置已移除，请刷新后重新选择")
                source_sha = sha256_file(source)
                try:
                    cfg = load_config(source)
                except (
                    OSError,
                    yaml.YAMLError,
                    TypeError,
                    AttributeError,
                    ValueError,
                ) as exc:
                    raise ValueError("配置无法读取或不是有效的任务配置") from exc
                if sha256_file(source) != source_sha:
                    raise ValueError("读取期间配置发生变化，请重新启动")
                config_name = source.name
            cfg.pop("_config_path", None)
            if request.video.strip():
                original = Path(str(cfg.get("inputs", {}).get("video", "")))
                extra = original.parent.resolve() if original.is_file() else None
                cfg.setdefault("inputs", {})["video"] = str(
                    self._input(request.video.strip(), extra_root=extra)
                )
            if request.prompt.strip():
                cfg.setdefault("inputs", {})["task"] = request.prompt.strip()
            key = uuid.uuid4().hex[:24]
            output_name = f"studio_{request.mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{key[:6]}"
            output = self.index.runs_root / output_name
            if output.exists():
                raise ValueError("输出目录已存在，请重新启动以创建新运行")
            cfg.setdefault("run", {}).update(
                output_dir=str(self.index.runs_root), name=output_name
            )
            agent_transport = None
            if (
                request.mode == "pipeline"
                and cfg.get("agent", {}).get("provider") == "external"
            ):
                agent_transport = output / "external"
                cfg["agent"]["transport_dir"] = str(agent_transport)
            python = Path(cfg.get("environments", {}).get("pipeline") or sys.executable)
            if not python.is_file():
                raise ValueError(f"配置中的运行解释器不存在：{python}")
            directory = self.root / key
            snapshot = directory / "config.yaml"
            if request.mode == "pipeline":
                runner = self.repo / "run_dexagent.py"
                command = [
                    str(python),
                    str(runner),
                    str(snapshot),
                    "--profile",
                    request.profile,
                    "--to",
                    request.stop_at,
                    "--yes",
                ]
            else:
                runner = self.repo / "run_robosimstudio.py"
                command = [
                    str(python),
                    str(runner),
                    request.mode,
                    "--config",
                    str(snapshot),
                    "--output",
                    str(output),
                ]
                if request.no_render:
                    command.append("--no-render")
                if request.mode == "reference":
                    if not request.reference_run_id:
                        raise ValueError("请选择已有 DexAgent 参考运行")
                    reference, _ = self.index.run_path(request.reference_run_id)
                    command += ["--reference-run", str(reference)]
                if request.mode == "simfoundry":
                    for stage in ("stage8", "stage11", "stage12"):
                        value = getattr(request, stage).strip()
                        if not value:
                            raise ValueError(
                                "SimFoundry 资产验收需要 Stage 8、11、12 输出目录"
                            )
                        command += [
                            f"--simfoundry-{stage}",
                            str(self._input(value, directory=True)),
                        ]
            directory.mkdir(mode=0o700)
            snapshot.write_text(
                yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
            )
            snapshot.chmod(0o600)
            data = dict(
                id=key,
                mode=request.mode,
                config_name=config_name,
                profile=request.profile if request.mode == "pipeline" else None,
                status="running",
                message="正在启动原生执行进程。",
                output_name=output_name,
                created_at=now(),
                finished_at=None,
                returncode=None,
                retry_of=previous["id"] if previous else None,
                request=request.model_dump(),
                source_config_sha256=source_sha,
                config_sha256=sha256_file(snapshot),
                runner_sha256=sha256_file(runner),
                command=command,
                pid=None,
                birth=None,
                agent_transport=str(agent_transport) if agent_transport else None,
            )
            self._write(data)
            try:
                with (directory / "console.log").open("wb") as log:
                    process = subprocess.Popen(
                        command,
                        cwd=self.repo,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        env=dict(
                            os.environ,
                            PYTHONUNBUFFERED="1",
                            EMBODIFORGE_EXECUTION_ID=key,
                        ),
                    )
                self.processes[key] = process
                data.update(
                    pid=process.pid,
                    birth=process_birth(process.pid),
                    message="进程运行中；验收结论请查看原生运行报告。",
                )
            except OSError as exc:
                data.update(
                    status="failed", message=f"启动失败：{exc}", finished_at=now()
                )
            self._write(data)
            return self.state(key)

    def _live(self, data: dict) -> bool:
        process = self.processes.get(data["id"])
        if process:
            return process.poll() is None
        try:
            return bool(
                data.get("pid")
                and data.get("birth")
                and process_birth(data["pid"]) == data["birth"]
                and os.getpgid(data["pid"]) == data["pid"]
            )
        except ProcessLookupError:
            return False

    @staticmethod
    def _stop_workers(data: dict, process=None, *, interrupt=True):
        signals = (
            ((signal.SIGINT, 5), (signal.SIGTERM, 2), (signal.SIGKILL, 2))
            if interrupt
            else ((signal.SIGTERM, 2), (signal.SIGKILL, 2))
        )
        for signum, grace in signals:
            deadline = time.monotonic() + grace
            signalled = set()
            while time.monotonic() < deadline:
                if process:
                    process.poll()
                targets = owned_processes(data)
                if not targets:
                    return
                for pid, birth in targets.items():
                    if (pid, birth) in signalled or process_birth(pid) != birth:
                        continue
                    try:
                        os.kill(pid, signum)
                    except ProcessLookupError:
                        continue
                    signalled.add((pid, birth))
                time.sleep(0.05)
        if owned_processes(data):
            raise RuntimeError("运行的子进程仍未退出，日志已保留，请稍后重查")

    def _agent_wait(self, data: dict):
        from .models import ExecutionAgentWait

        transport = data.get("agent_transport")
        if not transport:
            return None
        root = Path(transport)
        status = root / "status.json"
        try:
            if status.stat().st_size > 16 * 1024:
                return None
            value = json.loads(status.read_text())
            session, turn = value.get("session"), value.get("turn")
            if (
                value.get("state") != "waiting"
                or not isinstance(session, str)
                or not re.fullmatch("[a-f0-9]{32}", session)
                or type(turn) is not int
                or turn < 1
            ):
                return None
            request_id = f"{session}-{turn:04d}"
            request = root / "requests" / f"{request_id}.json"
            if (
                value.get("id") != request_id
                or value.get("request") != str(request)
                or not request.is_file()
                or (root / "responses" / request.name).exists()
            ):
                return None
            return ExecutionAgentWait(
                request_id=request_id, turn=turn, request_path=str(request)
            )
        except (OSError, ValueError, AttributeError):
            return None

    def state(self, key: str) -> Execution:
        with self.lock:
            data = self._read(key)
            process = self.processes.get(key)
            if data["status"] == "running" and not self._live(data):
                code = process.poll() if process else None
                orphan = bool(owned_processes(data))
                if orphan:
                    self._stop_workers(data, process, interrupt=False)
                data.update(
                    returncode=code,
                    finished_at=now(),
                    status="failed"
                    if orphan
                    else "completed"
                    if code == 0
                    else "failed"
                    if code is not None
                    else "interrupted",
                    message="主进程退出后仍有子进程存活，已停止残留进程；请检查原生报告。"
                    if orphan
                    else "执行进程已结束；任务验收和发布资格以原生报告为准。"
                    if code == 0
                    else f"执行进程退出（{code}），请查看日志和原生失败报告。"
                    if code is not None
                    else "原进程已结束，工作台未取得退出码；请检查保留的原生报告。",
                )
                self._write(data)
                self.processes.pop(key, None)
                self.index.refresh()
            run_id = identity(
                str((self.index.runs_root / data["output_name"]).resolve())
            )
            try:
                self.index.run_path(run_id)
            except KeyError:
                run_id = None
            waiting = self._agent_wait(data) if data["status"] == "running" else None
            return Execution(
                **{
                    **data,
                    "message": "等待 Agent 响应；当前请求已保存在本次运行目录。"
                    if waiting
                    else data["message"],
                },
                run_id=run_id,
                agent_wait=waiting,
            )

    def jobs(self) -> list[Execution]:
        return sorted(
            (self.state(p.parent.name) for p in self.root.glob("*/launch.json")),
            key=lambda j: j.created_at,
            reverse=True,
        )

    def retry(self, key: str) -> Execution:
        with self.lock:
            if self.state(key).status == "running":
                raise ExecutionBusy("请先等待或停止当前运行，再创建重试。")
            previous = self._read(key)
            return self.start(ExecutionCreate(**previous["request"]), previous=previous)

    def stop(self, key: str) -> Execution:
        with self.lock:
            current = self.state(key)
            if current.status != "running":
                return current
            data = self._read(key)
            process = self.processes.get(key)
            self._stop_workers(data, process)
            data.update(
                status="stopped",
                finished_at=now(),
                returncode=process.poll() if process else None,
                message="已停止执行；日志与原生产物已保留。重试会创建新运行。",
            )
            self._write(data)
            self.processes.pop(key, None)
            self.index.refresh()
            return self.state(key)

    def log(self, key: str) -> str:
        path = self.directory(key) / "console.log"
        if not path.is_file():
            return ""
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 64 * 1024))
            return stream.read(64 * 1024).decode("utf-8", errors="replace")

    def poll(self):
        self.jobs()

    def close(self):
        with self.lock:
            self.closed = True
            for job in self.jobs():
                if job.status == "running":
                    self.stop(job.id)
