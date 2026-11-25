"""测试计划领域模型与 plan_hash（实现规格 §6.2 / §9.2）。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ai_qa.domain.enums import PlanMode, PlanStatus


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanItem(_StrictModel):
    case_id: str = Field(min_length=1)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    refs: list[str] = Field(default_factory=list)


class BenchmarkSpec(_StrictModel):
    enabled: bool
    tool: Literal["wrk"]
    path: str
    duration_seconds: int = Field(gt=0)
    connections: int = Field(ge=1)
    threads: int = Field(ge=1)


class RunLimits(_StrictModel):
    max_cases: int = Field(default=6, ge=1)
    run_timeout_seconds: int = Field(default=120, gt=0)


class TestPlan(_StrictModel):
    """LLM/固定计划提供的原始计划；此处只做 Schema 校验。"""

    schema_version: Literal["1.0"]
    goal: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    items: list[PlanItem] = Field(min_length=1)
    benchmark: BenchmarkSpec | None = None
    limits: RunLimits


class ResolvedPlanItem(_StrictModel):
    """Catalog 与 PlanItem 合并后的内部执行项，不含自然语言步骤。"""

    case_id: str
    sequence_no: int = Field(ge=1)
    executor: str
    rule_set: str
    timeout_seconds: int = Field(gt=0)
    artifact_policy: str
    description: str
    params: dict[str, JsonValue] = Field(default_factory=dict)
    refs: list[str] = Field(default_factory=list)


class StoredPlan(_StrictModel):
    """通过校验并持久化的计划；MCP Server 只能按 plan_id/hash 加载它。"""

    plan_id: str
    plan_hash: str
    status: PlanStatus = PlanStatus.VALIDATED
    mode: PlanMode
    plan: TestPlan
    items: list[ResolvedPlanItem]
    limits: RunLimits
    benchmark: BenchmarkSpec | None = None
    repair_count: int = Field(default=0, ge=0)
    created_at: datetime


def canonical_plan_json(plan: TestPlan) -> str:
    """规范化 JSON：排序键、紧凑分隔符，保证同内容同 hash。"""
    data = plan.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_plan_hash(plan: TestPlan) -> str:
    digest = hashlib.sha256(canonical_plan_json(plan).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
