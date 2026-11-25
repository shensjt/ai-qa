from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from ai_qa.domain.enums import PlanMode, PlanStatus
from ai_qa.domain.errors import ErrorCode
from ai_qa.validation.catalog import CaseCatalog
from ai_qa.validation.policy import ValidationPolicy
from ai_qa.validation.validator import (
    PlanValidator,
    ValidationContext,
    ValidationResult,
)

TARGET = "cpp-webserver-local"


def _policy(**overrides: Any) -> ValidationPolicy:
    base: dict[str, Any] = {
        "expected_target_id": TARGET,
        "max_cases": 6,
        "run_timeout_seconds": 120,
        "benchmark_allowed": False,
        "benchmark_max_duration_seconds": 30,
        "benchmark_max_connections": 64,
        "benchmark_max_threads": 4,
        "max_plan_repairs": 2,
    }
    base.update(overrides)
    return ValidationPolicy(**base)


def _plan(
    items: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": "1.0",
        "goal": "smoke",
        "target_id": TARGET,
        "items": items or [{"case_id": "http.get_index", "params": {}}],
        "benchmark": None,
        "limits": {"max_cases": 6, "run_timeout_seconds": 120},
    }
    data.update(overrides)
    return data


def _benchmark(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "enabled": True,
        "tool": "wrk",
        "path": "/",
        "duration_seconds": 10,
        "connections": 10,
        "threads": 2,
    }
    data.update(overrides)
    return data


def _fixed() -> ValidationContext:
    return ValidationContext(mode=PlanMode.FIXED_PLAN)


def _agent(refs: set[str]) -> ValidationContext:
    return ValidationContext(mode=PlanMode.AGENT_PLAN, snapshot_ref_ids=frozenset(refs))


def _run(
    catalog: CaseCatalog,
    plan: Any,
    context: ValidationContext | None = None,
    **policy_overrides: Any,
) -> ValidationResult:
    validator = PlanValidator(catalog, _policy(**policy_overrides))
    return validator.validate(plan, context or _fixed())


def _codes(result: ValidationResult) -> list[ErrorCode]:
    return [issue.code for issue in result.issues]


def test_accepts_valid_plan(http_catalog: CaseCatalog) -> None:
    result = _run(http_catalog, _plan())

    assert result.ok
    assert result.plan is not None
    assert result.plan.status is PlanStatus.VALIDATED
    assert result.plan.plan_id.startswith("plan_")
    assert result.plan.plan_hash.startswith("sha256:")
    assert result.plan.items[0].params == {"path": "/"}
    assert result.plan.items[0].sequence_no == 1


def test_merges_defaults_and_overrides(http_catalog: CaseCatalog) -> None:
    result = _run(
        http_catalog,
        _plan(
            items=[
                {
                    "case_id": "http.split_request",
                    "params": {"split_sizes": [2, 4]},
                }
            ]
        ),
    )

    assert result.plan is not None
    assert result.plan.items[0].params == {"path": "/", "split_sizes": [2, 4]}


def test_resolved_item_comes_from_catalog(http_catalog: CaseCatalog) -> None:
    result = _run(http_catalog, _plan())

    assert result.plan is not None
    item = result.plan.items[0]
    assert item.executor == "http"
    assert item.rule_set == "http.get_index"
    assert item.timeout_seconds == 5
    assert item.artifact_policy == "http_exchange"


def test_plan_hash_is_stable_across_calls(http_catalog: CaseCatalog) -> None:
    first = _run(http_catalog, _plan())
    second = _run(http_catalog, _plan())

    assert first.plan is not None and second.plan is not None
    assert first.plan.plan_hash == second.plan.plan_hash
    assert first.plan.plan_id != second.plan.plan_id


def test_unknown_case(http_catalog: CaseCatalog) -> None:
    result = _run(
        http_catalog,
        _plan(items=[{"case_id": "http.made_up", "params": {}}]),
    )

    assert not result.ok
    assert _codes(result) == [ErrorCode.UNKNOWN_CASE]
    assert result.issues[0].path == "items[0].case_id"
    assert "http.get_index" in (result.issues[0].allowed or [])


