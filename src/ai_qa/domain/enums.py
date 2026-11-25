"""状态枚举与状态聚合（实现规格 §6.1 / §13.4）。"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class TestStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    INCONCLUSIVE = "INCONCLUSIVE"
    ERROR = "ERROR"


class ExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class RunState(StrEnum):
    CREATED = "CREATED"
    RETRIEVING = "RETRIEVING"
    PLANNING = "PLANNING"
    PLANNED = "PLANNED"
    VALIDATED = "VALIDATED"
    RUNNING = "RUNNING"
    EVALUATING = "EVALUATING"
    REPORTING = "REPORTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PlanMode(StrEnum):
    """计划来源。两种模式共享全部安全校验（规格 §11.3）。"""

    AGENT_PLAN = "AGENT_PLAN"
    FIXED_PLAN = "FIXED_PLAN"


class PlanStatus(StrEnum):
    """持久化计划状态。Phase 1 只产生 VALIDATED；REJECTED 预留给修复审计。"""

    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


# 保守优先级：ERROR > FAIL > INCONCLUSIVE > WARN > PASS
_PRIORITY: dict[TestStatus, int] = {
    TestStatus.PASS: 0,
    TestStatus.WARN: 1,
    TestStatus.INCONCLUSIVE: 2,
    TestStatus.FAIL: 3,
    TestStatus.ERROR: 4,
}


def aggregate_test_status(statuses: Iterable[TestStatus]) -> TestStatus:
    """按保守优先级聚合状态，保留最严重者。

    空集合返回 INCONCLUSIVE：没有证据不构成 PASS。
    """
    worst: TestStatus | None = None
    for status in statuses:
        if worst is None or _PRIORITY[status] > _PRIORITY[worst]:
            worst = status
    return worst if worst is not None else TestStatus.INCONCLUSIVE
