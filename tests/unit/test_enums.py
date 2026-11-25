from __future__ import annotations

from ai_qa.domain import enums

Status = enums.TestStatus


def test_priority_order() -> None:
    assert enums.aggregate_test_status([Status.PASS, Status.WARN]) is Status.WARN
    assert enums.aggregate_test_status([Status.WARN, Status.INCONCLUSIVE]) is Status.INCONCLUSIVE
    assert enums.aggregate_test_status([Status.INCONCLUSIVE, Status.FAIL]) is Status.FAIL
    assert enums.aggregate_test_status([Status.FAIL, Status.ERROR]) is Status.ERROR


def test_error_beats_everything() -> None:
    assert enums.aggregate_test_status([Status.PASS, Status.FAIL, Status.ERROR]) is Status.ERROR


def test_single_status_returns_itself() -> None:
    for status in Status:
        assert enums.aggregate_test_status([status]) is status


def test_aggregation_is_order_independent() -> None:
    statuses = [Status.FAIL, Status.PASS, Status.WARN]

    assert enums.aggregate_test_status(statuses) == enums.aggregate_test_status(reversed(statuses))


def test_empty_is_inconclusive() -> None:
    assert enums.aggregate_test_status([]) is Status.INCONCLUSIVE
