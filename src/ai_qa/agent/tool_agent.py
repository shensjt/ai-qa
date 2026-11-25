"""受约束的工具调用循环（实现规格 §11.1 / §17.1）。

模型只能选择工具，不能提供 run_id/plan_id/plan_hash；这些参数由编排层注入。
"""

from __future__ import annotations

import json

from ai_qa.agent.prompts import SUMMARY_SYSTEM, TOOL_SYSTEM, summary_user_prompt, tool_user_prompt
from ai_qa.application.mcp_payloads import parse_run_result, parse_tool_result
from ai_qa.application.ports import (
    ChatPlanner,
    McpToolGateway,
    ToolCall,
    ToolCallingChat,
    ToolDescriptor,
    ToolExchange,
)
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.models import TestRun, new_id
from ai_qa.domain.plans import StoredPlan
from ai_qa.domain.results import RunResult, ToolResult

_ALLOWED_TOOLS = {"run_http_cases", "get_run_result"}

_TOOL_DESCRIPTORS = [
    ToolDescriptor(
        name="run_http_cases",
        description="执行已校验计划中的 HTTP 用例。无需参数，禁止重复调用。",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    ToolDescriptor(
        name="get_run_result",
        description="只读获取当前 run 的已持久化结果与 Artifact 引用。无需参数。",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
]


class ToolAgent:
    def __init__(
        self,
        *,
        chat: ToolCallingChat,
        summarizer: ChatPlanner,
        gateway: McpToolGateway,
        max_tool_calls: int,
        max_result_chars: int = 8000,
    ) -> None:
        self._chat = chat
        self._summarizer = summarizer
        self._gateway = gateway
        self._max_tool_calls = max_tool_calls
        self._max_result_chars = max_result_chars

    async def execute(
        self, *, run: TestRun, plan: StoredPlan
    ) -> tuple[ToolResult, RunResult, list[ToolExchange], str]:
        user_prompt = tool_user_prompt(
            goal=plan.plan.goal,
            items=[
                {
                    "case_id": item.case_id,
                    "executor": item.executor,
                    "timeout_seconds": item.timeout_seconds,
                }
                for item in plan.items
            ],
        )
        history: list[ToolExchange] = []
        tool_result: ToolResult | None = None
        run_result: RunResult | None = None

        while len(history) < self._max_tool_calls:
            decision = await self._chat.decide(
                system=TOOL_SYSTEM,
                user=user_prompt,
                tools=_TOOL_DESCRIPTORS,
                history=history,
            )
            if not decision.calls:
                break
            for call in decision.calls:
                if len(history) >= self._max_tool_calls:
                    raise AiQaError(
                        ErrorCode.LLM_OUTPUT_INVALID,
                        "模型工具调用次数超过上限",
                        details={"max_tool_calls": self._max_tool_calls},
                    )
                if call.name not in _ALLOWED_TOOLS:
                    raise AiQaError(
                        ErrorCode.LLM_OUTPUT_INVALID,
                        f"模型请求了未授权的工具: {call.name}",
                    )
                if call.name == "run_http_cases" and tool_result is not None:
                    raise AiQaError(
                        ErrorCode.LLM_OUTPUT_INVALID,
                        "模型重复调用 run_http_cases",
                    )

                result = await self._gateway.call_tool(
                    ToolCall(
                        name=call.name,
                        arguments=self._gateway_arguments(call.name, run, plan),
                    ),
                    timeout_seconds=float(plan.limits.run_timeout_seconds) + 60.0,
                )
                if call.name == "run_http_cases":
                    tool_result = parse_tool_result(result.structured, result.is_error)
                else:
                    run_result = parse_run_result(result.structured, result.is_error)
                history.append(
                    ToolExchange(
                        call_id=call.call_id or new_id("call"),
                        tool=call.name,
                        arguments={},
                        result_text=self._result_text(result.structured, result.text),
                    )
                )
                if tool_result is not None and run_result is not None:
                    break
            if tool_result is not None and run_result is not None:
                break

        if tool_result is None:
            raise AiQaError(
                ErrorCode.LLM_OUTPUT_INVALID,
                "模型未调用 run_http_cases，拒绝生成总结",
                details={"tool_calls": len(history)},
            )
        if run_result is None:
            readback = await self._gateway.call_tool(
                ToolCall(name="get_run_result", arguments={"run_id": run.run_id}),
                timeout_seconds=60.0,
            )
            run_result = parse_run_result(readback.structured, readback.is_error)

        summary = await self._summarizer.complete(
            system=SUMMARY_SYSTEM,
            user=summary_user_prompt(
                goal=plan.plan.goal,
                tool_result=tool_result,
                run_result=run_result,
            ),
        )
        if not summary.strip():
            raise AiQaError(ErrorCode.LLM_OUTPUT_INVALID, "模型返回了空的总结草稿")
        return tool_result, run_result, history, summary

    @staticmethod
    def _gateway_arguments(name: str, run: TestRun, plan: StoredPlan) -> dict[str, str]:
        if name == "run_http_cases":
            return {
                "run_id": run.run_id,
                "plan_id": plan.plan_id,
                "plan_hash": plan.plan_hash,
            }
        return {"run_id": run.run_id}

    def _result_text(self, structured: dict[str, object] | None, text: str) -> str:
        raw = json.dumps(structured, ensure_ascii=False) if structured is not None else text
        return raw[: self._max_result_chars]
