"""HTTP 用例的确定性断言（实现规格 §13.3）。

Rule 不调用 LLM、不修改 facts、不依赖随机状态；同一 facts 多次评估结果一致。
"""

from __future__ import annotations

from typing import Any

from ai_qa.domain.enums import TestStatus
from ai_qa.domain.plans import ResolvedPlanItem
from ai_qa.domain.results import AssertionResult, CaseFacts

_SAFE_TRAVERSAL_STATUSES = {400, 403, 404}


def evaluate_rule_set(
    rule_set: str, item: ResolvedPlanItem, facts: CaseFacts
) -> list[AssertionResult] | None:
    """返回断言列表；未知 rule_set 返回 None（由 Evaluator 判 RULE_ENGINE_ERROR）。"""
    evaluator = _RULES.get(rule_set)
    if evaluator is None:
        return None
    return evaluator(item, facts)


def _assertion(
    rule_id: str,
    name: str,
    status: TestStatus,
    expected: Any,
    observed: Any,
    message: str,
    facts: CaseFacts,
) -> AssertionResult:
    return AssertionResult(
        rule_id=rule_id,
        name=name,
        status=status,
        expected=expected,
        observed=observed,
        message=message,
        evidence_refs=[artifact.artifact_id for artifact in facts.artifacts],
    )


def _status_equals(rule_id: str, facts: CaseFacts, expected_status: int) -> AssertionResult:
    observed = facts.observed.get("status")
    ok = observed == expected_status
    return _assertion(
        rule_id,
        f"status == {expected_status}",
        TestStatus.PASS if ok else TestStatus.FAIL,
        expected_status,
        observed,
        "" if ok else f"期望状态 {expected_status}，实际 {observed}",
        facts,
    )


def _evaluate_get_index(item: ResolvedPlanItem, facts: CaseFacts) -> list[AssertionResult]:
    observed = facts.observed
    body_length = observed.get("body_length")
    header_length = _int_value(_as_dict(observed.get("headers")).get("content-length"))
    integrity_status = (
        TestStatus.PASS
        if isinstance(body_length, int) and body_length == header_length
        else TestStatus.FAIL
    )
    non_empty_status = (
        TestStatus.PASS if isinstance(body_length, int) and body_length > 0 else TestStatus.FAIL
    )
    return [
        _status_equals("http.status", facts, 200),
        _assertion(
            "http.body_integrity",
            "Content-Length 与实际 body 长度一致",
            integrity_status,
            header_length,
            body_length,
            "" if integrity_status is TestStatus.PASS else "响应体长度与头不一致",
            facts,
        ),
        _assertion(
            "http.body_non_empty",
            "响应体非空",
            non_empty_status,
            0,
            body_length,
            "" if non_empty_status is TestStatus.PASS else "响应体为空",
            facts,
        ),
    ]


def _evaluate_head(item: ResolvedPlanItem, facts: CaseFacts) -> list[AssertionResult]:
    observed = facts.observed
    content_length = observed.get("content_length_header")
    length_ok = isinstance(content_length, int) and content_length > 0
    buffered = observed.get("buffered_after_head")
    no_body_ok = buffered == 0
    reused = observed.get("connection_reused") is True
    followup = observed.get("followup_status")

    return [
        _status_equals("http.status", facts, 200),
        _assertion(
            "http.head.content_length",
            "HEAD 携带非零 Content-Length",
            TestStatus.PASS if length_ok else TestStatus.FAIL,
            ">0",
            content_length,
            "" if length_ok else "HEAD 缺少有效 Content-Length",
            facts,
        ),
        _assertion(
            "http.head.no_body",
            "HEAD 不发送响应体",
            TestStatus.PASS if no_body_ok else TestStatus.FAIL,
            0,
            buffered,
            "" if no_body_ok else "HEAD 响应后仍有未消费的 body 字节",
            facts,
        ),
        _assertion(
            "http.head.connection_reuse",
            "HEAD 之后连接仍可复用",
            TestStatus.PASS if reused and followup == 200 else TestStatus.FAIL,
            200,
            {"followup_status": followup, "error": observed.get("followup_error")},
            "" if reused and followup == 200 else "HEAD 后连接不可复用或后续请求失败",
            facts,
        ),
    ]


def _evaluate_keep_alive(item: ResolvedPlanItem, facts: CaseFacts) -> list[AssertionResult]:
    observed = facts.observed
    responses = _as_response_list(observed.get("responses"))
    count = observed.get("response_count")
    statuses = [response.get("status") for response in responses]
    second_is_health = _is_health_response(responses, index=1)
    first_is_not_health = not _is_health_response(responses, index=0)

    return [
        _assertion(
            "http.keep_alive.response_count",
            "同一连接收到两个响应",
            TestStatus.PASS if count == 2 else TestStatus.FAIL,
            2,
            count,
            "" if count == 2 else f"只收到 {count} 个响应",
            facts,
        ),
        _assertion(
            "http.keep_alive.statuses",
            "两个响应状态均为 200",
            TestStatus.PASS if statuses == [200, 200] else TestStatus.FAIL,
            [200, 200],
            statuses,
            "" if statuses == [200, 200] else "响应状态不符合预期",
            facts,
        ),
        _assertion(
            "http.keep_alive.order",
            "响应顺序正确（首页在前，healthz 在后）",
            TestStatus.PASS if first_is_not_health and second_is_health else TestStatus.FAIL,
            "first=static, second=healthz",
            {"first_is_health": not first_is_not_health, "second_is_health": second_is_health},
            "" if first_is_not_health and second_is_health else "响应顺序或内容错位",
            facts,
        ),
        _assertion(
            "http.keep_alive.reused",
            "连接未被提前关闭",
            TestStatus.PASS if observed.get("connection_reused") is True else TestStatus.FAIL,
            True,
            observed.get("second_error"),
            "" if observed.get("connection_reused") is True else "第二个请求失败或连接被关闭",
            facts,
        ),
    ]


