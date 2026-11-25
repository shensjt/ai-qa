"""Benchmark smoke 编排：启动目标 → wrk → 持久化 PerfResult。"""

from __future__ import annotations

from ai_qa.bootstrap import AppContext
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.models import TestRun
from ai_qa.domain.plans import StoredPlan
from ai_qa.domain.results import PerfResult
from ai_qa.execution.target_manager import TargetManager, target_config_from_settings
from ai_qa.execution.wrk_executor import WrkConfig, WrkExecutor


class BenchmarkService:
    def __init__(self, context: AppContext) -> None:
        self._context = context

    def run(self, run: TestRun, plan: StoredPlan) -> PerfResult:
        benchmark = plan.benchmark
        if benchmark is None or not benchmark.enabled:
            raise AiQaError(
                ErrorCode.POLICY_VIOLATION,
                "计划未包含已授权的 benchmark",
                details={"plan_id": plan.plan_id},
            )

        settings = self._context.settings
        target = TargetManager(
            target_config_from_settings(settings),
            log_dir=self._context.paths.artifacts / run.run_id / "target-logs",
        )
        wrk = WrkExecutor(
            WrkConfig(
                wrk_path=settings.benchmark.wrk_path,
                allowed_processes=tuple(settings.runtime.allowed_processes),
                max_duration_seconds=settings.benchmark.max_duration_seconds,
                max_connections=settings.benchmark.max_connections,
                max_threads=settings.benchmark.max_threads,
            )
        )
        url = f"http://{settings.target.host}:{settings.target.port}{benchmark.path}"
        target.start()
        try:
            perf = wrk.run(
                run_id=run.run_id,
                target_url=url,
                benchmark=benchmark,
                artifact_store=self._context.artifact_store,
            )
        finally:
            target.stop()

        self._context.perf_repository.save_perf_result(perf)
        for artifact in (perf.raw_stdout, perf.raw_stderr, perf.metrics_artifact):
            if artifact is not None:
                self._context.artifact_repository.save_artifact(artifact)
        return perf
