"""知识库领域模型（实现规格 §10）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Priority = Literal["P0", "P1", "P2", "P3"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManifestDocument(_StrictModel):
    source_path: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    priority: Priority
    source_url: str | None = None


class KnowledgeManifest(_StrictModel):
    project_id: str = Field(min_length=1)
    commit: str = Field(min_length=1)
    documents: list[ManifestDocument] = Field(min_length=1)


class KnowledgeDocument(_StrictModel):
    doc_id: str
    project_id: str
    source_path: str
    source_type: str
    priority: Priority
    commit: str
    content_hash: str
    updated_at: datetime


class KnowledgeChunk(_StrictModel):
    chunk_id: str
    doc_id: str
    project_id: str
    source_path: str
    source_type: str
    priority: Priority
    commit: str
    section: str
    chunk_index: int = Field(ge=0)
    content_hash: str
    text: str


class EmbeddingMetadata(_StrictModel):
    model: str
    revision: str
    dim: int = Field(gt=0)
    normalize: bool
    distance: Literal["cosine", "l2", "ip"]
    schema_version: Literal["1.0"] = "1.0"


class RetrievalQuery(_StrictModel):
    query: str = Field(min_length=1)
    project_id: str
    top_k: int = Field(default=8, ge=1, le=50)
    source_types: list[str] | None = None
    commit: str | None = None
    max_context_chars: int = Field(default=8000, gt=0)


class RetrievalItem(_StrictModel):
    ref_id: str
    chunk_id: str
    doc_id: str
    source_path: str
    section: str
    chunk_index: int
    content_hash: str
    priority: Priority
    source_type: str
    score: float
    text: str


class RetrievalSnapshot(_StrictModel):
    query: RetrievalQuery
    items: list[RetrievalItem] = Field(default_factory=list)
    degraded: bool = False
    degradation_reason: str | None = None
    embedding: EmbeddingMetadata | None = None
    truncated_candidates: int = 0
    created_at: datetime
