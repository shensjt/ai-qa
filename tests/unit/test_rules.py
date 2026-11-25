from __future__ import annotations

import hashlib
from typing import Any

from ai_qa.domain.enums import ExecutionStatus
from ai_qa.domain.enums import TestStatus as Status
from ai_qa.domain.errors import ErrorCode, ErrorInfo
from ai_qa.domain.models import utc_now
from ai_qa.domain.plans import ResolvedPlanItem
from ai_qa.domain.results import ArtifactRef, CaseFacts
from ai_qa.rules.evaluator import RuleEvaluatorImpl
from ai_qa.rules.http_rules import evaluate_rule_set

HTML = "text/html; charset=utf-8"
JSON = "application/json; charset=utf-8"


def _item(case_id: str) -> ResolvedPlanItem:
    return ResolvedPlanItem(
        case_id=case_id,
        sequence_no=1,
        executor="http",
        rule_set=case_id,
        timeout_seconds=5,
        artifact_policy="http_exchange",
        description="test",
        params={"path": "/"},
        refs=[],
    )


def _artifact() -> ArtifactRef:
    now = utc_now()
    return ArtifactRef(
        artifact_id="art_1",
        run_id="run_1",
        name="cases/x.txt",
        kind="http_response",
        uri="run_1/cases/x.txt",
        mime="text/plain",
        size_bytes=1,
        sha256="a" * 64,
        created_at=now,
    )


def _facts(case_id: str, observed: dict[str, Any], error: ErrorInfo | None = None) -> CaseFacts:
    now = utc_now()
    return CaseFacts(
        case_id=case_id,
        started_at=now,
        ended_at=now,
        request={"method": "GET", "path": "/"},
        observed=observed,
        artifacts=[_artifact()],
        executor_error=error,
    )


def _response(
    status: int = 200, body: bytes = b"0123456789", content_type: str = HTML
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": "OK" if status == 200 else "Not Found",
        "headers": {
            "content-type": content_type,
            "content-length": str(len(body)),
            "connection": "keep-alive",
        },
        "body_length": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_preview": body[:50].decode("utf-8", errors="replace"),
    }


def _static(case_id: str, **observed: Any) -> CaseFacts:
    return _facts(case_id, observed)


def test_get_index_passes() -> None:
    facts = _static(
        "http.get_index",
        status=200,
        headers={"content-length": "10"},
        body_length=10,
    )

    assertions = evaluate_rule_set("http.get_index", _item("http.get_index"), facts)

    assert assertions is not None
    assert all(assertion.status is Status.PASS for assertion in assertions)


def test_get_index_length_mismatch_fails() -> None:
    facts = _static(
        "http.get_index",
        status=200,
        headers={"content-length": "99"},
        body_length=10,
    )

    assertions = evaluate_rule_set("http.get_index", _item("http.get_index"), facts)

    assert assertions is not None
    failed = [a for a in assertions if a.status is Status.FAIL]
    assert [a.rule_id for a in failed] == ["http.body_integrity"]
    assert all(a.evidence_refs for a in failed)


def test_head_passes() -> None:
    facts = _static(
        "http.head",
        status=200,
        content_length_header=10,
        buffered_after_head=0,
        followup_status=200,
        connection_reused=True,
        followup_error=None,
    )

    assertions = evaluate_rule_set("http.head", _item("http.head"), facts)

    assert assertions is not None
    assert all(assertion.status is Status.PASS for assertion in assertions)


def test_head_detects_body_or_close() -> None:
    facts = _static(
        "http.head",
        status=200,
        content_length_header=10,
        buffered_after_head=7,
        followup_status=None,
        connection_reused=False,
        followup_error="connection closed",
    )

    result = RuleEvaluatorImpl().evaluate_case(_item("http.head"), facts)

    assert result.test_status is Status.FAIL
    failed_ids = {
        assertion.rule_id for assertion in result.assertions if assertion.status is Status.FAIL
    }
    assert failed_ids == {"http.head.no_body", "http.head.connection_reuse"}


def test_keep_alive_passes_and_detects_order() -> None:
    facts = _static(
        "http.keep_alive",
        responses=[
            _response(body=b"<html>index</html>", content_type=HTML),
            _response(body=b'{"status":"ok"}', content_type=JSON),
        ],
        response_count=2,
        connection_reused=True,
        second_error=None,
    )

    good = evaluate_rule_set("http.keep_alive", _item("http.keep_alive"), facts)
    assert good is not None
    assert all(assertion.status is Status.PASS for assertion in good)

    swapped = dict(facts.observed)
    swapped["responses"] = list(reversed(facts.observed["responses"]))
    assertions = evaluate_rule_set(
        "http.keep_alive", _item("http.keep_alive"), _facts("http.keep_alive", swapped)
    )
    assert assertions is not None
    order = next(a for a in assertions if a.rule_id == "http.keep_alive.order")
    assert order.status is Status.FAIL


def test_keep_alive_detects_closed_connection() -> None:
    facts = _static(
        "http.keep_alive",
        responses=[_response()],
        response_count=1,
        connection_reused=False,
        second_error="connection closed",
    )

    result = RuleEvaluatorImpl().evaluate_case(_item("http.keep_alive"), facts)

    assert result.test_status is Status.FAIL


