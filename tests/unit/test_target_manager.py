from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Any

import pytest

from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.execution.http_protocol import HttpConnection, build_request
from ai_qa.execution.target_manager import TargetConfig, TargetManager

FAKE_SERVER = """#!/usr/bin/env python3
import argparse
import signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
for flag in ("--host", "--port", "--root", "--threads", "--timeout", "--cache-bytes"):
    parser.add_argument(flag)
args, _ = parser.parse_known_args()

if {ignore_term}:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{{"status":"ok"}}' if self.path == "/healthz" else b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

ThreadingHTTPServer((args.host, int(args.port)), Handler).serve_forever()
"""


def _write_executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _config(
    executable: Path,
    working_directory: Path,
    www_root: Path,
    port: int,
    **overrides: Any,
) -> TargetConfig:
    values: dict[str, Any] = {
        "executable_path": executable,
        "working_directory": working_directory,
        "www_root": www_root,
        "host": "127.0.0.1",
        "port": port,
        "threads": 1,
        "timeout_seconds": 30,
        "cache_bytes": 0,
        "health_path": "/healthz",
        "startup_timeout_seconds": 5,
        "stop_timeout_seconds": 1.0,
        "allowed_hosts": ("127.0.0.1",),
        "allowed_processes": (executable,),
    }
    values.update(overrides)
    return TargetConfig(**values)


def _health(port: int) -> int:
    with HttpConnection("127.0.0.1", port, connect_timeout=0.5, read_timeout=0.5) as conn:
        conn.send(build_request("GET", "/healthz", f"127.0.0.1:{port}"))
        return conn.read_response().status


@pytest.fixture
def target_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "cpp"
    www = root / "www"
    www.mkdir(parents=True)
    executable = _write_executable(
        root / "fake_server", FAKE_SERVER.replace("{ignore_term}", "False")
    )
    return executable, root, www


def test_start_health_and_stop(target_files: tuple[Path, Path, Path], tmp_path: Path) -> None:
    executable, root, www = target_files
    port = _free_port()
    manager = TargetManager(_config(executable, root, www, port), log_dir=tmp_path / "logs")

    manager.start()
    try:
        assert manager.is_running
        assert _health(port) == 200
        assert manager.stdout_path is not None and manager.stdout_path.exists()
    finally:
        manager.stop()

    assert manager.is_running is False
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.2).close()


def test_port_in_use_rejected(target_files: tuple[Path, Path, Path], tmp_path: Path) -> None:
    executable, root, www = target_files
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = int(blocker.getsockname()[1])
        manager = TargetManager(_config(executable, root, www, port), tmp_path / "logs")

        with pytest.raises(AiQaError) as excinfo:
            manager.start()

    assert excinfo.value.code is ErrorCode.PORT_UNAVAILABLE
    assert manager.is_running is False


def test_missing_executable_rejected(target_files: tuple[Path, Path, Path], tmp_path: Path) -> None:
    _, root, www = target_files
    manager = TargetManager(
        _config(tmp_path / "missing", root, www, _free_port()), tmp_path / "logs"
    )

    with pytest.raises(AiQaError) as excinfo:
        manager.start()

    assert excinfo.value.code is ErrorCode.TARGET_NOT_FOUND


def test_executable_outside_allowlist_rejected(
    target_files: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    executable, root, www = target_files
    other = _write_executable(tmp_path / "other", "#!/bin/sh\nexit 0\n")
    manager = TargetManager(
        _config(
            executable,
            root,
            www,
            _free_port(),
            allowed_processes=(other,),
        ),
        tmp_path / "logs",
    )

    with pytest.raises(AiQaError) as excinfo:
        manager.start()

    assert excinfo.value.code is ErrorCode.POLICY_VIOLATION


def test_missing_www_root_rejected(target_files: tuple[Path, Path, Path], tmp_path: Path) -> None:
    executable, root, _ = target_files
    manager = TargetManager(
        _config(executable, root, tmp_path / "no-www", _free_port()),
        tmp_path / "logs",
    )

    with pytest.raises(AiQaError) as excinfo:
        manager.start()

    assert excinfo.value.code is ErrorCode.TARGET_NOT_FOUND


def test_early_exit_reported_as_start_failure(tmp_path: Path) -> None:
    root = tmp_path / "cpp"
    www = root / "www"
    www.mkdir(parents=True)
    executable = _write_executable(root / "exits", "#!/bin/sh\nexit 3\n")
    manager = TargetManager(_config(executable, root, www, _free_port()), tmp_path / "logs")

    with pytest.raises(AiQaError) as excinfo:
        manager.start()

    assert excinfo.value.code is ErrorCode.TARGET_START_FAILED
    assert excinfo.value.details["returncode"] == 3
    assert manager.is_running is False


def test_startup_timeout_is_not_ready(tmp_path: Path) -> None:
    root = tmp_path / "cpp"
    www = root / "www"
    www.mkdir(parents=True)
    executable = _write_executable(
        root / "silent", "#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n"
    )
    manager = TargetManager(
        _config(
            executable,
            root,
            www,
            _free_port(),
            startup_timeout_seconds=0.3,
            stop_timeout_seconds=1.0,
        ),
        tmp_path / "logs",
    )

    with pytest.raises(AiQaError) as excinfo:
        manager.start()

    assert excinfo.value.code is ErrorCode.TARGET_NOT_READY
    assert manager.is_running is False


def test_stop_escalates_to_sigkill(tmp_path: Path) -> None:
    root = tmp_path / "cpp"
    www = root / "www"
    www.mkdir(parents=True)
    executable = _write_executable(root / "stubborn", FAKE_SERVER.replace("{ignore_term}", "True"))
    port = _free_port()
    manager = TargetManager(
        _config(
            executable,
            root,
            www,
            port,
            stop_timeout_seconds=0.3,
        ),
        tmp_path / "logs",
    )

    manager.start()
    assert _health(port) == 200
    started = time.monotonic()
    manager.stop()
    elapsed = time.monotonic() - started

    assert elapsed >= 0.3
    assert manager.is_running is False
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.2).close()


ENV_PROBE = """#!/usr/bin/env python3
import os
import time

open("env_probe.txt", "w", encoding="utf-8").write(
    os.environ.get("DEEPSEEK_API_KEY", "unset")
)
time.sleep(5)
"""


def test_target_does_not_inherit_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "cpp"
    www = root / "www"
    www.mkdir(parents=True)
    executable = _write_executable(root / "probe", ENV_PROBE)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-do-not-leak")
    manager = TargetManager(
        _config(
            executable,
            root,
            www,
            _free_port(),
            startup_timeout_seconds=0.3,
            stop_timeout_seconds=0.5,
        ),
        tmp_path / "logs",
    )

    with pytest.raises(AiQaError):
        manager.start()

    assert (root / "env_probe.txt").read_text(encoding="utf-8") == "unset"
