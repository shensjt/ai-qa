"""知识入库：manifest、hash、增量重建、陈旧清理（实现规格 §10.2 / §10.3）。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_qa.application.ports import EmbeddingProvider, KnowledgeRepository, VectorStore
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.knowledge import (
    EmbeddingMetadata,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeManifest,
    ManifestDocument,
)
from ai_qa.domain.models import utc_now
from ai_qa.knowledge.bm25 import BM25Index
from ai_qa.knowledge.chunking import chunk_markdown
from ai_qa.settings import KnowledgeSettings


class BuildReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    documents_total: int
    documents_indexed: int
    documents_skipped: int
    documents_deleted: int
    chunks_written: int
    degraded: bool
    degradation_reason: str | None = None
    collection: str | None = None
    smoke_hits: list[str] = Field(default_factory=list)


def load_manifest(path: Path) -> KnowledgeManifest:
    if not path.is_file():
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            f"知识库 manifest 不存在: {path}",
            details={"path": str(path)},
        )
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            f"无法读取 manifest: {path}",
            details={"path": str(path)},
        ) from exc
    except yaml.YAMLError as exc:
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            "manifest 不是合法 YAML",
            details={"path": str(path), "error": str(exc)[:300]},
        ) from exc
    if not isinstance(raw, dict):
        raise AiQaError(
            ErrorCode.CONFIG_INVALID, "manifest 顶层必须是映射", details={"path": str(path)}
        )
    try:
        manifest = KnowledgeManifest.model_validate(raw)
    except ValidationError as exc:
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            "manifest 校验失败",
            details={
                "path": str(path),
                "issues": [
                    {
                        "loc": ".".join(str(part) for part in error["loc"]),
                        "msg": error["msg"],
                    }
                    for error in exc.errors(include_url=False)[:20]
                ],
            },
        ) from exc

    seen: set[str] = set()
    duplicates: list[str] = []
    for document in manifest.documents:
        if document.source_path in seen:
            duplicates.append(document.source_path)
        seen.add(document.source_path)
    if duplicates:
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            "manifest 存在重复 source_path",
            details={"duplicates": sorted(set(duplicates))},
        )
    return manifest


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _doc_id(project_id: str, source_path: str) -> str:
    digest = hashlib.sha256(f"{project_id}:{source_path}".encode()).hexdigest()
    return f"doc_{digest[:32]}"


def _chunk_id(doc_id: str, section: str, chunk_index: int, content_hash: str) -> str:
    digest = hashlib.sha256(f"{doc_id}:{section}:{chunk_index}:{content_hash}".encode()).hexdigest()
    return f"chunk_{digest[:32]}"


class KnowledgeBuilder:
    def __init__(
        self,
        repository: KnowledgeRepository,
        settings: KnowledgeSettings,
        *,
        knowledge_root: Path,
        embedding_provider: EmbeddingProvider | None,
        vector_store_factory: Callable[[EmbeddingMetadata, bool], VectorStore] | None,
        embedding_error: str | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._knowledge_root = knowledge_root.expanduser().resolve()
        self._provider = embedding_provider
        self._store_factory = vector_store_factory
        self._embedding_error = embedding_error

    def build(
        self,
        manifest: KnowledgeManifest,
        *,
        project_id: str,
        rebuild: bool = False,
        smoke_query: str = "健康检查路径与 keep-alive 语义",
    ) -> BuildReport:
        if manifest.project_id != project_id:
            raise AiQaError(
                ErrorCode.CONFIG_INVALID,
                "manifest.project_id 与当前项目不一致",
                details={"manifest": manifest.project_id, "project": project_id},
            )

        degraded = self._provider is None or self._store_factory is None
        degradation_reason = self._embedding_error or (
            "Embedding 未配置，使用 BM25" if degraded else None
        )
        metadata: EmbeddingMetadata | None = None
        store: VectorStore | None = None

        if not degraded and self._provider is not None and self._store_factory is not None:
            try:
                metadata = self._provider.metadata()
                store = self._store_factory(metadata, rebuild)
            except AiQaError:
                raise
            except Exception as exc:
                degraded = True
                degradation_reason = f"{type(exc).__name__}: {exc}"[:300]
                metadata = None
                store = None

        if rebuild:
            old_chunk_ids = self._repository.delete_project(project_id)
            if store is not None:
                store.reset()
            elif old_chunk_ids:
                degradation_reason = degradation_reason or "无向量库，仅清理 SQLite chunk"

        existing_documents = {
            document.source_path: document
            for document in self._repository.list_documents(project_id)
        }
        manifest_paths = {document.source_path for document in manifest.documents}

        indexed = 0
        skipped = 0
        chunks_written = 0

        for manifest_document in manifest.documents:
            text = self._read_source(manifest_document.source_path)
            content_hash = _content_hash(text)
            existing = existing_documents.get(manifest_document.source_path)

            if existing is not None and existing.content_hash == content_hash and not rebuild:
                skipped += 1
                continue

            doc_id = (
                existing.doc_id
                if existing is not None
                else _doc_id(project_id, manifest_document.source_path)
            )
            old_chunk_ids = self._repository.delete_document(doc_id)
            if store is not None and old_chunk_ids:
                store.delete(old_chunk_ids)
            chunks = self._make_chunks(
                text=text,
                doc_id=doc_id,
                project_id=project_id,
                manifest_document=manifest_document,
                commit=manifest.commit,
            )
            document = KnowledgeDocument(
                doc_id=doc_id,
                project_id=project_id,
                source_path=manifest_document.source_path,
                source_type=manifest_document.source_type,
                priority=manifest_document.priority,
                commit=manifest.commit,
                content_hash=content_hash,
                updated_at=utc_now(),
            )
            self._repository.save_document(document, chunks)
            if store is not None and self._provider is not None and metadata is not None:
                try:
                    vectors = self._provider.embed_documents([chunk.text for chunk in chunks])
                    if len(vectors) != len(chunks):
                        raise ValueError("Embedding 数量与 chunk 数量不一致")
                    store.upsert(chunks, vectors)
                except Exception as exc:
                    degraded = True
                    degradation_reason = f"{type(exc).__name__}: {exc}"[:300]
                    store = None
                    metadata = None
            indexed += 1
            chunks_written += len(chunks)

        deleted = 0
        for source_path, document in existing_documents.items():
            if source_path in manifest_paths:
                continue
            old_chunk_ids = self._repository.delete_document(document.doc_id)
            if store is not None and old_chunk_ids:
                store.delete(old_chunk_ids)
            deleted += 1

        smoke_hits: list[str] = []
        if smoke_query:
            chunks = self._repository.list_chunks(project_id)
            hits = BM25Index(chunks).search(smoke_query, top_k=3)
            smoke_hits = [f"{hit.chunk.source_path}#{hit.chunk.section}" for hit in hits]

        return BuildReport(
            project_id=project_id,
            documents_total=len(manifest.documents),
            documents_indexed=indexed,
            documents_skipped=skipped,
            documents_deleted=deleted,
            chunks_written=chunks_written,
            degraded=degraded,
            degradation_reason=degradation_reason,
            collection=store.name if store is not None else None,
            smoke_hits=smoke_hits,
        )

    def _read_source(self, source_path: str) -> str:
        candidate = Path(source_path).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self._knowledge_root):
            raise AiQaError(
                ErrorCode.CONFIG_INVALID,
                f"知识源不在 knowledge_root 内: {source_path}",
                details={"path": str(resolved), "root": str(self._knowledge_root)},
            )
        if not resolved.is_file():
            raise AiQaError(
                ErrorCode.CONFIG_INVALID,
                f"知识源不存在: {source_path}",
                details={"path": str(resolved)},
            )
        return resolved.read_text(encoding="utf-8")

    def _make_chunks(
        self,
        *,
        text: str,
        doc_id: str,
        project_id: str,
        manifest_document: ManifestDocument,
        commit: str,
    ) -> list[KnowledgeChunk]:
        raw_chunks = chunk_markdown(
            text,
            target_tokens=self._settings.chunk_target_tokens,
            overlap_ratio=self._settings.chunk_overlap_ratio,
        )
        return [
            KnowledgeChunk(
                chunk_id=_chunk_id(doc_id, raw.section, raw.chunk_index, raw.content_hash),
                doc_id=doc_id,
                project_id=project_id,
                source_path=manifest_document.source_path,
                source_type=manifest_document.source_type,
                priority=manifest_document.priority,
                commit=commit,
                section=raw.section,
                chunk_index=raw.chunk_index,
                content_hash=raw.content_hash,
                text=raw.text,
            )
            for raw in raw_chunks
        ]
