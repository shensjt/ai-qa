"""MCP 工具实现：服务端重新校验并调用 ExecutionService（规格 §12.2～§12.5）。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any

from ai_qa.bootstrap import AppContext, build_execution_service, detect_secret_marker
from ai_qa.domain.enums import (
    ExecutionStatus,
    PlanStatus,
    RunState,
    TestStatus,
    aggregate_test_status,
)
from ai_qa.domain.errors import AiQaError, ErrorCode, ErrorInfo
from ai_qa.domain.models import TestRun, new_id, utc_now
from ai_qa.domain.plans import StoredPlan, compute_plan_hash
from ai_qa.domain.results import (
    CaseResult,
    RunResult,
    ToolCaseSummary,
    ToolFailureResult,
    ToolResult,
)
from ai_qa.mcp_server.schemas import GetRunResultArgs, RunCasesArgs

LOGGER = logging.getLogger(__name__)


class QaMcpTools:
    def __init__(self, context: AppContext) -> None:
        self._context = context

    async def run_http_cases(self, args: RunCasesArgs) -> tuple[dict[str, Any], bool]:
        run: TestRun | None = None
        try:
            run, plan = self._load_verified_plan(args)
            self._set_run_state(run, RunState.RUNNING)
            service = build_execution_service(self._context)
            results = await asyncio.to_thread(
                service.execute_plan,
                run,
                plan,
                secret_marker=detect_secret_marker(self._context.settings),
            )
            tool_result = self._build_tool_result(run, results)
            run.state = RunState.COMPLETED
            run.execution_status = tool_result.execution_status
            run.test_status = tool_result.test_status
            run.updated_at = utc_now()
            self._context.run_repository.update_run(run)
            return tool_result.model_dump(mode="json"), False
        except AiQaError as exc:
            if run is not None:
                self._mark_run_failed(run, exc.code)
            return self._failure("run_http_cases", exc, run_id=args.run_id), True
        except Exception as exc:  # Server 不能因单次调用崩溃
            LOGGER.exception("unhandled error in run_http_cases")
            if run is not None:
                self._mark_run_failed(run, ErrorCode.INTERNAL_ERROR)
            error = ErrorInfo(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"{type(exc).__name__}: {exc}"[:300],
            )
            return (
                ToolFailureResult(
                    tool="run_http_cases", run_id=args.run_id, error=error
                ).model_dump(mode="json"),
                True,
            )

    async def run_http_benchmark(self, args: RunCasesArgs) -> tuple[dict[str, Any], bool]:
        try:
            run, plan = self._load_verified_plan(args)
            if plan.benchmark is None or not plan.benchmark.enabled:
                raise AiQaError(
                    ErrorCode.POLICY_VIOLATION,
                    "计划未包含已授权的 benchmark",
                    details={"run_id": run.run_id},
                )
            raise AiQaError(
                ErrorCode.MCP_TOOL_ERROR,
                "run_http_benchmark 尚未实现（Phase 6）",
                details={"run_id": run.run_id},
            )
        except AiQaError as exc:
            return self._failure("run_http_benchmark", exc, run_id=args.run_id), True

    async def get_run_result(self, args: GetRunResultArgs) -> tuple[dict[str, Any], bool]:
        try:
            run = self._context.run_repository.get_run(args.run_id)
        except AiQaError as exc:
            return self._failure("get_run_result", exc, run_id=args.run_id), True

        payload = RunResult(
            run=run,
            case_results=self._context.result_repository.list_case_results(run.run_id),
            artifacts=self._context.artifact_repository.list_artifacts(run.run_id),
            perf=None,
        )
        return payload.model_dump(mode="json"), False

    # ------------------------------------------------------------- helpers

    def _load_verified_plan(self, args: RunCasesArgs) -> tuple[TestRun, StoredPlan]:
        try:
            run = self._context.run_repository.get_run(args.run_id)
            plan = self._context.plan_repository.get_plan(args.plan_id)
        except AiQaError as exc:
            raise AiQaError(ErrorCode.MCP_TOOL_ERROR, exc.message, details=exc.details) from exc

        if run.plan_id != plan.plan_id:
            raise AiQaError(
                ErrorCode.MCP_TOOL_ERROR,
                "run 与 plan 不匹配",
                details={"run_plan_id": run.plan_id, "plan_id": plan.plan_id},
            )
        if plan.status is not PlanStatus.VALIDATED:
            raise AiQaError(
                ErrorCode.MCP_TOOL_ERROR,
                f"计划状态不是 VALIDATED: {plan.status}",
                details={"plan_id": plan.plan_id},
            )
        if run.state is not RunState.VALIDATED:
            raise AiQaError(
                ErrorCode.MCP_TOOL_ERROR,
                f"run 状态不允许执行: {run.state}",
                details={"run_id": run.run_id},
            )

        target_id = self._context.settings.target.target_id
        if run.target_id != target_id or plan.plan.target_id != target_id:
            raise AiQaError(
                ErrorCode.TARGET_NOT_FOUND,
                "计划或运行的 target_id 与当前配置不一致",
                details={
                    "configured": target_id,
                    "run": run.target_id,
                    "plan": plan.plan.target_id,
                },
            )

        recomputed = compute_plan_hash(plan.plan)
        if args.plan_hash != recomputed or plan.plan_hash != recomputed:
            raise AiQaError(
                ErrorCode.PLAN_HASH_MISMATCH,
                "plan_hash 校验失败",
                details={
                    "requested": args.plan_hash,
                    "stored": plan.plan_hash,
                    "recomputed": recomputed,
                },
            )

        runtime = self._context.settings.runtime
        if len(plan.items) > runtime.max_cases:
            raise AiQaError(
                ErrorCode.POLICY_VIOLATION,
                f"计划用例数 {len(plan.items)} 超过上限 {runtime.max_cases}",
            )
        if plan.limits.run_timeout_seconds > runtime.run_timeout_seconds:
            raise AiQaError(
                ErrorCode.POLICY_VIOLATION,
                "计划运行超时超过服务端上限",
                details={
                    "plan": plan.limits.run_timeout_seconds,
                    "policy": runtime.run_timeout_seconds,
                },
            )

        benchmark = plan.benchmark
        if benchmark is not None and benchmark.enabled:
            limits = self._context.settings.benchmark
            if (
                benchmark.duration_seconds > limits.max_duration_seconds
                or benchmark.connections > limits.max_connections
                or benchmark.threads > limits.max_threads
            ):
                raise AiQaError(
                    ErrorCode.POLICY_VIOLATION,
                    "计划压测参数超过服务端上限",
                )

        return run, plan

    def _set_run_state(self, run: TestRun, state: RunState) -> None:
        run.state = state
        run.updated_at = utc_now()
        self._context.run_repository.update_run(run)

    def _mark_run_failed(self, run: TestRun, code: ErrorCode) -> None:
        run.state = RunState.FAILED
        run.execution_status = (
            ExecutionStatus.TIMED_OUT
            if code in {ErrorCode.CASE_TIMEOUT, ErrorCode.TOOL_TIMEOUT}
            else ExecutionStatus.FAILED
        )
        run.test_status = TestStatus.ERROR
        run.updated_at = utc_now()
        try:
            self._context.run_repository.update_run(run)
        except AiQaError:
            LOGGER.exception("failed to persist failed run state")

    def _build_tool_result(self, run: TestRun, results: list[CaseResult]) -> ToolResult:
        artifacts = [
            artifact
            for result in results
            if result.facts is not None
            for artifact in result.facts.artifacts
        ]
        summaries = [
            ToolCaseSummary(
                case_id=result.case_id,
                execution_status=result.execution_status,
                test_status=result.test_status,
                assertions=result.assertions,
            )
            for result in results
        ]
        return ToolResult(
            call_id=new_id("call"),
            run_id=run.run_id,
            tool="run_http_cases",
            execution_status=_aggregate_execution_status(
                result.execution_status for result in results
            ),
            test_status=aggregate_test_status(result.test_status for result in results),
            facts={
                "target": {
                    "host": self._context.settings.target.host,
                    "port": self._context.settings.target.port,
                },
                "case_fact_refs": [artifact.artifact_id for artifact in artifacts],
            },
            case_results=summaries,
            artifacts=artifacts,
            error=None,
        )

    @staticmethod
    def _failure(tool: str, exc: AiQaError, *, run_id: str | None = None) -> dict[str, Any]:
        return ToolFailureResult(tool=tool, run_id=run_id, error=exc.to_error_info()).model_dump(
            mode="json"
        )


def _aggregate_execution_status(
    statuses: Iterable[ExecutionStatus],
) -> ExecutionStatus:
    values = list(statuses)
    if not values:
        return ExecutionStatus.FAILED
    if all(status is ExecutionStatus.SUCCEEDED for status in values):
        return ExecutionStatus.SUCCEEDED
    if any(status is ExecutionStatus.TIMED_OUT for status in values):
        return ExecutionStatus.TIMED_OUT
    if any(status is ExecutionStatus.CANCELLED for status in values):
        return ExecutionStatus.CANCELLED
    return ExecutionStatus.FAILED
