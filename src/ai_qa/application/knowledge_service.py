"""知识构建与检索编排（实现规格 §10.3 / §10.5）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ai_qa.application.ports import (
    EmbeddingProvider,
    KnowledgeRepository,
    VectorStore,
)
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.knowledge import (
    EmbeddingMetadata,
    KnowledgeChunk,
    RetrievalItem,
    RetrievalQuery,
    RetrievalSnapshot,
)
from ai_qa.domain.models import utc_now
from ai_qa.knowledge.bm25 import BM25Index
from ai_qa.knowledge.ingest import BuildReport, KnowledgeBuilder, load_manifest
from ai_qa.settings import AppSettings

_PRIORITY_BOOST = {"P0": 0.15, "P1": 0.08, "P2": 0.03, "P3": 0.0}


class KnowledgeService:
    def __init__(
        self,
        repository: KnowledgeRepository,
        settings: AppSettings,
        *,
        knowledge_root: Path,
        embedding_provider_factory: Callable[[], EmbeddingProvider],
        vector_store_factory: Callable[[EmbeddingMetadata, bool], VectorStore],
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._knowledge_root = knowledge_root
        self._embedding_factory = embedding_provider_factory
        self._store_factory = vector_store_factory

    def build(self, manifest_path: Path, *, rebuild: bool = False) -> BuildReport:
        manifest = load_manifest(manifest_path)
        provider: EmbeddingProvider | None = None
        embedding_error: str | None = None
        try:
            provider = self._embedding_factory()
        except Exception as exc:
            embedding_error = f"{type(exc).__name__}: {exc}"[:300]

        builder = KnowledgeBuilder(
            self._repository,
            self._settings.knowledge,
            knowledge_root=self._knowledge_root,
            embedding_provider=provider,
            vector_store_factory=self._store_factory,
            embedding_error=embedding_error,
        )
        return builder.build(
            manifest,
            project_id=self._settings.project.project_id,
            rebuild=rebuild,
        )

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        source_types: list[str] | None = None,
        commit: str | None = None,
        max_context_chars: int = 8000,
    ) -> RetrievalSnapshot:
        retrieval_query = RetrievalQuery(
            query=query,
            project_id=self._settings.project.project_id,
            top_k=top_k or self._settings.knowledge.top_k,
            source_types=source_types,
            commit=commit,
            max_context_chars=max_context_chars,
        )

        scored: list[tuple[KnowledgeChunk, float]] | None = None
        metadata: EmbeddingMetadata | None = None
        degraded = False
        degradation_reason: str | None = None

        try:
            provider = self._embedding_factory()
            metadata = provider.metadata()
            if metadata.dim != self._settings.knowledge.embedding_dim:
                raise AiQaError(
                    ErrorCode.CONFIG_INVALID,
                    "Embedding 维度与配置不一致",
                    details={
                        "provider": metadata.dim,
                        "configured": self._settings.knowledge.embedding_dim,
                    },
                )
            store = self._store_factory(metadata, False)
            vector = provider.embed_query(query)
            raw_scores = store.search(retrieval_query, vector, limit=retrieval_query.top_k * 3)
            chunk_ids = [chunk_id for chunk_id, _score in raw_scores]
            chunks_by_id = {
                chunk.chunk_id: chunk for chunk in self._repository.get_chunks(chunk_ids)
            }
            scored = [
                (chunks_by_id[chunk_id], score)
                for chunk_id, score in raw_scores
                if chunk_id in chunks_by_id
            ]
        except AiQaError:
            raise
        except Exception as exc:  # 显式 BM25 降级
            degraded = True
            degradation_reason = f"{type(exc).__name__}: {exc}"[:300]
            metadata = None
            all_chunks = self._repository.list_chunks(retrieval_query.project_id)
            hits = BM25Index(all_chunks).search(
                query,
                top_k=retrieval_query.top_k * 3,
                source_types=source_types,
            )
            scored = [(hit.chunk, hit.score) for hit in hits]

        items, truncated = self._build_items(scored or [], retrieval_query)
        return RetrievalSnapshot(
            query=retrieval_query,
            items=items,
            degraded=degraded,
            degradation_reason=degradation_reason,
            embedding=metadata,
            truncated_candidates=truncated,
            created_at=utc_now(),
        )

    def _build_items(
        self,
        scored: list[tuple[KnowledgeChunk, float]],
        query: RetrievalQuery,
    ) -> tuple[list[RetrievalItem], int]:
        ranked = sorted(
            (
                (chunk, score + _PRIORITY_BOOST.get(chunk.priority, 0.0))
                for chunk, score in scored
                if query.commit is None or chunk.commit == query.commit
            ),
            key=lambda item: (-item[1], item[0].chunk_id),
        )

        deduped: list[tuple[KnowledgeChunk, float]] = []
        last_by_doc: dict[str, KnowledgeChunk] = {}
        for chunk, score in ranked:
            previous = last_by_doc.get(chunk.doc_id)
            if (
                previous is not None
                and abs(previous.chunk_index - chunk.chunk_index) <= 1
                and (previous.text in chunk.text or chunk.text in previous.text)
            ):
                continue
            last_by_doc[chunk.doc_id] = chunk
            deduped.append((chunk, score))

        items: list[RetrievalItem] = []
        used_chars = 0
        for chunk, score in deduped:
            if len(items) >= query.top_k:
                break
            cost = len(chunk.text) + len(chunk.section)
            if items and used_chars + cost > query.max_context_chars:
                break
            used_chars += cost
            items.append(
                RetrievalItem(
                    ref_id=f"ref_{len(items) + 1}",
                    chunk_id=chunk.chunk_id,
                    doc_id=chunk.doc_id,
                    source_path=chunk.source_path,
                    section=chunk.section,
                    chunk_index=chunk.chunk_index,
                    content_hash=chunk.content_hash,
                    priority=chunk.priority,
                    source_type=chunk.source_type,
                    score=round(score, 6),
                    text=chunk.text,
                )
            )
        return items, len(deduped) - len(items)
