from __future__ import annotations

from typing import Any

import pytest

from ai_qa.application.execution_service import ExecutionService
from ai_qa.application.ports import ExecutionContext
from ai_qa.domain.enums import ExecutionStatus, PlanMode, PlanStatus
from ai_qa.domain.enums import TestStatus as Status
from ai_qa.domain.models import new_test_run, utc_now
from ai_qa.domain.plans import (
    PlanItem,
    ResolvedPlanItem,
    RunLimits,
    StoredPlan,
)
from ai_qa.domain.plans import (
    TestPlan as PlanModel,
)
from ai_qa.domain.results import ArtifactRef, CaseFacts, CaseResult
from ai_qa.execution.registry import ExecutorRegistry


class FakeTarget:
    host = "127.0.0.1"
    port = 18080
    health_path = "/healthz"

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def start(self) -> None:
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")


class FakeExecutor:
    kind = "http"

    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    def execute(self, item: ResolvedPlanItem, context: ExecutionContext) -> CaseFacts:
        self.events.append(f"exec:{item.case_id}")
        if self.fail:
            raise RuntimeError("executor exploded")
        now = utc_now()
        return CaseFacts(
            case_id=item.case_id,
            started_at=now,
            ended_at=now,
            observed={"status": 200},
        )


class FakeEvaluator:
    def evaluate_case(self, item: ResolvedPlanItem, facts: CaseFacts) -> CaseResult:
        return CaseResult(
            case_id=item.case_id,
            execution_status=ExecutionStatus.SUCCEEDED,
            test_status=Status.PASS,
            facts=facts,
        )


class RecordingResults:
    def __init__(self) -> None:
        self.saved: list[tuple[str, CaseResult]] = []

    def save_case_result(self, run_id: str, result: CaseResult) -> None:
        self.saved.append((run_id, result))

    def list_case_results(self, run_id: str) -> list[CaseResult]:
        return [result for saved_run, result in self.saved if saved_run == run_id]


class NullArtifactStore:
    def write_bytes(
        self, run_id: str, name: str, data: bytes, kind: str, *, mime: str | None = None
    ) -> ArtifactRef:
        raise AssertionError("executor 未使用 artifact store")

    def write_text(
        self, run_id: str, name: str, text: str, kind: str, *, mime: str | None = None
    ) -> ArtifactRef:
        raise AssertionError("executor 未使用 artifact store")


def _plan(plan_id: str = "plan_1") -> StoredPlan:
    plan = PlanModel(
        schema_version="1.0",
        goal="smoke",
        target_id="target",
        items=[PlanItem(case_id="http.get_index", params={})],
        benchmark=None,
        limits=RunLimits(),
    )
    item = ResolvedPlanItem(
        case_id="http.get_index",
        sequence_no=1,
        executor="http",
        rule_set="http.get_index",
        timeout_seconds=5,
        artifact_policy="http_exchange",
        description="",
        params={"path": "/"},
        refs=[],
    )
    return StoredPlan(
        plan_id=plan_id,
        plan_hash="sha256:" + "0" * 64,
        status=PlanStatus.VALIDATED,
        mode=PlanMode.FIXED_PLAN,
        plan=plan,
        items=[item],
        limits=RunLimits(),
        benchmark=None,
        created_at=utc_now(),
    )


def _service(events: list[str], **kwargs: Any) -> ExecutionService:
    executor = kwargs.pop("executor", FakeExecutor(events))
    return ExecutionService(
        target=FakeTarget(events),
        registry=ExecutorRegistry([executor]),
        evaluator=FakeEvaluator(),
        artifact_store=NullArtifactStore(),
        **kwargs,
    )


def test_executes_plan_and_stops_target() -> None:
    events: list[str] = []
    results_repo = RecordingResults()
    plan = _plan()
    run = new_test_run(plan_id=plan.plan_id, target_id="target", mode=PlanMode.FIXED_PLAN)
    service = _service(events, result_repository=results_repo)

    results = service.execute_plan(run, plan)

    assert events == ["start", "exec:http.get_index", "stop"]
    assert [result.case_id for result in results] == ["http.get_index"]
    assert results_repo.saved == [(run.run_id, results[0])]


def test_target_stopped_when_executor_raises() -> None:
    events: list[str] = []
    plan = _plan()
    run = new_test_run(plan_id=plan.plan_id, target_id="target", mode=PlanMode.FIXED_PLAN)
    service = _service(events, executor=FakeExecutor(events, fail=True))

    with pytest.raises(RuntimeError):
        service.execute_plan(run, plan)

    assert events == ["start", "exec:http.get_index", "stop"]


def test_plan_mismatch_rejected_before_start() -> None:
    events: list[str] = []
    run = new_test_run(plan_id="other_plan", target_id="target", mode=PlanMode.FIXED_PLAN)
    service = _service(events)

    with pytest.raises(ValueError):
        service.execute_plan(run, _plan())

    assert events == []
