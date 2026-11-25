"""MCP structuredContent 的客户端契约校验。"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.results import RunResult, ToolFailureResult, ToolResult


def parse_tool_result(structured: dict[str, Any] | None, is_error: bool) -> ToolResult:
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


def parse_run_result(structured: dict[str, Any] | None, is_error: bool) -> RunResult:
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
