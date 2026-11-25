from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ai_qa.adapters.chroma_store import ChromaVectorStore
from ai_qa.adapters.qwen_embeddings import QwenEmbeddingProvider
from ai_qa.application.knowledge_service import KnowledgeService
from ai_qa.domain.knowledge import EmbeddingMetadata
from ai_qa.knowledge.eval import evaluate_cases, load_eval_cases
from ai_qa.knowledge.ingest import KnowledgeBuilder, load_manifest
from ai_qa.persistence.database import create_session_factory, create_sqlite_engine
from ai_qa.persistence.migrations import initialize_schema
from ai_qa.persistence.repositories import SqlKnowledgeRepository
from ai_qa.settings import AppSettings, load_settings

pytestmark = pytest.mark.eval

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "knowledge" / "manifests" / "cpp_webserver.yaml"
EVAL_CASES = REPO_ROOT / "tests" / "evals" / "rag_cases.yaml"


def _settings() -> AppSettings:
    return load_settings(REPO_ROOT / "config" / "settings.example.yaml").settings


def _repository(tmp_path: Path) -> tuple[SqlKnowledgeRepository, Engine]:
    engine: Engine = create_sqlite_engine(tmp_path / "eval.db")
    factory: sessionmaker[Session] = create_session_factory(engine)
    initialize_schema(engine, factory)
    return SqlKnowledgeRepository(factory), engine


def _bm25_service(
    tmp_path: Path,
) -> tuple[KnowledgeService, Engine]:
    settings = _settings()
    repository, engine = _repository(tmp_path)
    manifest = load_manifest(MANIFEST)
    builder = KnowledgeBuilder(
        repository,
        settings.knowledge,
        knowledge_root=REPO_ROOT / "knowledge",
        embedding_provider=None,
        vector_store_factory=None,
    )
    report = builder.build(manifest, project_id=settings.project.project_id)
    assert report.chunks_written > 0

    def failing_provider() -> None:
        raise RuntimeError("embedding disabled for BM25 eval")

    def unused_store(metadata: EmbeddingMetadata, reset: bool) -> None:
        raise AssertionError("BM25 eval 不应创建向量库")

    service = KnowledgeService(
        repository,
        settings,
        knowledge_root=REPO_ROOT / "knowledge",
        embedding_provider_factory=failing_provider,  # type: ignore[arg-type]
        vector_store_factory=unused_store,  # type: ignore[arg-type]
    )
    return service, engine


def test_bm25_rag_eval_metrics(tmp_path: Path) -> None:
    service, engine = _bm25_service(tmp_path)
    try:
        cases = load_eval_cases(EVAL_CASES)
        metrics = evaluate_cases(cases, lambda question: service.search(question, top_k=5))
        print(metrics.to_json(), flush=True)

        assert metrics.cases == len(cases)
        assert metrics.citation_valid_rate == 1.0
        assert metrics.no_result_rate <= 0.3
        assert metrics.recall_at_5 >= 0.4
    finally:
        engine.dispose()


@pytest.mark.skipif(
    os.environ.get("AI_QA_RUN_REAL_EMBEDDING") != "1",
    reason="设置 AI_QA_RUN_REAL_EMBEDDING=1 才运行真实 Qwen Embedding 评估",
)
def test_qwen_embedding_real_eval(tmp_path: Path) -> None:
    settings = _settings()
    repository, engine = _repository(tmp_path)
    try:
        provider = QwenEmbeddingProvider(
            settings.knowledge.embedding_model,
            device=settings.knowledge.embedding_device,
        )
        metadata = provider.metadata()
        assert metadata.dim == settings.knowledge.embedding_dim

        def store_factory(embedding: EmbeddingMetadata, reset: bool) -> ChromaVectorStore:
            return ChromaVectorStore(
                tmp_path / "chroma",
                project_id=settings.project.project_id,
                metadata=embedding,
                reset=reset,
            )

        manifest = load_manifest(MANIFEST)
        builder = KnowledgeBuilder(
            repository,
            settings.knowledge,
            knowledge_root=REPO_ROOT / "knowledge",
            embedding_provider=provider,
            vector_store_factory=store_factory,
        )
        report = builder.build(manifest, project_id=settings.project.project_id)
        assert report.degraded is False, report.degradation_reason

        service = KnowledgeService(
            repository,
            settings,
            knowledge_root=REPO_ROOT / "knowledge",
            embedding_provider_factory=lambda: provider,
            vector_store_factory=store_factory,
        )
        cases = load_eval_cases(EVAL_CASES)
        metrics = evaluate_cases(cases, lambda question: service.search(question, top_k=5))
        print(metrics.to_json(), flush=True)

        assert metrics.cases == len(cases)
        assert metrics.citation_valid_rate == 1.0
        assert metrics.recall_at_5 >= 0.5
    finally:
        engine.dispose()
