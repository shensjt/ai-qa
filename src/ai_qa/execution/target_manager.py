"""被测目标进程生命周期（实现规格 §12.6 / §14.1）。

只启动配置里白名单内的可执行文件；不使用"杀掉占用端口的任意进程"这类清理。
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.execution.http_protocol import HttpConnection, HttpProtocolError, build_request
from ai_qa.execution.process import minimal_child_env
from ai_qa.settings import AppSettings, TargetSettings

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TargetConfig:
    executable_path: Path
    working_directory: Path
    www_root: Path
    host: str
    port: int
    threads: int
    timeout_seconds: int
    cache_bytes: int
    health_path: str
    startup_timeout_seconds: int
    stop_timeout_seconds: float
    allowed_hosts: tuple[str, ...] = ()
    allowed_processes: tuple[Path, ...] = ()


def target_config_from_settings(settings: AppSettings) -> TargetConfig:
    target: TargetSettings = settings.target
    return TargetConfig(
        executable_path=target.executable_path,
        working_directory=target.working_directory,
        www_root=target.www_root,
        host=target.host,
        port=target.port,
        threads=target.threads,
        timeout_seconds=target.timeout_seconds,
        cache_bytes=target.cache_bytes,
        health_path=target.health_path,
        startup_timeout_seconds=target.startup_timeout_seconds,
        stop_timeout_seconds=float(target.stop_timeout_seconds),
        allowed_hosts=tuple(settings.runtime.allowed_target_hosts),
        allowed_processes=tuple(settings.runtime.allowed_processes),
    )


class TargetManager:
    def __init__(self, config: TargetConfig, log_dir: Path | None = None) -> None:
        self._config = config
        self._log_dir = (log_dir or Path(".data/target-logs")).expanduser()
        self._proc: subprocess.Popen[bytes] | None = None
        self._stdout_handle: IO[bytes] | None = None
        self._stderr_handle: IO[bytes] | None = None
        self._stdout_path: Path | None = None
        self._stderr_path: Path | None = None

    @property
    def config(self) -> TargetConfig:
        return self._config

    @property
    def host(self) -> str:
        return self._config.host

    @property
    def port(self) -> int:
        return self._config.port

    @property
    def health_path(self) -> str:
        return self._config.health_path

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def stdout_path(self) -> Path | None:
        return self._stdout_path

    @property
    def stderr_path(self) -> Path | None:
        return self._stderr_path

    def start(self) -> None:
        if self._proc is not None:
            raise AiQaError(ErrorCode.INTERNAL_ERROR, "目标进程已经启动")
        self._validate_paths()
        self._check_port_free()

        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._stdout_path = self._log_dir / "target.stdout.log"
        self._stderr_path = self._log_dir / "target.stderr.log"

        argv = self._build_argv()
        try:
            self._stdout_handle = self._stdout_path.open("wb")
            self._stderr_handle = self._stderr_path.open("wb")
            self._proc = subprocess.Popen(
                argv,
                cwd=self._config.working_directory,
                stdin=subprocess.DEVNULL,
                stdout=self._stdout_handle,
                stderr=self._stderr_handle,
                env=minimal_child_env(),
                start_new_session=True,
                shell=False,
            )
        except OSError as exc:
            self._close_handles()
            raise AiQaError(
                ErrorCode.TARGET_START_FAILED,
                f"启动目标失败: {self._config.executable_path}",
                details={"executable": str(self._config.executable_path)},
            ) from exc

        try:
            self._wait_until_ready()
        except AiQaError:
            self._terminate_quietly()
            self._close_handles()
            raise

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        try:
            if proc is not None:
                self._terminate(proc)
        finally:
            self._close_handles()

    def __enter__(self) -> TargetManager:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def _validate_paths(self) -> None:
        config = self._config
        executable = config.executable_path.expanduser().resolve()
        if not executable.is_file():
            raise AiQaError(
                ErrorCode.TARGET_NOT_FOUND,
                f"目标可执行文件不存在: {executable}",
                details={"path": str(executable)},
            )
        if not os.access(executable, os.X_OK):
            raise AiQaError(
                ErrorCode.TARGET_NOT_FOUND,
                f"目标文件不可执行: {executable}",
                details={"path": str(executable)},
            )
        if config.allowed_processes:
            allowed = {path.expanduser().resolve() for path in config.allowed_processes}
            if executable not in allowed:
                raise AiQaError(
                    ErrorCode.POLICY_VIOLATION,
                    f"目标可执行文件不在 runtime.allowed_processes 白名单: {executable}",
                    details={"path": str(executable)},
                )
        if config.allowed_hosts and config.host not in config.allowed_hosts:
            raise AiQaError(
                ErrorCode.POLICY_VIOLATION,
                f"目标 host 不在 runtime.allowed_target_hosts 白名单: {config.host}",
                details={"host": config.host},
            )
        if not config.working_directory.is_dir():
            raise AiQaError(
                ErrorCode.TARGET_NOT_FOUND,
                f"目标工作目录不存在: {config.working_directory}",
                details={"path": str(config.working_directory)},
            )
        if not config.www_root.is_dir():
            raise AiQaError(
                ErrorCode.TARGET_NOT_FOUND,
                f"目标 www_root 不存在: {config.www_root}",
                details={"path": str(config.www_root)},
            )

    def _check_port_free(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self._config.host, self._config.port))
        except OSError as exc:
            raise AiQaError(
                ErrorCode.PORT_UNAVAILABLE,
                f"端口不可用: {self._config.host}:{self._config.port}",
                details={"host": self._config.host, "port": self._config.port},
            ) from exc
        finally:
            sock.close()

    def _build_argv(self) -> list[str]:
        config = self._config
        return [
            str(config.executable_path),
            "--host",
            config.host,
            "--port",
            str(config.port),
            "--root",
            str(config.www_root),
            "--threads",
            str(config.threads),
            "--timeout",
            str(config.timeout_seconds),
            "--cache-bytes",
            str(config.cache_bytes),
        ]

    def _wait_until_ready(self) -> None:
        config = self._config
        deadline = time.monotonic() + config.startup_timeout_seconds
        last_error = "未收到健康检查响应"
        host_header = f"{config.host}:{config.port}"

        while time.monotonic() < deadline:
            proc = self._proc
            if proc is not None:
                returncode = proc.poll()
                if returncode is not None:
                    raise AiQaError(
                        ErrorCode.TARGET_START_FAILED,
                        f"目标进程提前退出（code={returncode}）",
                        details={
                            "returncode": returncode,
                            "stderr_tail": self._read_stderr_tail(),
                        },
                    )
            try:
                with HttpConnection(
                    config.host, config.port, connect_timeout=0.5, read_timeout=0.5
                ) as connection:
                    connection.send(build_request("GET", config.health_path, host_header))
                    response = connection.read_response()
                if response.status == 200:
                    LOGGER.info(
                        "target ready",
                        extra={
                            "context": {
                                "host": config.host,
                                "port": config.port,
                                "path": config.health_path,
                            }
                        },
                    )
                    return
                last_error = f"健康检查返回 {response.status}"
            except (OSError, HttpProtocolError) as exc:
                last_error = str(exc)[:300]
            time.sleep(0.05)

        raise AiQaError(
            ErrorCode.TARGET_NOT_READY,
            f"目标在 {config.startup_timeout_seconds} 秒内未通过健康检查",
            details={"health_path": config.health_path, "last_error": last_error},
        )

    def _read_stderr_tail(self, limit: int = 2000) -> str:
        path = self._stderr_path
        if path is None or not path.exists():
            return ""
        try:
            data = path.read_bytes()[-limit:]
        except OSError:
            return ""
        return data.decode("utf-8", errors="replace")

    def _terminate(self, proc: subprocess.Popen[bytes]) -> None:
        if proc.poll() is not None:
            return

        pgid = self._process_group(proc)
        if pgid is None:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=self._config.stop_timeout_seconds)
            return
        except subprocess.TimeoutExpired:
            LOGGER.warning("target did not stop in time, sending SIGKILL")

        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=self._config.stop_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise AiQaError(
                ErrorCode.TARGET_STOP_FAILED,
                "目标进程在 SIGKILL 后仍未退出",
                details={"pid": proc.pid},
            ) from exc

    def _terminate_quietly(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            self._terminate(proc)
        except AiQaError:
            LOGGER.error("failed to clean up target process %s", proc.pid)

    @staticmethod
    def _process_group(proc: subprocess.Popen[bytes]) -> int | None:
        try:
            return os.getpgid(proc.pid)
        except ProcessLookupError:
            return None

    def _close_handles(self) -> None:
        for handle in (self._stdout_handle, self._stderr_handle):
            if handle is not None:
                with contextlib.suppress(OSError):
                    handle.close()
        self._stdout_handle = None
        self._stderr_handle = None
