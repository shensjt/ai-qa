from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from ai_qa.application.execution_service import ExecutionService
from ai_qa.artifacts.store import FileArtifactStore
from ai_qa.domain.enums import ExecutionStatus, PlanMode
from ai_qa.domain.enums import TestStatus as Status
from ai_qa.domain.models import new_test_run
from ai_qa.execution.http_executor import HttpExecutor
from ai_qa.execution.registry import ExecutorRegistry
from ai_qa.execution.target_manager import TargetConfig, TargetManager
from ai_qa.rules.evaluator import RuleEvaluatorImpl
from ai_qa.validation.catalog import load_catalog
from ai_qa.validation.policy import ValidationPolicy
from ai_qa.validation.validator import PlanValidator, ValidationContext

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_SERVER = Path(os.environ.get("AI_QA_CPP_SERVER", "/home/cpp/CPP/build/server/web_server"))
TARGET_ID = "cpp-webserver-local"
SECRET_MARKER = "TOP-SECRET-MARKER-12345"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _policy() -> ValidationPolicy:
    return ValidationPolicy(
        expected_target_id=TARGET_ID,
        max_cases=6,
        run_timeout_seconds=120,
        benchmark_allowed=False,
        benchmark_max_duration_seconds=30,
        benchmark_max_connections=64,
        benchmark_max_threads=4,
        max_plan_repairs=2,
    )


def _plan():  # type: ignore[no-untyped-def]
    catalog = load_catalog(REPO_ROOT / "config" / "cases" / "http.yaml")
    data = {
        "schema_version": "1.0",
        "goal": "phase2 real target",
        "target_id": TARGET_ID,
        "items": [
            {"case_id": "http.get_index", "params": {}},
            {"case_id": "http.head", "params": {}},
            {"case_id": "http.keep_alive", "params": {}},
            {"case_id": "http.pipelined", "params": {}},
            {"case_id": "http.split_request", "params": {"split_sizes": [1, 3, 7]}},
            {"case_id": "http.path_traversal", "params": {}},
        ],
        "benchmark": None,
        "limits": {"max_cases": 6, "run_timeout_seconds": 120},
    }
    result = PlanValidator(catalog, _policy()).validate(
        data, ValidationContext(mode=PlanMode.FIXED_PLAN)
    )
    assert result.plan is not None, [issue.model_dump() for issue in result.issues]
    return result.plan


@pytest.fixture
def website(tmp_path: Path) -> Path:
    if not WEB_SERVER.is_file():
        pytest.skip(f"真实 C++ WebServer 不存在: {WEB_SERVER}（先编译 CPP 仓库）")
    fixture_root = tmp_path / "fixture"
    www = fixture_root / "www"
    www.mkdir(parents=True)
    (www / "index.html").write_text(
        "<html><body>ai-qa-fixture-index</body></html>", encoding="utf-8"
    )
    (fixture_root / "outside_secret.txt").write_text(SECRET_MARKER, encoding="utf-8")
    return www


def test_real_webserver_six_cases(tmp_path: Path, website: Path) -> None:
    port = _free_port()
    target = TargetManager(
        TargetConfig(
            executable_path=WEB_SERVER,
            working_directory=tmp_path,
            www_root=website,
            host="127.0.0.1",
            port=port,
            threads=4,
            timeout_seconds=60,
            cache_bytes=16 * 1024 * 1024,
            health_path="/healthz",
            startup_timeout_seconds=10,
            stop_timeout_seconds=5.0,
            allowed_hosts=("127.0.0.1",),
            allowed_processes=(WEB_SERVER,),
        ),
        log_dir=tmp_path / "target-logs",
    )
    store = FileArtifactStore(tmp_path / "artifacts")
    service = ExecutionService(
        target=target,
        registry=ExecutorRegistry([HttpExecutor()]),
        evaluator=RuleEvaluatorImpl(),
        artifact_store=store,
    )
    plan = _plan()
    run = new_test_run(plan_id=plan.plan_id, target_id=TARGET_ID, mode=PlanMode.FIXED_PLAN)

    results = service.execute_plan(run, plan, secret_marker=SECRET_MARKER)

    by_case = {result.case_id: result for result in results}
    assert len(results) == 6
    assert by_case["http.get_index"].test_status is Status.PASS
    assert by_case["http.head"].test_status is Status.PASS
    assert by_case["http.keep_alive"].test_status is Status.PASS
    assert by_case["http.pipelined"].test_status is Status.PASS
    assert by_case["http.path_traversal"].test_status is Status.PASS

    # 已知真实缺陷：请求在请求行之后分片会返回 405。
    # 这里只验证用例确实执行并产生了事件，不断言具体 verdict，避免把上游 bug 固化进测试。
    split_result = by_case["http.split_request"]
    assert split_result.execution_status is ExecutionStatus.SUCCEEDED
    assert split_result.facts is not None
    assert "split" in split_result.facts.observed

    assert target.is_running is False
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
    assert list((tmp_path / "artifacts").rglob("*"))
