"""Executor 注册表：按 ResolvedPlanItem.executor 分发。"""

from __future__ import annotations

from collections.abc import Iterable

from ai_qa.application.ports import TestExecutor
from ai_qa.domain.errors import AiQaError, ErrorCode


class ExecutorRegistry:
    def __init__(self, executors: Iterable[TestExecutor]) -> None:
        self._executors: dict[str, TestExecutor] = {}
        for executor in executors:
            if executor.kind in self._executors:
                raise AiQaError(
                    ErrorCode.CONFIG_INVALID,
                    f"Executor 重复注册: {executor.kind}",
                )
            self._executors[executor.kind] = executor

    def get(self, kind: str) -> TestExecutor:
        executor = self._executors.get(kind)
        if executor is None:
            raise AiQaError(
                ErrorCode.INTERNAL_ERROR,
                f"未注册的 Executor: {kind}",
                details={"kind": kind, "registered": sorted(self._executors)},
            )
        return executor

    def kinds(self) -> tuple[str, ...]:
        return tuple(self._executors)
