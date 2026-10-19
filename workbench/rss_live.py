"""Read-only status bridge to a RoboSimStudio live page."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import socket
import tempfile
from datetime import datetime, timezone


def rss_socket_path(run_dir: Path) -> Path:
    """Mirror RoboSimStudio's short-path rule for its Unix control socket."""
    path = run_dir / "ctl.sock"
    full = path.resolve()
    if len(os.fsencode(full)) <= 100:
        return path if len(os.fsencode(path)) <= 100 else full
    digest = hashlib.sha1(os.fsencode(full)).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"rss-live-{digest}.sock"


def _finite_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class RssLiveInspector:
    """Read one configured RSS run directory; never issues simulation commands."""

    def __init__(self, repo: Path, run_dir: str | Path | None = None, *, timeout: float = 1.0):
        configured = run_dir or os.environ.get("WORKBENCH_RSS_RUN_DIR")
        self.run_dir = None
        if configured:
            value = Path(configured).expanduser()
            self.run_dir = (value if value.is_absolute() else repo / value).resolve()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return self.run_dir is not None

    def read(self) -> dict:
        checked_at = datetime.now(timezone.utc).isoformat()
        base = {
            "configured": self.configured,
            "connected": False,
            "checked_at": checked_at,
            "run_dir": str(self.run_dir) if self.run_dir else None,
            "url": None,
            "step": None,
            "sim_time": None,
            "paused": None,
            "finite": None,
            "playing": None,
            "generation": None,
            "names": [],
            "cameras": [],
            "positions": {},
            "error": None,
        }
        if self.run_dir is None:
            base["error"] = "未配置 WORKBENCH_RSS_RUN_DIR"
            return base

        sock_path = rss_socket_path(self.run_dir)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(self.timeout)
                client.connect(str(sock_path))
                client.sendall(b'{"cmd":"status"}\n')
                chunks = bytearray()
                while b"\n" not in chunks and len(chunks) <= 1_048_576:
                    part = client.recv(65536)
                    if not part:
                        break
                    chunks.extend(part)
            line = bytes(chunks).split(b"\n", 1)[0]
            if not line:
                raise ValueError("RSS 控制 socket 未返回状态")
            reply = json.loads(line)
            if not isinstance(reply, dict) or not reply.get("ok"):
                detail = reply.get("error", "RSS 状态请求失败") if isinstance(reply, dict) else "无效响应"
                raise ValueError(str(detail))
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            base["error"] = f"{type(exc).__name__}: {exc}"
            return base

        raw_positions = reply.get("positions")
        positions = {}
        if isinstance(raw_positions, dict):
            for name, values in list(raw_positions.items())[:100]:
                if isinstance(values, (list, tuple)):
                    vector = [_finite_number(value) for value in values[:16]]
                    positions[str(name)] = vector if all(value is not None for value in vector) else "不可用"
                else:
                    positions[str(name)] = str(values)[:100]

        base.update({
            "connected": True,
            "url": reply.get("url") if isinstance(reply.get("url"), str) else None,
            "step": reply.get("step") if isinstance(reply.get("step"), int) else None,
            "sim_time": _finite_number(reply.get("time")),
            "paused": reply.get("paused") if isinstance(reply.get("paused"), bool) else None,
            "finite": reply.get("finite") if isinstance(reply.get("finite"), bool) else None,
            "playing": reply.get("playing") if isinstance(reply.get("playing"), int) else None,
            "generation": reply.get("generation") if isinstance(reply.get("generation"), int) else None,
            "names": [str(value) for value in reply.get("names", [])[:100]] if isinstance(reply.get("names"), list) else [],
            "cameras": [str(value) for value in reply.get("cameras", [])[:50]] if isinstance(reply.get("cameras"), list) else [],
            "positions": positions,
        })
        return base
