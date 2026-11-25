from __future__ import annotations

import asyncio
import json

import pytest

from ai_qa.agent.tool_agent import ToolAgent
from ai_qa.application.ports import (
    McpCallResult,
    ToolCall,
    ToolDecision,
    ToolDescriptor,
    ToolExchange,
)
from ai_qa.domain.enums import (
    ExecutionStatus,
    PlanMode,
    PlanStatus,
)
from ai_qa.domain.enums import (
    TestStatus as Status,
)
from ai_qa.domain.errors import AiQaError, ErrorCode, ErrorInfo
from ai_qa.domain.models import TestRun as RunModel
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
from ai_qa.domain.results import (
    CaseResult,
    RunResult,
    ToolCaseSummary,
    ToolFailureResult,
    ToolResult,
)

TARGET = "cpp-webserver-local"


class ScriptedChat:
    def __init__(
        self,
        decisions: list[ToolDecision],
        summaries: list[str] | None = None,
    ) -> None:
        self.decisions = list(decisions)
        self.summaries = list(summaries or ["总结：整体 PASS。"])
        self.decision_calls = 0

    async def decide(
        self,
        *,
        system: str,
        user: str,
        tools: list[ToolDescriptor],
        history: list[ToolExchange],
    ) -> ToolDecision:
        self.decision_calls += 1
        if not self.decisions:
            return ToolDecision()
        return self.decisions.pop(0)

    async def complete(self, *, system: str, user: str) -> str:
        if not self.summaries:
            return ""
        return self.summaries.pop(0)


class FakeGateway:
    def __init__(
        self,
        *,
        tool_result: ToolResult,
        run_result: RunResult,
        tool_is_error: bool = False,
        tool_error: ToolFailureResult | None = None,
    ) -> None:
        self._tool_result = tool_result
        self._run_result = run_result
        self._tool_is_error = tool_is_error
        self._tool_error = tool_error
        self.calls: list[ToolCall] = []

    async def list_tools(self) -> list[ToolDescriptor]:
        return []

    async def call_tool(
        self, call: ToolCall, *, timeout_seconds: float | None = None
    ) -> McpCallResult:
        self.calls.append(call)
        if call.name == "run_http_cases":
            if self._tool_is_error and self._tool_error is not None:
                payload = self._tool_error.model_dump(mode="json")
                return McpCallResult(
                    tool=call.name,
                    is_error=True,
                    structured=payload,
                    text=json.dumps(payload, ensure_ascii=False),
                )
            payload = self._tool_result.model_dump(mode="json")
        else:
            payload = self._run_result.model_dump(mode="json")
        return McpCallResult(
            tool=call.name,
            is_error=False,
            structured=payload,
            text=json.dumps(payload, ensure_ascii=False),
        )


