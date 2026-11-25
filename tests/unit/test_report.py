from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ai_qa.bootstrap import AppContext, build_app_context
from ai_qa.domain.enums import (
    ExecutionStatus,
    PlanMode,
    RunState,
)
from ai_qa.domain.enums import (
    TestStatus as Status,
)
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.models import new_test_run, utc_now
from ai_qa.domain.results import (
    ArtifactRef,
    AssertionResult,
    CaseFacts,
    CaseResult,
    PerfResult,
)
from ai_qa.report.markdown import ReportService
from ai_qa.settings import load_settings
from ai_qa.validation.policy import policy_from_settings
from ai_qa.validation.validator import PlanValidator, ValidationContext

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET = "cpp-webserver-local"


@pytest.fixture
def report_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppContext:
    data = yaml.safe_load(
        (REPO_ROOT / "config" / "settings.example.yaml").read_text(encoding="utf-8")
    )
    data["project"]["repo_root"] = str(tmp_path)
    data["target"]["executable_path"] = str(tmp_path / "web_server")
    data["target"]["working_directory"] = str(tmp_path)
    data["target"]["www_root"] = str(tmp_path / "www")
    (tmp_path / "www").mkdir()
    config = tmp_path / "settings.yaml"
    config.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "ai_qa.db"))
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AI_QA_CATALOG", str(REPO_ROOT / "config" / "cases" / "http.yaml"))
    context = build_app_context(config)
    yield context
    context.close()


def _plan_and_run(context: AppContext):  # type: ignore[no-untyped-def]
    settings = load_settings(context.paths.config).settings
    policy = policy_from_settings(settings, allow_benchmark=True)
    plan_data = {
        "schema_version": "1.0",
        "goal": "report smoke",
        "target_id": TARGET,
        "items": [{"case_id": "http.get_index", "params": {}}],
        "benchmark": {
            "enabled": True,
            "tool": "wrk",
            "path": "/",
            "duration_seconds": 5,
            "connections": 10,
            "threads": 2,
        },
        "limits": {"max_cases": 6, "run_timeout_seconds": 120},
    }
    result = PlanValidator(context.catalog, policy).validate(
        plan_data, ValidationContext(mode=PlanMode.FIXED_PLAN)
    )
    assert result.plan is not None
    plan = result.plan
    context.plan_repository.save_plan(plan)
    run = new_test_run(plan_id=plan.plan_id, target_id=TARGET, mode=PlanMode.FIXED_PLAN)
    run.state = RunState.COMPLETED
    run.execution_status = ExecutionStatus.SUCCEEDED
    run.test_status = Status.FAIL
    run.updated_at = utc_now()
    context.run_repository.create_run(run)
    return plan, run


def _artifact(context: AppContext, run_id: str, name: str, text: str) -> ArtifactRef:
    ref = context.artifact_store.write_text(run_id, name, text, "case_log")
    context.artifact_repository.save_artifact(ref)
    return ref


def test_generates_report_and_results(report_context: AppContext) -> None:
    context = report_context
    _, run = _plan_and_run(context)
    artifact = _artifact(context, run.run_id, "cases/http_get_index.txt", "ok")
    now = utc_now()
    facts = CaseFacts(
        case_id="http.get_index",
        started_at=now,
        ended_at=now,
        artifacts=[artifact],
    )
    case = CaseResult(
        case_id="http.get_index",
        execution_status=ExecutionStatus.SUCCEEDED,
        test_status=Status.FAIL,
        facts=facts,
        assertions=[
            AssertionResult(
                rule_id="http.status",
                name="status == 200",
                status=Status.FAIL,
                expected=200,
                observed=500,
                message="unexpected",
                evidence_refs=[artifact.artifact_id],
            )
        ],
    )
    context.result_repository.save_case_result(run.run_id, case)

    stdout = _artifact(context, run.run_id, "benchmark/wrk.txt", "raw wrk")
    metrics = _artifact(context, run.run_id, "benchmark/metrics.json", "{}")
    perf = PerfResult(
        perf_result_id="perf_1",
        run_id=run.run_id,
        tool="wrk",
        tool_version="wrk 4.2.0",
        command=["wrk"],
        target_url="http://127.0.0.1:18080/",
        config={"duration_seconds": 5},
        metrics={"requests_per_sec": 1000.0},
        environment={"os": "linux"},
        exit_code=0,
        raw_stdout=stdout,
        metrics_artifact=metrics,
        test_status=Status.INCONCLUSIVE,
        notes=["无基线"],
        created_at=now,
    )
    context.perf_repository.save_perf_result(perf)
    summary = _artifact(context, run.run_id, "summary.md", "## 总结\n这是草稿。")

    report = ReportService(context).generate(run.run_id)

    text = (context.paths.artifacts / report.report.uri).read_text(encoding="utf-8")
    assert "AI QA 测试报告" in text
    assert "http.get_index" in text
    assert "FAIL" in text
    assert "INCONCLUSIVE" in text
    assert "这是草稿" in text
    assert "benchmark/wrk.txt" in text
    assert report.payload["overall_verdict"] == "FAIL"
    assert report.payload["perf"]["metrics"]["requests_per_sec"] == 1000.0
    results = json.loads((context.paths.artifacts / report.results.uri).read_text(encoding="utf-8"))
    assert results["run"]["run_id"] == run.run_id
    assert results["summary"] == "## 总结\n这是草稿。"
    assert summary.artifact_id in [a["artifact_id"] for a in report.payload["artifacts"]]


def test_report_write_failure_maps_to_report_error(report_context: AppContext) -> None:
    context = report_context
    _, run = _plan_and_run(context)
    run2 = new_test_run(plan_id=run.plan_id, target_id=TARGET, mode=PlanMode.FIXED_PLAN)
    run2.state = RunState.COMPLETED
    run2.updated_at = utc_now()
    context.run_repository.create_run(run2)
    (context.paths.artifacts / run2.run_id).write_text("not a dir", encoding="utf-8")

    with pytest.raises(AiQaError) as excinfo:
        ReportService(context).generate(run2.run_id)

    assert excinfo.value.code is ErrorCode.REPORT_GENERATION_FAILED


def test_missing_run_is_config_error(report_context: AppContext) -> None:
    with pytest.raises(AiQaError):
        report_context.run_repository.get_run("run_missing")
