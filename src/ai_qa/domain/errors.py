"""统一错误码、错误对象与 CLI 退出码映射（实现规格 §6.4 / §16.3）。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    """稳定错误码。新增代码必须同步实现规格 §6.4。"""

    CONFIG_INVALID = "CONFIG_INVALID"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_START_FAILED = "TARGET_START_FAILED"
    TARGET_NOT_READY = "TARGET_NOT_READY"
    TARGET_STOP_FAILED = "TARGET_STOP_FAILED"
    PORT_UNAVAILABLE = "PORT_UNAVAILABLE"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    PLAN_JSON_INVALID = "PLAN_JSON_INVALID"
    PLAN_SCHEMA_INVALID = "PLAN_SCHEMA_INVALID"
    UNKNOWN_CASE = "UNKNOWN_CASE"
    DUPLICATE_CASE = "DUPLICATE_CASE"
    PARAM_NOT_ALLOWED = "PARAM_NOT_ALLOWED"
    PARAM_INVALID = "PARAM_INVALID"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    REFERENCE_INVALID = "REFERENCE_INVALID"
    PLAN_REPAIR_EXHAUSTED = "PLAN_REPAIR_EXHAUSTED"
    PLAN_HASH_MISMATCH = "PLAN_HASH_MISMATCH"
    MCP_PROTOCOL_ERROR = "MCP_PROTOCOL_ERROR"
    MCP_TOOL_ERROR = "MCP_TOOL_ERROR"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    CASE_TIMEOUT = "CASE_TIMEOUT"
    ASSERTION_FAILED = "ASSERTION_FAILED"
    RULE_ENGINE_ERROR = "RULE_ENGINE_ERROR"
    ARTIFACT_WRITE_FAILED = "ARTIFACT_WRITE_FAILED"
    RETRIEVAL_UNAVAILABLE = "RETRIEVAL_UNAVAILABLE"
    EMBEDDING_UNAVAILABLE = "EMBEDDING_UNAVAILABLE"
    LLM_REQUEST_FAILED = "LLM_REQUEST_FAILED"
    LLM_OUTPUT_INVALID = "LLM_OUTPUT_INVALID"
    REPORT_GENERATION_FAILED = "REPORT_GENERATION_FAILED"
    RUN_CANCELLED = "RUN_CANCELLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorInfo(BaseModel):
    """可序列化的错误事实；details 不得包含密钥或不受控大文本。"""

    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    cause_type: str | None = None


class AiQaError(Exception):
    """所有预期内的业务/环境错误都应包装为该类型。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details: dict[str, Any] = dict(details or {})

    def to_error_info(self) -> ErrorInfo:
        cause = self.__cause__
        return ErrorInfo(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            details=self.details,
            cause_type=type(cause).__name__ if cause is not None else None,
        )


_PLAN_ERROR_CODES = frozenset(
    {
        ErrorCode.PLAN_JSON_INVALID,
        ErrorCode.PLAN_SCHEMA_INVALID,
        ErrorCode.UNKNOWN_CASE,
        ErrorCode.DUPLICATE_CASE,
        ErrorCode.PARAM_NOT_ALLOWED,
        ErrorCode.PARAM_INVALID,
        ErrorCode.POLICY_VIOLATION,
        ErrorCode.REFERENCE_INVALID,
        ErrorCode.PLAN_REPAIR_EXHAUSTED,
        ErrorCode.PLAN_HASH_MISMATCH,
    }
)


def exit_code_for(code: ErrorCode) -> int:
    """按实现规格 §16.3 映射退出码。"""
    if code is ErrorCode.CONFIG_INVALID:
        return 2
    if code in _PLAN_ERROR_CODES:
        return 3
    if code is ErrorCode.RUN_CANCELLED:
        return 6
    if code is ErrorCode.INTERNAL_ERROR:
        return 7
    return 5
