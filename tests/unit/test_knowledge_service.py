from __future__ import annotations

from pathlib import Path

from ai_qa.application.knowledge_service import KnowledgeService
from ai_qa.domain.knowledge import (
    EmbeddingMetadata,
    KnowledgeChunk,
    RetrievalQuery,
)
from ai_qa.settings import load_settings


def _chunk(
    chunk_id: str,
    text: str,
    *,
    doc_id: str = "doc_1",
    index: int = 0,
    source_path: str = "knowledge/project/runtime-contract.md",
    source_type: str = "project_doc",
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        project_id="cpp-webserver",
        source_path=source_path,
        source_type=source_type,
        priority="P0",
        commit="abc123",
        section="健康检查",
        chunk_index=index,
        content_hash=f"hash-{chunk_id}",
        text=text,
    )


class FakeRepository:
    def __init__(self, chunks: list[KnowledgeChunk]) -> None:
        self.chunks = chunks

    def get_chunks(self, chunk_ids: list[str]) -> list[KnowledgeChunk]:
        return [chunk for chunk in self.chunks if chunk.chunk_id in chunk_ids]

    def list_chunks(self, project_id: str) -> list[KnowledgeChunk]:
        return [chunk for chunk in self.chunks if chunk.project_id == project_id]


class FakeProvider:
    def __init__(self) -> None:
        self._metadata = EmbeddingMetadata(
            model="fake", revision="rev", dim=1024, normalize=True, distance="cosine"
        )

    def metadata(self) -> EmbeddingMetadata:
        return self._metadata

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]


class FakeStore:
    def __init__(self, scored: list[tuple[str, float]]) -> None:
        self.scored = scored
        self.last_query: RetrievalQuery | None = None
        self._metadata = EmbeddingMetadata(
            model="fake", revision="rev", dim=1024, normalize=True, distance="cosine"
        )

    @property
    def name(self) -> str:
        return "fake"

    @property
    def metadata(self) -> EmbeddingMetadata:
        return self._metadata

    def upsert(self, chunks: list[KnowledgeChunk], vectors: list[list[float]]) -> None:
        pass

    def delete(self, chunk_ids: list[str]) -> None:
        pass

    def search(
        self, query: RetrievalQuery, vector: list[float], limit: int
    ) -> list[tuple[str, float]]:
        self.last_query = query
        return self.scored[:limit]

    def reset(self) -> None:
        pass


def _service(
    chunks: list[KnowledgeChunk],
    *,
    provider: FakeProvider | None = None,
    store: FakeStore | None = None,
) -> KnowledgeService:
    settings = load_settings(
        Path(__file__).resolve().parents[2] / "config" / "settings.example.yaml"
    ).settings

    def provider_factory() -> FakeProvider:
        if provider is None:
            raise RuntimeError("embedding model missing")
        return provider

    def store_factory(metadata: EmbeddingMetadata, reset: bool) -> FakeStore:
        assert store is not None
        return store

    return KnowledgeService(
        FakeRepository(chunks),
        settings,
        knowledge_root=Path(__file__).resolve().parents[2] / "knowledge",
        embedding_provider_factory=provider_factory,
        vector_store_factory=store_factory,
    )


def test_bm25_fallback_when_embedding_unavailable() -> None:
    chunks = [
        _chunk("chunk_a", "健康检查使用 /healthz 路径"),
        _chunk("chunk_b", "wrk 压测参数 -t -c -d"),
    ]

    snapshot = _service(chunks).search("健康检查", top_k=3)

    assert snapshot.degraded is True
    assert snapshot.degradation_reason
    assert "model missing" in snapshot.degradation_reason
    assert snapshot.embedding is None
    assert snapshot.items[0].chunk_id == "chunk_a"
    assert snapshot.items[0].ref_id == "ref_1"


def test_vector_path_builds_citations() -> None:
    chunks = [
        _chunk("chunk_a", "alpha"),
        _chunk("chunk_b", "beta", index=1),
    ]
    store = FakeStore([("chunk_b", 0.9), ("chunk_a", 0.8)])

    snapshot = _service(chunks, provider=FakeProvider(), store=store).search("query", top_k=2)

    assert snapshot.degraded is False
    assert snapshot.embedding is not None
    assert [item.chunk_id for item in snapshot.items] == ["chunk_b", "chunk_a"]
    assert [item.ref_id for item in snapshot.items] == ["ref_1", "ref_2"]
    assert store.last_query is not None


def test_adjacent_duplicate_chunks_are_deduped() -> None:
    chunks = [
        _chunk("chunk_a", "健康检查路径是 /healthz", index=0),
        _chunk("chunk_b", "健康检查路径是 /healthz 的说明", index=1),
    ]
    store = FakeStore([("chunk_a", 0.9), ("chunk_b", 0.8)])

    snapshot = _service(chunks, provider=FakeProvider(), store=store).search("query", top_k=5)

    assert [item.chunk_id for item in snapshot.items] == ["chunk_a"]


def test_context_budget_truncates() -> None:
    chunks = [
        _chunk("chunk_a", "A" * 200),
        _chunk("chunk_b", "B" * 200, index=1),
    ]
    store = FakeStore([("chunk_a", 0.9), ("chunk_b", 0.8)])

    snapshot = _service(chunks, provider=FakeProvider(), store=store).search(
        "query", top_k=2, max_context_chars=250
    )

    assert len(snapshot.items) == 1
    assert snapshot.truncated_candidates == 1


def test_source_type_filter_is_passed_to_store() -> None:
    chunks = [_chunk("chunk_a", "alpha")]
    store = FakeStore([("chunk_a", 0.9)])

    snapshot = _service(chunks, provider=FakeProvider(), store=store).search(
        "query", top_k=1, source_types=["standard"]
    )

    assert snapshot.query.source_types == ["standard"]
    assert store.last_query is not None
    assert store.last_query.source_types == ["standard"]


def test_missing_chunks_for_vector_ids_are_skipped() -> None:
    store = FakeStore([("chunk_missing", 0.9), ("chunk_a", 0.8)])

    snapshot = _service([_chunk("chunk_a", "alpha")], provider=FakeProvider(), store=store).search(
        "query", top_k=2
    )

    assert [item.chunk_id for item in snapshot.items] == ["chunk_a"]
