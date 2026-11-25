"""固定计划 E2E：校验 → 持久化 → 真实 MCP Server 执行 → 只读回读。"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ai_qa.adapters.mcp_client import McpServerLaunchConfig, McpStdioGateway
from ai_qa.application.ports import ToolCall
from ai_qa.bootstrap import AppContext
from ai_qa.domain.enums import PlanMode, RunState
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.models import TestRun, new_test_run, utc_now
from ai_qa.domain.plans import StoredPlan
from ai_qa.domain.results import RunResult, ToolFailureResult, ToolResult
from ai_qa.validation.policy import policy_from_settings
from ai_qa.validation.validator import (
    PlanValidationError,
    PlanValidator,
    ValidationContext,
)


@dataclass(frozen=True)
class FixedPlanExecution:
    run: TestRun
    tool_result: ToolResult
    run_result: RunResult
    trace_path: Path


class FixedPlanRunner:
    def __init__(self, context: AppContext, *, allow_benchmark: bool = False) -> None:
        self._context = context
        self._allow_benchmark = allow_benchmark

    async def execute(self, plan_path: Path, *, target_id: str | None = None) -> FixedPlanExecution:
        context = self._context
        plan = self._validate_plan(plan_path)
        configured_target = context.settings.target.target_id
        if target_id is not None and target_id != configured_target:
            raise AiQaError(
                ErrorCode.TARGET_NOT_FOUND,
                f"--target {target_id} 与配置的 target_id {configured_target} 不一致",
                details={
                    "requested": target_id,
                    "configured": configured_target,
                },
            )

        context.plan_repository.save_plan(plan)
        run = new_test_run(
            plan_id=plan.plan_id,
            target_id=configured_target,
            mode=PlanMode.FIXED_PLAN,
            environment={
                "python": sys.version.split()[0],
                "config": str(context.paths.config),
                "catalog": str(context.paths.catalog),
            },
        )
        run.state = RunState.VALIDATED
        run.updated_at = utc_now()
        context.run_repository.create_run(run)

        trace_path = context.paths.artifacts / run.run_id / "trace.jsonl"
        launch = McpServerLaunchConfig(
            command=sys.executable,
            args=("-m", "ai_qa.mcp_server"),
            env=context.paths.server_env(),
        )

        async with McpStdioGateway(launch, trace_path) as gateway:
            descriptors = await gateway.list_tools()
            tool_names = {descriptor.name for descriptor in descriptors}
            missing = {"run_http_cases", "get_run_result"} - tool_names
            if missing:
                raise AiQaError(
                    ErrorCode.MCP_PROTOCOL_ERROR,
                    f"MCP Server 缺少必需工具: {sorted(missing)}",
                )

            response = await gateway.call_tool(
                ToolCall(
                    name="run_http_cases",
                    arguments={
                        "run_id": run.run_id,
                        "plan_id": plan.plan_id,
                        "plan_hash": plan.plan_hash,
                    },
                ),
                timeout_seconds=float(plan.limits.run_timeout_seconds) + 60.0,
            )
            tool_result = _parse_tool_result(response.structured, response.is_error)

            readback = await gateway.call_tool(
                ToolCall(name="get_run_result", arguments={"run_id": run.run_id}),
                timeout_seconds=60.0,
            )
            run_result = _parse_run_result(readback.structured, readback.is_error)

        return FixedPlanExecution(
            run=run,
            tool_result=tool_result,
            run_result=run_result,
            trace_path=trace_path,
        )

    def _validate_plan(self, path: Path) -> StoredPlan:
        if not path.is_file():
            raise AiQaError(
                ErrorCode.CONFIG_INVALID,
                f"计划文件不存在: {path}",
                details={"path": str(path)},
            )
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AiQaError(
                ErrorCode.CONFIG_INVALID,
                f"无法读取计划文件: {path}",
                details={"path": str(path)},
            ) from exc
        try:
            raw: Any = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AiQaError(
                ErrorCode.PLAN_JSON_INVALID,
                f"计划 JSON 解析失败: {exc}",
                details={"path": str(path)},
            ) from exc

        policy = policy_from_settings(self._context.settings, allow_benchmark=self._allow_benchmark)
        result = PlanValidator(self._context.catalog, policy).validate(
            raw, ValidationContext(mode=PlanMode.FIXED_PLAN)
        )
        if not result.ok:
            raise PlanValidationError(result.issues)
        if result.plan is None:
            raise PlanValidationError(result.issues)
        return result.plan


def _parse_tool_result(structured: dict[str, Any] | None, is_error: bool) -> ToolResult:
    if structured is None:
        raise AiQaError(ErrorCode.MCP_PROTOCOL_ERROR, "MCP 返回缺少 structuredContent")
    try:
        if is_error:
            failure = ToolFailureResult.model_validate(structured)
            raise AiQaError(
                failure.error.code,
                failure.error.message,
                retryable=failure.error.retryable,
                details=failure.error.details,
            )
        return ToolResult.model_validate(structured)
    except ValidationError as exc:
        raise AiQaError(
            ErrorCode.MCP_PROTOCOL_ERROR,
            "structuredContent 不符合 ToolResult 契约",
            details={"error": str(exc)[:300]},
        ) from exc


def _parse_run_result(structured: dict[str, Any] | None, is_error: bool) -> RunResult:
    if structured is None:
        raise AiQaError(ErrorCode.MCP_PROTOCOL_ERROR, "get_run_result 缺少 structuredContent")
    try:
        if is_error:
            failure = ToolFailureResult.model_validate(structured)
            raise AiQaError(
                failure.error.code,
                failure.error.message,
                retryable=failure.error.retryable,
                details=failure.error.details,
            )
        return RunResult.model_validate(structured)
    except ValidationError as exc:
        raise AiQaError(
            ErrorCode.MCP_PROTOCOL_ERROR,
            "get_run_result 返回不符合 RunResult 契约",
            details={"error": str(exc)[:300]},
        ) from exc