def test_duplicate_case(http_catalog: CaseCatalog) -> None:
    result = _run(
        http_catalog,
        _plan(
            items=[
                {"case_id": "http.get_index", "params": {}},
                {"case_id": "http.get_index", "params": {}},
            ]
        ),
    )

    assert _codes(result) == [ErrorCode.DUPLICATE_CASE]
    assert result.issues[0].path == "items[1].case_id"


def test_param_not_allowed(http_catalog: CaseCatalog) -> None:
    result = _run(
        http_catalog,
        _plan(items=[{"case_id": "http.get_index", "params": {"command": "id"}}]),
    )

    assert _codes(result) == [ErrorCode.PARAM_NOT_ALLOWED]
    assert result.issues[0].allowed == ["path"]
    assert result.issues[0].path == "items[0].params.command"


def test_param_invalid_type(http_catalog: CaseCatalog) -> None:
    result = _run(
        http_catalog,
        _plan(
            items=[
                {
                    "case_id": "http.split_request",
                    "params": {"split_sizes": "abc"},
                }
            ]
        ),
    )

    assert _codes(result) == [ErrorCode.PARAM_INVALID]


@pytest.mark.parametrize(
    "path",
    [
        "/../etc/passwd",
        "/%2e%2e/etc/passwd",
        "/%2fetc/passwd",
        "/a\\b",
        "http://example.com/",
        "/ index",
        "no-leading-slash",
    ],
)
def test_http_path_rejects_unsafe_targets(http_catalog: CaseCatalog, path: str) -> None:
    result = _run(
        http_catalog,
        _plan(items=[{"case_id": "http.get_index", "params": {"path": path}}]),
    )

    assert _codes(result) == [ErrorCode.PARAM_INVALID]


def test_attack_path_accepts_catalog_value(http_catalog: CaseCatalog) -> None:
    result = _run(
        http_catalog,
        _plan(
            items=[
                {
                    "case_id": "http.path_traversal",
                    "params": {"path": "/..%2foutside_secret.txt"},
                }
            ]
        ),
    )

    assert result.ok
    assert result.plan is not None
    assert result.plan.items[0].params["path"] == "/..%2foutside_secret.txt"


def test_attack_path_rejects_arbitrary_target(http_catalog: CaseCatalog) -> None:
    result = _run(
        http_catalog,
        _plan(
            items=[
                {
                    "case_id": "http.path_traversal",
                    "params": {"path": "/etc/passwd"},
                }
            ]
        ),
    )

    assert _codes(result) == [ErrorCode.PARAM_INVALID]


def test_agent_refs_must_be_in_snapshot(http_catalog: CaseCatalog) -> None:
    good = _run(
        http_catalog,
        _plan(items=[{"case_id": "http.get_index", "params": {}, "refs": ["ref_1"]}]),
        _agent({"ref_1"}),
    )
    bad = _run(
        http_catalog,
        _plan(items=[{"case_id": "http.get_index", "params": {}, "refs": ["ref_x"]}]),
        _agent({"ref_1"}),
    )

    assert good.ok
    assert _codes(bad) == [ErrorCode.REFERENCE_INVALID]
    assert bad.issues[0].path == "items[0].refs[0]"


def test_agent_context_requires_snapshot() -> None:
    with pytest.raises(ValidationError):
        ValidationContext(mode=PlanMode.AGENT_PLAN)


def test_fixed_plan_allows_empty_refs(http_catalog: CaseCatalog) -> None:
    result = _run(http_catalog, _plan())

    assert result.ok


def test_fixed_plan_rejects_unverifiable_refs(http_catalog: CaseCatalog) -> None:
    result = _run(
        http_catalog,
        _plan(items=[{"case_id": "http.get_index", "params": {}, "refs": ["ref_1"]}]),
    )

    assert _codes(result) == [ErrorCode.REFERENCE_INVALID]


def test_fixed_plan_can_verify_refs_with_snapshot(http_catalog: CaseCatalog) -> None:
    context = ValidationContext(mode=PlanMode.FIXED_PLAN, snapshot_ref_ids=frozenset({"ref_1"}))
    result = _run(
        http_catalog,
        _plan(items=[{"case_id": "http.get_index", "params": {}, "refs": ["ref_1"]}]),
        context,
    )

    assert result.ok


def test_target_mismatch(http_catalog: CaseCatalog) -> None:
    result = _run(http_catalog, _plan(target_id="other-target"))

    assert _codes(result) == [ErrorCode.TARGET_NOT_FOUND]
    assert result.issues[0].allowed == [TARGET]


