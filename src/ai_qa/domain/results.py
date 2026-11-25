"""执行事实、断言结果与统一工具返回（实现规格 §6.3 / §12.5）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ai_qa.domain.enums import ExecutionStatus, TestStatus
from ai_qa.domain.errors import ErrorInfo
from ai_qa.domain.models import TestRun


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactRef(_StrictModel):
    artifact_id: str
    run_id: str
    name: str
    kind: str
    uri: str
    mime: str
    size_bytes: int = Field(ge=0)
    sha256: str
    created_at: datetime


class CaseFacts(_StrictModel):
    """Executor 产出的客观事实；不包含 verdict。"""

    case_id: str
    started_at: datetime
    ended_at: datetime
    target: dict[str, JsonValue] = Field(default_factory=dict)
    request: dict[str, JsonValue] = Field(default_factory=dict)
    observed: dict[str, JsonValue] = Field(default_factory=dict)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    executor_error: ErrorInfo | None = None


class AssertionResult(_StrictModel):
    rule_id: str
    name: str
    status: TestStatus
    expected: JsonValue | None = None
    observed: JsonValue | None = None
    message: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class CaseResult(_StrictModel):
    case_id: str
    execution_status: ExecutionStatus
    test_status: TestStatus
    facts: CaseFacts | None = None
    assertions: list[AssertionResult] = Field(default_factory=list)
    error: ErrorInfo | None = None


class ToolCaseSummary(_StrictModel):
    """返回给模型的裁剪结果；完整事实留在数据库和 Artifact 中。"""

    case_id: str
    execution_status: ExecutionStatus
    test_status: TestStatus
    assertions: list[AssertionResult] = Field(default_factory=list)


class ToolResult(_StrictModel):
    call_id: str
    run_id: str
    tool: Literal["run_http_cases", "run_http_benchmark", "get_run_result"]
    execution_status: ExecutionStatus
    test_status: TestStatus
    facts: dict[str, JsonValue] = Field(default_factory=dict)
    case_results: list[ToolCaseSummary] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    error: ErrorInfo | None = None


class ToolFailureResult(_StrictModel):
    """工具参数/协议错误时的结构化返回；与断言失败区分。"""

    tool: str
    run_id: str | None = None
    execution_status: ExecutionStatus = ExecutionStatus.FAILED
    test_status: TestStatus = TestStatus.ERROR
    error: ErrorInfo


class PerfResult(_StrictModel):
    perf_result_id: str
    run_id: str
    tool: Literal["wrk"]
    tool_version: str
    command: list[str] = Field(default_factory=list)
    target_url: str
    config: dict[str, JsonValue] = Field(default_factory=dict)
    metrics: dict[str, JsonValue] = Field(default_factory=dict)
    environment: dict[str, JsonValue] = Field(default_factory=dict)
    exit_code: int | None = None
    raw_stdout: ArtifactRef
    raw_stderr: ArtifactRef | None = None
    metrics_artifact: ArtifactRef | None = None
    test_status: TestStatus
    notes: list[str] = Field(default_factory=list)
    created_at: datetime


class RunResult(_StrictModel):
    """get_run_result 的只读返回。"""

    run: TestRun
    case_results: list[CaseResult] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    perf: PerfResult | None = None
