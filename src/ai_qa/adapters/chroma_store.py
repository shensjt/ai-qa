"""Chroma 向量库适配器（实现规格 §10.4 / §10.5）。"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any

from ai_qa.application.ports import VectorStore
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.knowledge import EmbeddingMetadata, KnowledgeChunk, RetrievalQuery

_COLLECTION_METADATA_KEYS = (
    "project_id",
    "embedding_model",
    "embedding_revision",
    "embedding_dim",
    "distance",
    "normalize_embeddings",
    "schema_version",
)


def collection_name(project_id: str, model: str, dim: int, distance: str) -> str:
    model_alias = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    project = re.sub(r"[^a-z0-9]+", "_", project_id.lower()).strip("_")
    return f"{project}__{model_alias}__{dim}__{distance}"


class ChromaVectorStore(VectorStore):
    def __init__(
        self,
        path: Path,
        *,
        project_id: str,
        metadata: EmbeddingMetadata,
        reset: bool = False,
    ) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        if metadata.dim <= 0:
            raise ValueError("Embedding 维度必须为正数")
        self._metadata = metadata
        self._project_id = project_id
        self._name = collection_name(project_id, metadata.model, metadata.dim, metadata.distance)
        self._path = path.expanduser().resolve()
        self._path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(self._path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        if reset:
            with contextlib.suppress(Exception):  # collection 不存在时无需删除
                self._client.delete_collection(self._name)
        self._collection = self._open_collection(metadata)

    @property
    def name(self) -> str:
        return self._name

    @property
    def metadata(self) -> EmbeddingMetadata:
        return self._metadata

    def upsert(self, chunks: list[KnowledgeChunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunk 数量与向量数量不一致")
        if not chunks:
            return
        for vector in vectors:
            if len(vector) != self._metadata.dim:
                raise ValueError(f"向量维度 {len(vector)} 与配置 {self._metadata.dim} 不一致")
        self._collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            embeddings=vectors,
            documents=[chunk.text for chunk in chunks],
            metadatas=[self._chunk_metadata(chunk) for chunk in chunks],
        )

    def delete(self, chunk_ids: list[str]) -> None:
        if chunk_ids:
            self._collection.delete(ids=chunk_ids)

    def search(
        self, query: RetrievalQuery, vector: list[float], limit: int
    ) -> list[tuple[str, float]]:
        where: dict[str, Any] | None = None
        if query.source_types:
            where = {"source_type": {"$in": list(query.source_types)}}
        result = self._collection.query(
            query_embeddings=[vector],
            n_results=max(1, limit),
            where=where,
            include=["metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        scored: list[tuple[str, float]] = []
        for chunk_id, distance in zip(ids, distances, strict=False):
            similarity = (
                1.0 - float(distance) if self._metadata.distance == "cosine" else -float(distance)
            )
            scored.append((str(chunk_id), similarity))
        return scored

    def reset(self) -> None:
        self._client.delete_collection(name=self._name)
        self._collection = self._open_collection(self._metadata)

    def _open_collection(self, metadata: EmbeddingMetadata) -> Any:
        expected = self._collection_metadata(metadata)
        try:
            collection = self._client.get_or_create_collection(
                name=self._name,
                metadata=expected,
                configuration={
                    "hnsw": {"space": metadata.distance},
                },
            )
        except TypeError:
            collection = self._client.get_or_create_collection(name=self._name, metadata=expected)
        self._verify_metadata(collection, expected)
        return collection

    def _collection_metadata(self, metadata: EmbeddingMetadata) -> dict[str, Any]:
        return {
            "project_id": self._project_id,
            "embedding_model": metadata.model,
            "embedding_revision": metadata.revision,
            "embedding_dim": metadata.dim,
            "distance": metadata.distance,
            "normalize_embeddings": metadata.normalize,
            "schema_version": metadata.schema_version,
        }

    def _verify_metadata(self, collection: Any, expected: dict[str, Any]) -> None:
        actual = collection.metadata or {}
        mismatched = {
            key: {"expected": expected[key], "actual": actual.get(key)}
            for key in _COLLECTION_METADATA_KEYS
            if actual.get(key) != expected[key]
        }
        if mismatched:
            raise AiQaError(
                ErrorCode.CONFIG_INVALID,
                "Chroma Collection metadata 与当前 Embedding 配置不一致，请重建",
                details={"collection": self._name, "mismatched": mismatched},
            )

    @staticmethod
    def _chunk_metadata(chunk: KnowledgeChunk) -> dict[str, Any]:
        return {
            "project_id": chunk.project_id,
            "doc_id": chunk.doc_id,
            "source_path": chunk.source_path,
            "source_type": chunk.source_type,
            "priority": chunk.priority,
            "commit": chunk.commit,
            "section": chunk.section,
            "chunk_index": chunk.chunk_index,
            "content_hash": chunk.content_hash,
        }
