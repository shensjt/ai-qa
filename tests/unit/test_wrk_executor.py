from __future__ import annotations

from pathlib import Path

import pytest

from ai_qa.artifacts.store import FileArtifactStore
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.plans import BenchmarkSpec
from ai_qa.execution.wrk_executor import WrkConfig, WrkExecutor

FAKE_WRK = """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "wrk 4.2.0"
  exit 0
fi
cat <<'OUT'
Running 5s test @ http://127.0.0.1:8080/
  2 threads and 10 connections
  Thread Stats   Avg      Stdev     Max   +/- Stdev
    Latency     1.23ms  200.00us  10.00ms   90.00%
    Req/Sec     1.00k   100.00     2.00k    80.00%
  12345 requests in 5.00s, 10.00MB read
Requests/sec:   2469.00
Transfer/sec:      2.00MB
Socket errors: connect 0, read 0, write 0, timeout 1
Non-2xx or 3xx responses: 3
OUT
"""

SLOW_WRK = "#!/bin/sh\nsleep 5\n"

FAIL_WRK = "#!/bin/sh\necho boom >&2\nexit 2\n"


def _script(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _benchmark(**overrides: object) -> BenchmarkSpec:
    data: dict[str, object] = {
        "enabled": True,
        "tool": "wrk",
        "path": "/",
        "duration_seconds": 5,
        "connections": 10,
        "threads": 2,
    }
    data.update(overrides)
    return BenchmarkSpec.model_validate(data)


def _executor(tmp_path: Path, wrk: Path, **overrides: object) -> WrkExecutor:
    config = WrkConfig(
        wrk_path=wrk,
        allowed_processes=(wrk,),
        max_duration_seconds=10,
        max_connections=64,
        max_threads=4,
        timeout_margin_seconds=1.0,
    )
    return WrkExecutor(config)


def test_runs_wrk_and_parses_metrics(tmp_path: Path) -> None:
    wrk = _script(tmp_path, "wrk", FAKE_WRK)
    store = FileArtifactStore(tmp_path / "artifacts")

    perf = _executor(tmp_path, wrk).run(
        run_id="run_1",
        target_url="http://127.0.0.1:8080/",
        benchmark=_benchmark(),
        artifact_store=store,
    )

    assert perf.tool == "wrk"
    assert perf.tool_version.startswith("wrk 4.2.0")
    assert perf.metrics["requests_per_sec"] == 2469.0
    assert perf.metrics["non_2xx_responses"] == 3
    assert perf.metrics["socket_timeouts"] == 1
    assert perf.test_status.value == "INCONCLUSIVE"
    assert perf.exit_code == 0
    assert perf.raw_stdout.uri.endswith("benchmark/wrk.txt")
    assert perf.metrics_artifact is not None
    assert (tmp_path / "artifacts" / perf.raw_stdout.uri).is_file()


def test_missing_wrk_is_dependency_error(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    missing = tmp_path / "no-wrk"

    with pytest.raises(AiQaError) as excinfo:
        _executor(tmp_path, missing).run(
            run_id="run_1",
            target_url="http://127.0.0.1:8080/",
            benchmark=_benchmark(),
            artifact_store=store,
        )

    assert excinfo.value.code is ErrorCode.DEPENDENCY_MISSING


def test_wrk_not_in_allowlist_rejected(tmp_path: Path) -> None:
    wrk = _script(tmp_path, "wrk", FAKE_WRK)
    other = _script(tmp_path, "other", "#!/bin/sh\nexit 0\n")
    executor = WrkExecutor(
        WrkConfig(
            wrk_path=wrk,
            allowed_processes=(other,),
            max_duration_seconds=10,
            max_connections=64,
            max_threads=4,
        )
    )

    with pytest.raises(AiQaError) as excinfo:
        executor.run(
            run_id="run_1",
            target_url="http://127.0.0.1:8080/",
            benchmark=_benchmark(),
            artifact_store=FileArtifactStore(tmp_path / "artifacts"),
        )

    assert excinfo.value.code is ErrorCode.POLICY_VIOLATION


def test_limits_revalidated(tmp_path: Path) -> None:
    wrk = _script(tmp_path, "wrk", FAKE_WRK)

    with pytest.raises(AiQaError) as excinfo:
        _executor(tmp_path, wrk).run(
            run_id="run_1",
            target_url="http://127.0.0.1:8080/",
            benchmark=_benchmark(duration_seconds=11),
            artifact_store=FileArtifactStore(tmp_path / "artifacts"),
        )

    assert excinfo.value.code is ErrorCode.POLICY_VIOLATION


def test_timeout_maps_to_tool_timeout(tmp_path: Path) -> None:
    wrk = _script(tmp_path, "wrk", SLOW_WRK)

    with pytest.raises(AiQaError) as excinfo:
        _executor(tmp_path, wrk).run(
            run_id="run_1",
            target_url="http://127.0.0.1:8080/",
            benchmark=_benchmark(duration_seconds=1),
            artifact_store=FileArtifactStore(tmp_path / "artifacts"),
        )

    assert excinfo.value.code is ErrorCode.TOOL_TIMEOUT


def test_nonzero_exit_maps_to_tool_error(tmp_path: Path) -> None:
    wrk = _script(tmp_path, "wrk", FAIL_WRK)

    with pytest.raises(AiQaError) as excinfo:
        _executor(tmp_path, wrk).run(
            run_id="run_1",
            target_url="http://127.0.0.1:8080/",
            benchmark=_benchmark(),
            artifact_store=FileArtifactStore(tmp_path / "artifacts"),
        )

    assert excinfo.value.code is ErrorCode.MCP_TOOL_ERROR
    assert excinfo.value.details["exit_code"] == 2


ENV_WRK = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "wrk 4.2.0"; exit 0; fi
echo "DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-unset}"
"""


def test_child_process_does_not_inherit_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrk = _script(tmp_path, "wrk", ENV_WRK)
    store = FileArtifactStore(tmp_path / "artifacts")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-do-not-leak")

    perf = _executor(tmp_path, wrk).run(
        run_id="run_1",
        target_url="http://127.0.0.1:8080/",
        benchmark=_benchmark(),
        artifact_store=store,
    )

    stdout = (tmp_path / "artifacts" / perf.raw_stdout.uri).read_text(encoding="utf-8")
    assert "sk-do-not-leak" not in stdout
    assert "DEEPSEEK_API_KEY=unset" in stdout
