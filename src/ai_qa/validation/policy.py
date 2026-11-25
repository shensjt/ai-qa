"""服务端 Policy：计划与压测资源的上限来源（实现规格 §8.3 / §11.3 / §12.3）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ai_qa.settings import AppSettings


class ValidationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_target_id: str
    max_cases: int = Field(ge=1)
    run_timeout_seconds: int = Field(gt=0)
    benchmark_allowed: bool
    benchmark_max_duration_seconds: int = Field(gt=0)
    benchmark_max_connections: int = Field(ge=1)
    benchmark_max_threads: int = Field(ge=1)
    max_plan_repairs: int = Field(ge=0)


def policy_from_settings(
    settings: AppSettings, *, allow_benchmark: bool = False
) -> ValidationPolicy:
    """CLI 显式授权或配置默认开启时，压测才可用；资源上限始终取配置值。"""
    return ValidationPolicy(
        expected_target_id=settings.target.target_id,
        max_cases=settings.runtime.max_cases,
        run_timeout_seconds=settings.runtime.run_timeout_seconds,
        benchmark_allowed=allow_benchmark or settings.benchmark.enabled_by_default,
        benchmark_max_duration_seconds=settings.benchmark.max_duration_seconds,
        benchmark_max_connections=settings.benchmark.max_connections,
        benchmark_max_threads=settings.benchmark.max_threads,
        max_plan_repairs=settings.agent.max_plan_repairs,
    )