def test_too_many_items(http_catalog: CaseCatalog) -> None:
    result = _run(
        http_catalog,
        _plan(
            items=[
                {"case_id": "http.get_index", "params": {}},
                {"case_id": "http.head", "params": {}},
            ],
            limits={"max_cases": 1, "run_timeout_seconds": 120},
        ),
    )

    assert _codes(result) == [ErrorCode.POLICY_VIOLATION]
    assert result.issues[0].path == "items"


def test_limits_clamped_to_policy(http_catalog: CaseCatalog) -> None:
    result = _run(
        http_catalog,
        _plan(limits={"max_cases": 99, "run_timeout_seconds": 999}),
        max_cases=6,
        run_timeout_seconds=120,
    )

    assert result.plan is not None
    assert result.plan.limits.max_cases == 6
    assert result.plan.limits.run_timeout_seconds == 120


def test_benchmark_requires_authorization(http_catalog: CaseCatalog) -> None:
    result = _run(http_catalog, _plan(benchmark=_benchmark()))

    assert ErrorCode.POLICY_VIOLATION in _codes(result)
    assert any(issue.path == "benchmark" for issue in result.issues)


@pytest.mark.parametrize(
    ("override", "path"),
    [
        ({"duration_seconds": 31}, "benchmark.duration_seconds"),
        ({"connections": 65}, "benchmark.connections"),
        ({"threads": 5}, "benchmark.threads"),
    ],
)
def test_benchmark_limits(http_catalog: CaseCatalog, override: dict[str, Any], path: str) -> None:
    result = _run(
        http_catalog,
        _plan(benchmark=_benchmark(**override)),
        benchmark_allowed=True,
    )

    assert _codes(result) == [ErrorCode.POLICY_VIOLATION]
    assert result.issues[0].path == path


def test_benchmark_path_traversal_rejected(http_catalog: CaseCatalog) -> None:
    result = _run(
        http_catalog,
        _plan(benchmark=_benchmark(path="/../secret")),
        benchmark_allowed=True,
    )

    assert _codes(result) == [ErrorCode.PARAM_INVALID]
    assert result.issues[0].path == "benchmark.path"


def test_authorized_benchmark_is_stored(http_catalog: CaseCatalog) -> None:
    result = _run(
        http_catalog,
        _plan(benchmark=_benchmark()),
        benchmark_allowed=True,
    )

    assert result.ok
    assert result.plan is not None
    assert result.plan.benchmark is not None
    assert result.plan.benchmark.duration_seconds == 10


def test_disabled_benchmark_is_not_stored(http_catalog: CaseCatalog) -> None:
    result = _run(http_catalog, _plan(benchmark=_benchmark(enabled=False)))

    assert result.ok
    assert result.plan is not None
    assert result.plan.benchmark is None


def test_malformed_json_string(http_catalog: CaseCatalog) -> None:
    result = _run(http_catalog, "{not-json")

    assert _codes(result) == [ErrorCode.PLAN_JSON_INVALID]


def test_json_top_level_must_be_object(http_catalog: CaseCatalog) -> None:
    result = _run(http_catalog, "[]")

    assert _codes(result) == [ErrorCode.PLAN_JSON_INVALID]


def test_schema_extra_field(http_catalog: CaseCatalog) -> None:
    data = _plan()
    data["unexpected"] = 1

    result = _run(http_catalog, data)

    assert _codes(result) == [ErrorCode.PLAN_SCHEMA_INVALID]
    assert result.issues[0].path == "unexpected"


def test_schema_extra_item_field(http_catalog: CaseCatalog) -> None:
    result = _run(
        http_catalog,
        _plan(items=[{"case_id": "http.get_index", "params": {}, "extra": 1}]),
    )

    assert _codes(result) == [ErrorCode.PLAN_SCHEMA_INVALID]
    assert result.issues[0].path == "items[0].extra"


def test_multiple_issues_are_accumulated(http_catalog: CaseCatalog) -> None:
    result = _run(
        http_catalog,
        _plan(
            target_id="wrong",
            items=[{"case_id": "http.made_up", "params": {}}],
        ),
    )

    assert _codes(result) == [ErrorCode.TARGET_NOT_FOUND, ErrorCode.UNKNOWN_CASE]