def _run_and_plan() -> tuple[RunModel, StoredPlan]:
    run = new_test_run(plan_id="plan_1", target_id=TARGET, mode=PlanMode.AGENT_PLAN)
    plan = PlanModel(
        schema_version="1.0",
        goal="smoke",
        target_id=TARGET,
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
    stored = StoredPlan(
        plan_id="plan_1",
        plan_hash="sha256:" + "0" * 64,
        status=PlanStatus.VALIDATED,
        mode=PlanMode.AGENT_PLAN,
        plan=plan,
        items=[item],
        limits=RunLimits(),
        benchmark=None,
        created_at=utc_now(),
    )
    return run, stored


def _tool_result(run: RunModel) -> ToolResult:
    case = CaseResult(
        case_id="http.get_index",
        execution_status=ExecutionStatus.SUCCEEDED,
        test_status=Status.PASS,
    )
    return ToolResult(
        call_id="call_1",
        run_id=run.run_id,
        tool="run_http_cases",
        execution_status=ExecutionStatus.SUCCEEDED,
        test_status=Status.PASS,
        case_results=[
            ToolCaseSummary(
                case_id=case.case_id,
                execution_status=case.execution_status,
                test_status=case.test_status,
                assertions=case.assertions,
            )
        ],
    )


def _run_result(run: RunModel) -> RunResult:
    return RunResult(run=run, case_results=[], artifacts=[], perf=None)


def _agent(
    chat: ScriptedChat,
    gateway: FakeGateway,
    *,
    max_calls: int = 4,
) -> ToolAgent:
    return ToolAgent(
        chat=chat,
        summarizer=chat,
        gateway=gateway,
        max_tool_calls=max_calls,
    )


def _call_run() -> ToolCall:
    return ToolCall(name="run_http_cases", arguments={"run_id": "evil"})


def _call_get() -> ToolCall:
    return ToolCall(name="get_run_result")


def test_happy_path_injects_ids_and_summarizes() -> None:
    run, plan = _run_and_plan()
    chat = ScriptedChat(
        [
            ToolDecision(calls=[_call_run()]),
            ToolDecision(calls=[_call_get()]),
            ToolDecision(),
        ]
    )
    gateway = FakeGateway(tool_result=_tool_result(run), run_result=_run_result(run))

    tool_result, run_result, history, summary = asyncio.run(
        _agent(chat, gateway).execute(run=run, plan=plan)
    )

    assert tool_result.test_status is Status.PASS
    assert run_result.run.run_id == run.run_id
    assert summary.startswith("总结")
    assert [call.name for call in gateway.calls] == [
        "run_http_cases",
        "get_run_result",
    ]
    assert gateway.calls[0].arguments == {
        "run_id": run.run_id,
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
    }
    assert len(history) == 2


def test_disallowed_tool_rejected() -> None:
    run, plan = _run_and_plan()
    chat = ScriptedChat([ToolDecision(calls=[ToolCall(name="shell")])])

    with pytest.raises(AiQaError) as excinfo:
        asyncio.run(
            _agent(
                chat,
                FakeGateway(
                    tool_result=_tool_result(run),
                    run_result=_run_result(run),
                ),
            ).execute(run=run, plan=plan)
        )

    assert excinfo.value.code is ErrorCode.LLM_OUTPUT_INVALID


def test_duplicate_run_http_cases_rejected() -> None:
    run, plan = _run_and_plan()
    chat = ScriptedChat(
        [
            ToolDecision(calls=[_call_run()]),
            ToolDecision(calls=[_call_run()]),
        ]
    )

    with pytest.raises(AiQaError) as excinfo:
        asyncio.run(
            _agent(
                chat,
                FakeGateway(
                    tool_result=_tool_result(run),
                    run_result=_run_result(run),
                ),
            ).execute(run=run, plan=plan)
        )

    assert excinfo.value.code is ErrorCode.LLM_OUTPUT_INVALID
    assert "重复" in excinfo.value.message


def test_tool_call_limit_enforced() -> None:
    run, plan = _run_and_plan()
    chat = ScriptedChat([ToolDecision(calls=[_call_get(), _call_get(), _call_get()])])

    with pytest.raises(AiQaError) as excinfo:
        asyncio.run(
            _agent(
                chat,
                FakeGateway(
                    tool_result=_tool_result(run),
                    run_result=_run_result(run),
                ),
                max_calls=2,
            ).execute(run=run, plan=plan)
        )

    assert excinfo.value.code is ErrorCode.LLM_OUTPUT_INVALID
    assert "上限" in excinfo.value.message


def test_missing_run_tool_rejected() -> None:
    run, plan = _run_and_plan()
    chat = ScriptedChat([ToolDecision(calls=[_call_get()]), ToolDecision()])

    with pytest.raises(AiQaError) as excinfo:
        asyncio.run(
            _agent(
                chat,
                FakeGateway(
                    tool_result=_tool_result(run),
                    run_result=_run_result(run),
                ),
            ).execute(run=run, plan=plan)
        )

    assert excinfo.value.code is ErrorCode.LLM_OUTPUT_INVALID
    assert "run_http_cases" in excinfo.value.message


def test_tool_error_is_propagated_as_error() -> None:
    run, plan = _run_and_plan()
    chat = ScriptedChat([ToolDecision(calls=[_call_run()])])
    failure = ToolFailureResult(
        tool="run_http_cases",
        run_id=run.run_id,
        error=ErrorInfo(code=ErrorCode.MCP_TOOL_ERROR, message="boom"),
    )

    with pytest.raises(AiQaError) as excinfo:
        asyncio.run(
            _agent(
                chat,
                FakeGateway(
                    tool_result=_tool_result(run),
                    run_result=_run_result(run),
                    tool_is_error=True,
                    tool_error=failure,
                ),
            ).execute(run=run, plan=plan)
        )

    assert excinfo.value.code is ErrorCode.MCP_TOOL_ERROR


def test_empty_summary_rejected() -> None:
    run, plan = _run_and_plan()
    chat = ScriptedChat([ToolDecision(calls=[_call_run(), _call_get()])], summaries=[""])

    with pytest.raises(AiQaError) as excinfo:
        asyncio.run(
            _agent(
                chat,
                FakeGateway(
                    tool_result=_tool_result(run),
                    run_result=_run_result(run),
                ),
            ).execute(run=run, plan=plan)
        )

    assert excinfo.value.code is ErrorCode.LLM_OUTPUT_INVALID
