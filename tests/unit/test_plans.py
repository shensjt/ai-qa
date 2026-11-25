from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ai_qa.domain import plans


def _plan_data() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "goal": "smoke",
        "target_id": "cpp-webserver-local",
        "items": [{"case_id": "http.get_index", "params": {"path": "/"}}],
        "benchmark": None,
        "limits": {"max_cases": 6, "run_timeout_seconds": 120},
    }


def test_plan_hash_is_prefixed_sha256() -> None:
    plan = plans.TestPlan.model_validate(_plan_data())

    digest = plans.compute_plan_hash(plan)

    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_plan_hash_ignores_key_order() -> None:
    data = _plan_data()
    reordered = {key: data[key] for key in reversed(list(data.keys()))}

    assert plans.compute_plan_hash(plans.TestPlan.model_validate(data)) == plans.compute_plan_hash(
        plans.TestPlan.model_validate(reordered)
    )


def test_plan_hash_changes_with_content() -> None:
    base = plans.TestPlan.model_validate(_plan_data())
    changed = _plan_data()
    changed["goal"] = "different goal"

    assert plans.compute_plan_hash(base) != plans.compute_plan_hash(
        plans.TestPlan.model_validate(changed)
    )


def test_canonical_json_is_compact_and_sorted() -> None:
    plan = plans.TestPlan.model_validate(_plan_data())

    canonical = plans.canonical_plan_json(plan)

    assert " " not in canonical
    assert json.loads(canonical)["goal"] == "smoke"
    keys = list(json.loads(canonical).keys())
    assert keys == sorted(keys)


def test_extra_field_rejected() -> None:
    data = _plan_data()
    data["command"] = "rm -rf /"

    with pytest.raises(ValidationError):
        plans.TestPlan.model_validate(data)


def test_empty_items_rejected() -> None:
    data = _plan_data()
    data["items"] = []

    with pytest.raises(ValidationError):
        plans.TestPlan.model_validate(data)
