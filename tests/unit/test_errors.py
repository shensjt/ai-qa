from __future__ import annotations

import pytest

from ai_qa.domain.errors import AiQaError, ErrorCode, exit_code_for

SPEC_CODES = {
    "CONFIG_INVALID",
    "TARGET_NOT_FOUND",
    "TARGET_START_FAILED",
    "TARGET_NOT_READY",
    "TARGET_STOP_FAILED",
    "PORT_UNAVAILABLE",
    "DEPENDENCY_MISSING",
    "PLAN_JSON_INVALID",
    "PLAN_SCHEMA_INVALID",
    "UNKNOWN_CASE",
    "DUPLICATE_CASE",
    "PARAM_NOT_ALLOWED",
    "PARAM_INVALID",
    "POLICY_VIOLATION",
    "REFERENCE_INVALID",
    "PLAN_REPAIR_EXHAUSTED",
    "PLAN_HASH_MISMATCH",
    "MCP_PROTOCOL_ERROR",
    "MCP_TOOL_ERROR",
    "TOOL_TIMEOUT",
    "CASE_TIMEOUT",
    "ASSERTION_FAILED",
    "RULE_ENGINE_ERROR",
    "ARTIFACT_WRITE_FAILED",
    "RETRIEVAL_UNAVAILABLE",
    "EMBEDDING_UNAVAILABLE",
    "LLM_REQUEST_FAILED",
    "LLM_OUTPUT_INVALID",
    "REPORT_GENERATION_FAILED",
    "RUN_CANCELLED",
    "INTERNAL_ERROR",
}


def test_all_spec_error_codes_defined() -> None:
    assert {code.value for code in ErrorCode} == SPEC_CODES


@pytest.mark.parametrize(
    "code",
    [
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
    ],
)
def test_plan_errors_exit_3(code: ErrorCode) -> None:
    assert exit_code_for(code) == 3


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (ErrorCode.CONFIG_INVALID, 2),
        (ErrorCode.RUN_CANCELLED, 6),
        (ErrorCode.INTERNAL_ERROR, 7),
        (ErrorCode.TARGET_NOT_READY, 5),
        (ErrorCode.MCP_PROTOCOL_ERROR, 5),
        (ErrorCode.ASSERTION_FAILED, 5),
    ],
)
def test_other_exit_codes(code: ErrorCode, expected: int) -> None:
    assert exit_code_for(code) == expected


def test_error_info_captures_cause_type() -> None:
    try:
        try:
            raise TimeoutError("slow")
        except TimeoutError as cause:
            raise AiQaError(
                ErrorCode.TARGET_NOT_READY,
                "目标未就绪",
                details={"health_path": "/healthz"},
            ) from cause
    except AiQaError as exc:
        info = exc.to_error_info()

    assert info.code is ErrorCode.TARGET_NOT_READY
    assert info.message == "目标未就绪"
    assert info.retryable is False
    assert info.details == {"health_path": "/healthz"}
    assert info.cause_type == "TimeoutError"
    assert info.model_dump(mode="json")["code"] == "TARGET_NOT_READY"


def test_error_without_cause() -> None:
    info = AiQaError(ErrorCode.CONFIG_INVALID, "bad config").to_error_info()
    assert info.cause_type is None
