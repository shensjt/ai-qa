"""运行实例模型与 ID 生成。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ai_qa.domain.enums import ExecutionStatus, PlanMode, RunState, TestStatus


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TestRun(_StrictModel):
    run_id: str
    plan_id: str
    target_id: str
    mode: PlanMode
    state: RunState = RunState.CREATED
    execution_status: ExecutionStatus | None = None
    test_status: TestStatus | None = None
    commit: str | None = None
    environment: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_test_run(
    *,
    plan_id: str,
    target_id: str,
    mode: PlanMode,
    commit: str | None = None,
    environment: dict[str, JsonValue] | None = None,
) -> TestRun:
    now = utc_now()
    return TestRun(
        run_id=new_id("run"),
        plan_id=plan_id,
        target_id=target_id,
        mode=mode,
        state=RunState.CREATED,
        commit=commit,
        environment=dict(environment or {}),
        created_at=now,
        updated_at=now,
    )