def test_pipelined_passes_and_detects_missing_response() -> None:
    facts = _static(
        "http.pipelined",
        responses=[
            _response(body=b"<html>index</html>", content_type=HTML),
            _response(body=b'{"status":"ok"}', content_type=JSON),
        ],
        response_count=2,
        second_error=None,
    )

    good = evaluate_rule_set("http.pipelined", _item("http.pipelined"), facts)
    assert good is not None
    assert all(assertion.status is Status.PASS for assertion in good)

    broken = dict(facts.observed)
    broken["responses"] = facts.observed["responses"][:1]
    broken["response_count"] = 1
    broken["second_error"] = "timeout"
    result = RuleEvaluatorImpl().evaluate_case(
        _item("http.pipelined"), _facts("http.pipelined", broken)
    )
    assert result.test_status is Status.FAIL


def test_split_request_detects_405_regression() -> None:
    baseline = _response(body=b"<html>index</html>", content_type=HTML)
    split = _response(status=405, body=b"405 Method Not Allowed\n", content_type=HTML)
    split["reason"] = "Method Not Allowed"
    facts = _static(
        "http.split_request",
        baseline=baseline,
        split=split,
        split_sizes=[1, 3, 7],
        chunks_sent=8,
        chunk_sizes_used=[1, 3, 7, 1, 3, 7, 1, 3],
    )

    result = RuleEvaluatorImpl().evaluate_case(_item("http.split_request"), facts)

    assert result.test_status is Status.FAIL
    assert result.execution_status is ExecutionStatus.SUCCEEDED
    failed = {
        assertion.rule_id for assertion in result.assertions if assertion.status is Status.FAIL
    }
    assert {"http.split.status", "http.split.body_length", "http.split.body"} <= failed


def test_split_request_marks_single_chunk_inconclusive() -> None:
    baseline = _response()
    facts = _static(
        "http.split_request",
        baseline=baseline,
        split=dict(baseline),
        split_sizes=[1000],
        chunks_sent=1,
    )

    result = RuleEvaluatorImpl().evaluate_case(_item("http.split_request"), facts)

    assert result.test_status is Status.INCONCLUSIVE


def test_traversal_passes_on_safe_rejection() -> None:
    facts = _static(
        "http.path_traversal",
        status=403,
        body_length=14,
        body_preview="403 Forbidden\n",
        secret_leaked=False,
    )

    assertions = evaluate_rule_set("http.path_traversal", _item("http.path_traversal"), facts)

    assert assertions is not None
    assert all(assertion.status is Status.PASS for assertion in assertions)


def test_traversal_fails_on_leak_or_200() -> None:
    leaked = _static(
        "http.path_traversal",
        status=200,
        body_length=10,
        body_preview="TOP-SECRET",
        secret_leaked=True,
    )
    result = RuleEvaluatorImpl().evaluate_case(_item("http.path_traversal"), leaked)
    assert result.test_status is Status.FAIL

    served = _static(
        "http.path_traversal",
        status=200,
        body_length=10,
        body_preview="index",
        secret_leaked=False,
    )
    served_result = RuleEvaluatorImpl().evaluate_case(_item("http.path_traversal"), served)
    assert served_result.test_status is Status.FAIL


def test_traversal_unknown_status_is_warn() -> None:
    facts = _static(
        "http.path_traversal",
        status=500,
        body_length=21,
        body_preview="500 Internal Server",
        secret_leaked=False,
    )

    result = RuleEvaluatorImpl().evaluate_case(_item("http.path_traversal"), facts)

    assert result.test_status is Status.WARN


def test_executor_error_is_error_not_fail() -> None:
    error = ErrorInfo(code=ErrorCode.CASE_TIMEOUT, message="timeout")
    facts = _facts("http.get_index", {}, error=error)

    result = RuleEvaluatorImpl().evaluate_case(_item("http.get_index"), facts)

    assert result.execution_status is ExecutionStatus.TIMED_OUT
    assert result.test_status is Status.ERROR
    assert result.assertions == []


def test_unknown_rule_set_is_rule_engine_error() -> None:
    facts = _static("http.get_index", status=200, headers={}, body_length=0)

    result = RuleEvaluatorImpl().evaluate_case(_item("http.unknown_rule"), facts)

    assert result.test_status is Status.ERROR
    assert result.error is not None
    assert result.error.code is ErrorCode.RULE_ENGINE_ERROR


def test_evaluation_is_deterministic() -> None:
    facts = _static(
        "http.get_index",
        status=200,
        headers={"content-length": "10"},
        body_length=10,
    )
    evaluator = RuleEvaluatorImpl()

    first = evaluator.evaluate_case(_item("http.get_index"), facts)
    second = evaluator.evaluate_case(_item("http.get_index"), facts)

    assert first == second


def test_fail_without_evidence_is_rule_engine_error() -> None:
    now = utc_now()
    facts = CaseFacts(
        case_id="http.get_index",
        started_at=now,
        ended_at=now,
        observed={"status": 500, "headers": {}, "body_length": 0},
        artifacts=[],
    )

    result = RuleEvaluatorImpl().evaluate_case(_item("http.get_index"), facts)

    assert result.test_status is Status.ERROR
    assert result.error is not None
    assert result.error.code is ErrorCode.RULE_ENGINE_ERROR
