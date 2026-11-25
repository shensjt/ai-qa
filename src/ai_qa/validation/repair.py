"""计划修复预算（实现规格 §11.4）；Phase 1 不接 LLM。"""

from __future__ import annotations

from ai_qa.domain.errors import AiQaError, ErrorCode


class PlanRepairBudget:
    def __init__(self, max_repairs: int) -> None:
        if max_repairs < 0:
            raise ValueError("max_repairs 不能为负数")
        self._max_repairs = max_repairs
        self._used = 0

    @property
    def max_repairs(self) -> int:
        return self._max_repairs

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return self._max_repairs - self._used

    @property
    def can_repair(self) -> bool:
        return self.remaining > 0

    def consume(self) -> int:
        """占用一次修复机会，返回本次尝试序号；耗尽时抛 PLAN_REPAIR_EXHAUSTED。"""
        if not self.can_repair:
            raise AiQaError(
                ErrorCode.PLAN_REPAIR_EXHAUSTED,
                f"计划修复次数已用尽（上限 {self._max_repairs}）",
                details={"max_repairs": self._max_repairs, "used": self._used},
            )
        self._used += 1
        return self._used
