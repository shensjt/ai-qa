from __future__ import annotations

from ai_qa.domain.enums import ExecutionStatus, PlanMode, RunState
from ai_qa.domain.enums import TestStatus as Status
from ai_qa.domain.models import new_test_run, utc_now
from ai_qa.domain.results import (
    AssertionResult,
    CaseResult,
    ToolCaseSummary,
    ToolResult,
)


def _case_result() -> CaseResult:
    return CaseResult(
        case_id="http.get_index",
        execution_status=ExecutionStatus.SUCCEEDED,
        test_status=Status.FAIL,
        assertions=[
            AssertionResult(
                rule_id="http.status",
                name="status is 200",
                status=Status.FAIL,
                expected=200,
                observed=500,
                message="unexpected status",
                evidence_refs=["art_1"],
            )
        ],
    )


def test_case_result_keeps_execution_and_test_status_separate() -> None:
    result = _case_result()

    assert result.execution_status is ExecutionStatus.SUCCEEDED
    assert result.test_status is Status.FAIL


def test_tool_result_serializes_summaries() -> None:
    case = _case_result()
    tool_result = ToolResult(
        call_id="call_1",
        run_id="run_1",
        tool="run_http_cases",
        execution_status=ExecutionStatus.SUCCEEDED,
        test_status=Status.FAIL,
        facts={"target": {"host": "127.0.0.1"}},
        case_results=[
            ToolCaseSummary(
                case_id=case.case_id,
                execution_status=case.execution_status,
                test_status=case.test_status,
                assertions=case.assertions,
            )
        ],
    )

    payload = tool_result.model_dump(mode="json")

    assert payload["tool"] == "run_http_cases"
    assert payload["case_results"][0]["case_id"] == "http.get_index"
    assert payload["case_results"][0]["assertions"][0]["evidence_refs"] == ["art_1"]


def test_new_run_starts_created_with_utc_timestamps() -> None:
    run = new_test_run(plan_id="plan_1", target_id="cpp-webserver-local", mode=PlanMode.FIXED_PLAN)

    assert run.run_id.startswith("run_")
    assert run.state is RunState.CREATED
    assert run.created_at.tzinfo is not None
    assert run.created_at == run.updated_at
    assert utc_now().tzinfo is not None
