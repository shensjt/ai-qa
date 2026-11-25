from __future__ import annotations

import pytest

from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.validation.repair import PlanRepairBudget


def test_budget_allows_max_repairs() -> None:
    budget = PlanRepairBudget(2)

    assert budget.remaining == 2
    assert budget.consume() == 1
    assert budget.consume() == 2
    assert budget.can_repair is False
    assert budget.remaining == 0


def test_budget_exhausted_raises_plan_repair_exhausted() -> None:
    budget = PlanRepairBudget(1)
    budget.consume()

    with pytest.raises(AiQaError) as excinfo:
        budget.consume()

    assert excinfo.value.code is ErrorCode.PLAN_REPAIR_EXHAUSTED
    assert excinfo.value.details == {"max_repairs": 1, "used": 1}


def test_zero_repairs_is_exhausted_immediately() -> None:
    budget = PlanRepairBudget(0)

    assert budget.can_repair is False
    with pytest.raises(AiQaError):
        budget.consume()


def test_negative_budget_rejected() -> None:
    with pytest.raises(ValueError):
        PlanRepairBudget(-1)