def _evaluate_pipelined(item: ResolvedPlanItem, facts: CaseFacts) -> list[AssertionResult]:
    observed = facts.observed
    responses = _as_response_list(observed.get("responses"))
    count = observed.get("response_count")
    statuses = [response.get("status") for response in responses]
    second_is_health = _is_health_response(responses, index=1)
    first_is_not_health = not _is_health_response(responses, index=0)

    return [
        _assertion(
            "http.pipelined.response_count",
            "一次发送收到两个响应",
            TestStatus.PASS if count == 2 else TestStatus.FAIL,
            2,
            count,
            "" if count == 2 else f"只收到 {count} 个响应",
            facts,
        ),
        _assertion(
            "http.pipelined.statuses",
            "两个响应状态均为 200",
            TestStatus.PASS if statuses == [200, 200] else TestStatus.FAIL,
            [200, 200],
            statuses,
            "" if statuses == [200, 200] else "响应状态不符合预期",
            facts,
        ),
        _assertion(
            "http.pipelined.order",
            "流水线响应顺序正确",
            TestStatus.PASS if first_is_not_health and second_is_health else TestStatus.FAIL,
            "first=static, second=healthz",
            {"first_is_health": not first_is_not_health, "second_is_health": second_is_health},
            "" if first_is_not_health and second_is_health else "流水线响应顺序错位",
            facts,
        ),
        _assertion(
            "http.pipelined.second_response",
            "第二个响应完整",
            TestStatus.PASS if observed.get("second_error") is None else TestStatus.FAIL,
            None,
            observed.get("second_error"),
            "" if observed.get("second_error") is None else "第二个响应读取失败",
            facts,
        ),
    ]


def _evaluate_split_request(item: ResolvedPlanItem, facts: CaseFacts) -> list[AssertionResult]:
    observed = facts.observed
    baseline = _as_dict(observed.get("baseline"))
    split = _as_dict(observed.get("split"))
    baseline_status = baseline.get("status")
    split_status = split.get("status")
    chunks = observed.get("chunks_sent")

    baseline_ok = baseline_status == 200
    status_matches = split_status == baseline_status
    length_matches = split.get("body_length") == baseline.get("body_length")
    body_matches = split.get("body_sha256") == baseline.get("body_sha256")
    fragmented = isinstance(chunks, int) and chunks > 1

    return [
        _assertion(
            "http.split.baseline",
            "基线请求成功",
            TestStatus.PASS if baseline_ok else TestStatus.FAIL,
            200,
            baseline_status,
            "" if baseline_ok else "基线 GET 未返回 200",
            facts,
        ),
        _assertion(
            "http.split.status",
            "分片请求与基线状态一致",
            TestStatus.PASS if status_matches else TestStatus.FAIL,
            baseline_status,
            split_status,
            ""
            if status_matches
            else f"分片请求状态 {split_status} 与基线 {baseline_status} 不一致（分片/半包解析异常）",
            facts,
        ),
        _assertion(
            "http.split.body_length",
            "分片响应长度与基线一致",
            TestStatus.PASS if length_matches else TestStatus.FAIL,
            baseline.get("body_length"),
            split.get("body_length"),
            "" if length_matches else "分片响应体长度与基线不一致",
            facts,
        ),
        _assertion(
            "http.split.body",
            "分片响应内容与基线一致",
            TestStatus.PASS if body_matches else TestStatus.FAIL,
            baseline.get("body_sha256"),
            split.get("body_sha256"),
            "" if body_matches else "分片响应体 hash 与基线不一致",
            facts,
        ),
        _assertion(
            "http.split.fragmented",
            "请求确实被分成多片发送",
            TestStatus.PASS if fragmented else TestStatus.INCONCLUSIVE,
            ">1",
            chunks,
            "" if fragmented else "分片数不足，无法验证半包解析",
            facts,
        ),
    ]


def _evaluate_path_traversal(item: ResolvedPlanItem, facts: CaseFacts) -> list[AssertionResult]:
    observed = facts.observed
    leaked = observed.get("secret_leaked") is True
    status = observed.get("status")
    if status in _SAFE_TRAVERSAL_STATUSES:
        status_verdict = TestStatus.PASS
        status_message = ""
    elif status == 200:
        status_verdict = TestStatus.FAIL
        status_message = "路径穿越请求返回 200"
    else:
        status_verdict = TestStatus.WARN
        status_message = f"路径穿越请求返回非预期状态 {status}"

    return [
        _assertion(
            "http.path_traversal.no_secret",
            "响应不包含受控秘密内容",
            TestStatus.FAIL if leaked else TestStatus.PASS,
            False,
            leaked,
            "响应泄露了 www_root 之外的文件内容" if leaked else "",
            facts,
        ),
        _assertion(
            "http.path_traversal.safe_status",
            "返回安全的拒绝状态",
            status_verdict,
            sorted(_SAFE_TRAVERSAL_STATUSES),
            status,
            status_message,
            facts,
        ),
    ]


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _as_response_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [_as_dict(item) for item in value]


def _is_health_response(responses: list[dict[str, Any]], index: int) -> bool:
    if index >= len(responses):
        return False
    content_type = responses[index].get("headers", {}).get("content-type", "")
    return "application/json" in content_type


def _int_value(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


_RULES = {
    "http.get_index": _evaluate_get_index,
    "http.head": _evaluate_head,
    "http.keep_alive": _evaluate_keep_alive,
    "http.pipelined": _evaluate_pipelined,
    "http.split_request": _evaluate_split_request,
    "http.path_traversal": _evaluate_path_traversal,
}
