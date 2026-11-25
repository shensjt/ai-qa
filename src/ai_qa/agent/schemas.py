"""Agent 层结果模型。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ai_qa.application.ports import ToolExchange
from ai_qa.domain.models import TestRun
from ai_qa.domain.plans import StoredPlan
from ai_qa.domain.results import ArtifactRef, RunResult, ToolResult
from ai_qa.validation.validator import ValidationIssue


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanAttempt(_StrictModel):
    attempt: int = Field(ge=0)
    raw_text: str
    issues: list[ValidationIssue] = Field(default_factory=list)


class PlanProposal(_StrictModel):
    plan: StoredPlan
    attempts: list[PlanAttempt] = Field(default_factory=list)


class AgentExecution(_StrictModel):
    tool_result: ToolResult
    run_result: RunResult
    summary: str
    summary_artifact: ArtifactRef
    repair_artifact: ArtifactRef | None = None
    exchanges: list[ToolExchange] = Field(default_factory=list)


class AgentRunOutcome(_StrictModel):
    run: TestRun
    proposal: PlanProposal
    execution: AgentExecution
    trace_path: Path
    retrieval_degraded: bool = False
