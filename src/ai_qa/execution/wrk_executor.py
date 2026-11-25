"""wrk 短压测执行器（实现规格 §12.3 / §14.2）。

只做 benchmark smoke：参数来自已校验计划，资源上限由服务端再次校验。
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_qa.application.ports import ArtifactStore
from ai_qa.domain.enums import TestStatus
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.models import new_id, utc_now
from ai_qa.domain.plans import BenchmarkSpec
from ai_qa.domain.results import ArtifactRef, PerfResult
from ai_qa.validation.params import validate_http_path

_TOOL_VERSION = re.compile(r"^(wrk\s+\S+)")
_REQUESTS = re.compile(r"Requests/sec:\s+([\d.]+)")
_TRANSFER = re.compile(r"Transfer/sec:\s+([\d.]+\w+)")
_LATENCY = re.compile(r"Latency\s+([\d.]+\w+)\s+([\d.]+\w+)\s+([\d.]+\w+)\s+([\d.]+%)")
_REQ_SEC = re.compile(r"Req/Sec\s+([\d.]+\w*)\s+([\d.]+\w*)\s+([\d.]+\w*)\s+([\d.]+%)")
_SOCKET_ERRORS = re.compile(
    r"Socket errors:\s+connect (\d+),\s+read (\d+),\s+write (\d+),\s+timeout (\d+)"
)
_NON_2XX = re.compile(r"Non-2xx or 3xx responses:\s+(\d+)")
_TOTAL_REQUESTS = re.compile(r"(\d+) requests in")


@dataclass(frozen=True)
class WrkConfig:
    wrk_path: Path
    allowed_processes: tuple[Path, ...]
    max_duration_seconds: int
    max_connections: int
    max_threads: int
    timeout_margin_seconds: float = 30.0


class WrkExecutor:
    def __init__(self, config: WrkConfig) -> None:
        self._config = config

    def run(
        self,
        *,
        run_id: str,
        target_url: str,
        benchmark: BenchmarkSpec,
        artifact_store: ArtifactStore,
    ) -> PerfResult:
        wrk = self._validate_wrk()
        problem = validate_http_path(benchmark.path)
        if problem is not None:
            raise AiQaError(ErrorCode.PARAM_INVALID, problem)
        self._validate_limits(benchmark)
        if not target_url.startswith("http://"):
            raise AiQaError(ErrorCode.PARAM_INVALID, f"非法压测 URL: {target_url}")

        command = [
            str(wrk),
            "-t",
            str(benchmark.threads),
            "-c",
            str(benchmark.connections),
            "-d",
            f"{benchmark.duration_seconds}s",
            target_url,
        ]
        timeout = float(benchmark.duration_seconds) + self._config.timeout_margin_seconds
        stdout = b""
        stderr = b""
        exit_code: int | None = None
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
                shell=False,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
            self._save_outputs(run_id, stdout, stderr, artifact_store)
            raise AiQaError(
                ErrorCode.TOOL_TIMEOUT,
                f"wrk 执行超时（{timeout:.0f}s）",
                details={"run_id": run_id, "timeout_seconds": timeout},
            ) from exc
        except OSError as exc:
            raise AiQaError(
                ErrorCode.DEPENDENCY_MISSING,
                f"无法启动 wrk: {exc}",
                details={"path": str(wrk)},
            ) from exc

        stdout_ref, stderr_ref = self._save_outputs(run_id, stdout, stderr, artifact_store)
        if exit_code != 0:
            raise AiQaError(
                ErrorCode.MCP_TOOL_ERROR,
                f"wrk 退出码非零: {exit_code}",
                details={
                    "exit_code": exit_code,
                    "stderr_preview": stderr.decode("utf-8", "replace")[:500],
                    "stdout_artifact": stdout_ref.artifact_id,
                },
            )

        metrics = _parse_metrics(stdout.decode("utf-8", "replace"))
        metrics_ref = artifact_store.write_text(
            run_id,
            "benchmark/metrics.json",
            json.dumps(metrics, ensure_ascii=False, indent=2),
            "benchmark_metrics",
            mime="application/json; charset=utf-8",
        )
        notes = [
            "短压测仅作 benchmark smoke，不能用于容量结论",
            "无历史基线，性能状态为 INCONCLUSIVE",
        ]
        return PerfResult(
            perf_result_id=new_id("perf"),
            run_id=run_id,
            tool="wrk",
            tool_version=self._tool_version(wrk),
            command=command,
            target_url=target_url,
            config={
                "duration_seconds": benchmark.duration_seconds,
                "connections": benchmark.connections,
                "threads": benchmark.threads,
                "path": benchmark.path,
            },
            metrics=metrics,
            environment=_environment(),
            exit_code=exit_code,
            raw_stdout=stdout_ref,
            raw_stderr=stderr_ref,
            metrics_artifact=metrics_ref,
            test_status=_status_for(metrics),
            notes=notes,
            created_at=utc_now(),
        )

    def _validate_wrk(self) -> Path:
        wrk = self._config.wrk_path.expanduser().resolve()
        if not wrk.is_file() or not os.access(wrk, os.X_OK):
            raise AiQaError(
                ErrorCode.DEPENDENCY_MISSING,
                f"wrk 不存在或不可执行: {wrk}",
                details={"path": str(wrk)},
            )
        if self._config.allowed_processes:
            allowed = {path.expanduser().resolve() for path in self._config.allowed_processes}
            if wrk not in allowed:
                raise AiQaError(
                    ErrorCode.POLICY_VIOLATION,
                    f"wrk 不在 runtime.allowed_processes 白名单: {wrk}",
                    details={"path": str(wrk)},
                )
        return wrk

    def _validate_limits(self, benchmark: BenchmarkSpec) -> None:
        if benchmark.duration_seconds > self._config.max_duration_seconds:
            raise AiQaError(
                ErrorCode.POLICY_VIOLATION,
                f"压测时长不能超过 {self._config.max_duration_seconds} 秒",
            )
        if benchmark.connections > self._config.max_connections:
            raise AiQaError(
                ErrorCode.POLICY_VIOLATION,
                f"连接数不能超过 {self._config.max_connections}",
            )
        if benchmark.threads > self._config.max_threads:
            raise AiQaError(
                ErrorCode.POLICY_VIOLATION,
                f"线程数不能超过 {self._config.max_threads}",
            )

    @staticmethod
    def _save_outputs(
        run_id: str,
        stdout: bytes,
        stderr: bytes,
        artifact_store: ArtifactStore,
    ) -> tuple[ArtifactRef, ArtifactRef | None]:
        stdout_ref = artifact_store.write_bytes(
            run_id,
            "benchmark/wrk.txt",
            stdout,
            "benchmark_stdout",
            mime="text/plain; charset=utf-8",
        )
        stderr_ref = None
        if stderr:
            stderr_ref = artifact_store.write_bytes(
                run_id,
                "benchmark/wrk.stderr.txt",
                stderr,
                "benchmark_stderr",
                mime="text/plain; charset=utf-8",
            )
        return stdout_ref, stderr_ref

    @staticmethod
    def _tool_version(wrk: Path) -> str:
        try:
            completed = subprocess.run(
                [str(wrk), "--version"],
                capture_output=True,
                timeout=5,
                shell=False,
                check=False,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        first_line = (completed.stdout or completed.stderr).splitlines()[:1]
        if not first_line:
            return "unknown"
        match = _TOOL_VERSION.match(first_line[0].strip())
        return match.group(1) if match else first_line[0].strip()[:80]


def _parse_metrics(output: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    match = _REQUESTS.search(output)
    if match:
        metrics["requests_per_sec"] = float(match.group(1))
    match = _TRANSFER.search(output)
    if match:
        metrics["transfer_per_sec"] = match.group(1)
    match = _LATENCY.search(output)
    if match:
        metrics["latency_avg"] = match.group(1)
        metrics["latency_stdev"] = match.group(2)
        metrics["latency_max"] = match.group(3)
    match = _REQ_SEC.search(output)
    if match:
        metrics["req_sec_avg"] = match.group(1)
        metrics["req_sec_stdev"] = match.group(2)
        metrics["req_sec_max"] = match.group(3)
    match = _SOCKET_ERRORS.search(output)
    if match:
        metrics["socket_connect_errors"] = int(match.group(1))
        metrics["socket_read_errors"] = int(match.group(2))
        metrics["socket_write_errors"] = int(match.group(3))
        metrics["socket_timeouts"] = int(match.group(4))
    match = _NON_2XX.search(output)
    if match:
        metrics["non_2xx_responses"] = int(match.group(1))
    match = _TOTAL_REQUESTS.search(output)
    if match:
        metrics["total_requests"] = int(match.group(1))
    return metrics


def _status_for(metrics: dict[str, Any]) -> TestStatus:
    # 无基线时性能只能是 INCONCLUSIVE；错误计数保留在 metrics 中供报告展示。
    return TestStatus.INCONCLUSIVE


def _environment() -> dict[str, Any]:
    uname = platform.uname()
    return {
        "os": f"{uname.system} {uname.release}",
        "machine": uname.machine,
        "cpu_count": os.cpu_count(),
        "python": sys.version.split()[0],
    }
