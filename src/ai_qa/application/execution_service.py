"""执行编排：启动目标 → 逐条执行 → 规则判定 → 持久化（实现规格 §12.6）。"""

from __future__ import annotations

import logging

from ai_qa.application.ports import (
    ArtifactRepository,
    ArtifactStore,
    ExecutionContext,
    ResultRepository,
    RuleEvaluator,
    TargetController,
)
from ai_qa.domain.models import TestRun
from ai_qa.domain.plans import StoredPlan
from ai_qa.domain.results import CaseResult
from ai_qa.execution.registry import ExecutorRegistry

LOGGER = logging.getLogger(__name__)


class ExecutionService:
    def __init__(
        self,
        target: TargetController,
        registry: ExecutorRegistry,
        evaluator: RuleEvaluator,
        artifact_store: ArtifactStore,
        result_repository: ResultRepository | None = None,
        artifact_repository: ArtifactRepository | None = None,
    ) -> None:
        self._target = target
        self._registry = registry
        self._evaluator = evaluator
        self._artifact_store = artifact_store
        self._result_repository = result_repository
        self._artifact_repository = artifact_repository

    def execute_plan(
        self,
        run: TestRun,
        plan: StoredPlan,
        *,
        secret_marker: str | None = None,
    ) -> list[CaseResult]:
        if plan.plan_id != run.plan_id:
            raise ValueError(f"run.plan_id={run.plan_id} 与 plan.plan_id={plan.plan_id} 不一致")

        context = ExecutionContext(
            run_id=run.run_id,
            host=self._target.host,
            port=self._target.port,
            health_path=self._target.health_path,
            artifact_store=self._artifact_store,
            secret_marker=secret_marker,
        )

        results: list[CaseResult] = []
        self._target.start()
        try:
            for item in plan.items:
                executor = self._registry.get(item.executor)
                facts = executor.execute(item, context)
                result = self._evaluator.evaluate_case(item, facts)
                self._persist(run, result)
                results.append(result)
        finally:
            self._target.stop()

        return results

    def _persist(self, run: TestRun, result: CaseResult) -> None:
        if self._result_repository is not None:
            self._result_repository.save_case_result(run.run_id, result)
        if self._artifact_repository is not None and result.facts is not None:
            for artifact in result.facts.artifacts:
                self._artifact_repository.save_artifact(artifact)
        LOGGER.info(
            "case finished",
            extra={
                "context": {
                    "run_id": run.run_id,
                    "case_id": result.case_id,
                    "execution_status": result.execution_status.value,
                    "test_status": result.test_status.value,
                }
            },
        )
