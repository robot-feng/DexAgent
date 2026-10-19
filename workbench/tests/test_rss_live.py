"""RoboSimStudio status integration must stay read-only and fail closed."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import threading

from fastapi.testclient import TestClient

from workbench.api import PREFIX, create_app
from workbench.rss_live import rss_socket_path


def serve_one_status(run_dir: Path, reply: dict):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(rss_socket_path(run_dir)))
    listener.listen(1)
    received = []

    def serve():
        with listener:
            connection, _ = listener.accept()
            with connection:
                received.append(connection.recv(256))
                connection.sendall(json.dumps(reply).encode() + b"\n")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread, received


def test_live_endpoint_reports_unconfigured_without_connecting(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKBENCH_RSS_RUN_DIR", raising=False)
    with TestClient(create_app(tmp_path, refresh_interval=0)) as client:
        status = client.get(PREFIX + "/studio/live").json()
        capability = next(item for item in client.get(PREFIX + "/capabilities").json()
                          if item["id"] == "studio_feedback")

    assert status["configured"] is False
    assert status["connected"] is False
    assert "WORKBENCH_RSS_RUN_DIR" in status["error"]
    assert capability["available"] is False


def test_live_endpoint_reads_status_command_and_exposes_only_observed_state(tmp_path):
    reply = {
        "ok": True,
        "url": "http://localhost:8080",
        "step": 42,
        "time": 1.25,
        "paused": False,
        "finite": True,
        "playing": 0,
        "generation": 3,
        "names": ["robot", "cube"],
        "cameras": ["overview"],
        "positions": {"robot": [0.1, 0.2, 0.3], "cube": [1, 2, 3]},
        "unsafe_extra": "must not be exposed",
    }
    thread, received = serve_one_status(tmp_path, reply)

    with TestClient(create_app(tmp_path, rss_run_dir=tmp_path, refresh_interval=0)) as client:
        status = client.get(PREFIX + "/studio/live")

    thread.join(timeout=2)
    assert status.status_code == 200
    data = status.json()
    assert received == [b'{"cmd":"status"}\n']
    assert data["connected"] is True
    assert data["step"] == 42
    assert data["sim_time"] == 1.25
    assert data["positions"]["robot"] == [0.1, 0.2, 0.3]
    assert "unsafe_extra" not in data
