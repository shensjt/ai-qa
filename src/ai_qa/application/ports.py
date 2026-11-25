"""应用层 Port 协议（实现规格 §7）。

Phase 1 只定义已经实现并由 Repository/ArtifactStore 满足的接口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ai_qa.domain.knowledge import (
    EmbeddingMetadata,
    KnowledgeChunk,
    KnowledgeDocument,
    RetrievalQuery,
)
from ai_qa.domain.models import TestRun
from ai_qa.domain.plans import ResolvedPlanItem, StoredPlan
from ai_qa.domain.results import ArtifactRef, CaseFacts, CaseResult


class PlanRepository(Protocol):
    def save_plan(self, plan: StoredPlan) -> None: ...

    def get_plan(self, plan_id: str) -> StoredPlan: ...


class RunRepository(Protocol):
    def create_run(self, run: TestRun) -> None: ...

    def update_run(self, run: TestRun) -> None: ...

    def get_run(self, run_id: str) -> TestRun: ...


class ResultRepository(Protocol):
    def save_case_result(self, run_id: str, result: CaseResult) -> None: ...

    def list_case_results(self, run_id: str) -> list[CaseResult]: ...


class ArtifactRepository(Protocol):
    def save_artifact(self, artifact: ArtifactRef, case_result_id: str | None = None) -> None: ...

    def list_artifacts(self, run_id: str) -> list[ArtifactRef]: ...


class ArtifactStore(Protocol):
    def write_bytes(
        self,
        run_id: str,
        name: str,
        data: bytes,
        kind: str,
        *,
        mime: str | None = None,
    ) -> ArtifactRef: ...

    def write_text(
        self,
        run_id: str,
        name: str,
        text: str,
        kind: str,
        *,
        mime: str | None = None,
    ) -> ArtifactRef: ...


@dataclass(frozen=True)
class ExecutionContext:
    """一次 Run 的执行上下文；由应用层构造，执行器只读。"""

    run_id: str
    host: str
    port: int
    health_path: str
    artifact_store: ArtifactStore
    secret_marker: str | None = None
    split_delay_seconds: float = 0.005

    @property
    def host_header(self) -> str:
        return f"{self.host}:{self.port}"


class TargetController(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    @property
    def host(self) -> str: ...

    @property
    def port(self) -> int: ...

    @property
    def health_path(self) -> str: ...


class TestExecutor(Protocol):
    kind: str

    def execute(self, item: ResolvedPlanItem, context: ExecutionContext) -> CaseFacts: ...


class RuleEvaluator(Protocol):
    def evaluate_case(self, item: ResolvedPlanItem, facts: CaseFacts) -> CaseResult: ...


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    call_id: str | None = None


class ToolDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None


class McpCallResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    is_error: bool
    structured: dict[str, Any] | None = None
    text: str = ""
    duration_ms: float = 0.0


class McpToolGateway(Protocol):
    async def list_tools(self) -> list[ToolDescriptor]: ...

    async def call_tool(
        self, call: ToolCall, *, timeout_seconds: float | None = None
    ) -> McpCallResult: ...


class KnowledgeRepository(Protocol):
    def get_document(self, project_id: str, source_path: str) -> KnowledgeDocument | None: ...

    def list_documents(self, project_id: str) -> list[KnowledgeDocument]: ...

    def save_document(self, document: KnowledgeDocument, chunks: list[KnowledgeChunk]) -> None: ...

    def delete_document(self, doc_id: str) -> list[str]: ...

    def delete_project(self, project_id: str) -> list[str]: ...

    def list_chunks(self, project_id: str) -> list[KnowledgeChunk]: ...

    def get_chunks(self, chunk_ids: list[str]) -> list[KnowledgeChunk]: ...


class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def metadata(self) -> EmbeddingMetadata: ...


class VectorStore(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def metadata(self) -> EmbeddingMetadata: ...

    def upsert(self, chunks: list[KnowledgeChunk], vectors: list[list[float]]) -> None: ...

    def delete(self, chunk_ids: list[str]) -> None: ...

    def search(
        self, query: RetrievalQuery, vector: list[float], limit: int
    ) -> list[tuple[str, float]]: ...

    def reset(self) -> None: ...


class ToolExchange(BaseModel):
    """一次模型工具调用及其观察结果，用于下一轮工具决策。"""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    result_text: str


class ToolDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls: list[ToolCall] = Field(default_factory=list)
    text: str = ""


class ChatPlanner(Protocol):
    async def complete(self, *, system: str, user: str) -> str: ...


class ToolCallingChat(Protocol):
    async def decide(
        self,
        *,
        system: str,
        user: str,
        tools: list[ToolDescriptor],
        history: list[ToolExchange],
    ) -> ToolDecision: ...
