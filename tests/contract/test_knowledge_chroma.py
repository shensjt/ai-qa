from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ai_qa.adapters.chroma_store import ChromaVectorStore
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.knowledge import EmbeddingMetadata, KnowledgeManifest, RetrievalQuery
from ai_qa.knowledge.ingest import KnowledgeBuilder
from ai_qa.persistence.database import create_session_factory, create_sqlite_engine
from ai_qa.persistence.migrations import initialize_schema
from ai_qa.persistence.repositories import SqlKnowledgeRepository
from ai_qa.settings import load_settings

pytestmark = pytest.mark.contract

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ID = "cpp-webserver"


class FakeEmbeddingProvider:
    def __init__(self) -> None:
        self._metadata = EmbeddingMetadata(
            model="fake-embedding",
            revision="rev-1",
            dim=8,
            normalize=True,
            distance="cosine",
        )

    def metadata(self) -> EmbeddingMetadata:
        return self._metadata

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [digest[index] / 255.0 for index in range(8)]
        norm = sum(value * value for value in raw) ** 0.5 or 1.0
        return [value / norm for value in raw]


@pytest.fixture
def knowledge_env(tmp_path: Path) -> tuple[SqlKnowledgeRepository, Path, KnowledgeManifest]:
    engine: Engine = create_sqlite_engine(tmp_path / "kb.db")
    factory: sessionmaker[Session] = create_session_factory(engine)
    initialize_schema(engine, factory)
    repository = SqlKnowledgeRepository(factory)

    root = tmp_path / "knowledge"
    project = root / "project"
    project.mkdir(parents=True)
    runtime = project / "runtime.md"
    runtime.write_text(
        "# 运行契约\n\n健康检查使用 /healthz，返回 status 和 commit。\n",
        encoding="utf-8",
    )
    http = project / "http.md"
    http.write_text(
        "# HTTP 行为\n\nHTTP/1.1 默认 keep-alive，HEAD 不发送 body。\n",
        encoding="utf-8",
    )
    manifest = KnowledgeManifest.model_validate(
        {
            "project_id": PROJECT_ID,
            "commit": "abc123",
            "documents": [
                {
                    "source_path": str(runtime),
                    "source_type": "project_doc",
                    "priority": "P0",
                },
                {
                    "source_path": str(http),
                    "source_type": "project_doc",
                    "priority": "P0",
                },
            ],
        }
    )
    yield repository, root, manifest
    engine.dispose()


def _builder(
    repository: SqlKnowledgeRepository,
    root: Path,
    *,
    metadata_revision: str = "rev-1",
    chroma_path: Path,
) -> KnowledgeBuilder:
    settings = load_settings(REPO_ROOT / "config" / "settings.example.yaml").settings

    class Provider(FakeEmbeddingProvider):
        def metadata(self) -> EmbeddingMetadata:
            base = super().metadata()
            return base.model_copy(update={"revision": metadata_revision})

    def store_factory(metadata: EmbeddingMetadata, reset: bool) -> ChromaVectorStore:
        return ChromaVectorStore(
            chroma_path,
            project_id=PROJECT_ID,
            metadata=metadata,
            reset=reset,
        )

    return KnowledgeBuilder(
        repository,
        settings.knowledge,
        knowledge_root=root,
        embedding_provider=Provider(),
        vector_store_factory=store_factory,
    )


def test_build_search_and_incremental_update(
    knowledge_env: tuple[SqlKnowledgeRepository, Path, KnowledgeManifest],
    tmp_path: Path,
) -> None:
    repository, root, manifest = knowledge_env
    chroma_path = tmp_path / "chroma"
    builder = _builder(repository, root, chroma_path=chroma_path)

    report = builder.build(manifest, project_id=PROJECT_ID)

    assert report.degraded is False
    assert report.documents_indexed == 2
    assert report.chunks_written > 0
    assert report.collection

    provider = FakeEmbeddingProvider()
    store = ChromaVectorStore(chroma_path, project_id=PROJECT_ID, metadata=provider.metadata())
    scores = store.search(
        RetrievalQuery(query="健康检查", project_id=PROJECT_ID),
        provider.embed_query("健康检查"),
        limit=3,
    )
    assert scores
    chunk_ids = [chunk_id for chunk_id, _score in scores]
    chunks = repository.get_chunks(chunk_ids)
    assert chunks

    again = builder.build(manifest, project_id=PROJECT_ID)
    assert again.documents_skipped == 2
    assert again.documents_indexed == 0

    runtime_path = Path(manifest.documents[0].source_path)
    runtime_path.write_text("# 运行契约\n\n新的健康检查说明。\n", encoding="utf-8")
    changed = builder.build(manifest, project_id=PROJECT_ID)
    assert changed.documents_indexed == 1
    assert changed.documents_skipped == 1

    reduced = manifest.model_copy(update={"documents": manifest.documents[:1]})
    removed = builder.build(reduced, project_id=PROJECT_ID)
    assert removed.documents_deleted == 1


def test_metadata_mismatch_rejected(
    knowledge_env: tuple[SqlKnowledgeRepository, Path, KnowledgeManifest],
    tmp_path: Path,
) -> None:
    repository, root, manifest = knowledge_env
    chroma_path = tmp_path / "chroma"
    _builder(repository, root, chroma_path=chroma_path).build(manifest, project_id=PROJECT_ID)

    with pytest.raises(AiQaError) as excinfo:
        _builder(repository, root, metadata_revision="rev-2", chroma_path=chroma_path).build(
            manifest, project_id=PROJECT_ID
        )

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID
    assert "重建" in excinfo.value.message


def test_rebuild_clears_previous_index(
    knowledge_env: tuple[SqlKnowledgeRepository, Path, KnowledgeManifest],
    tmp_path: Path,
) -> None:
    repository, root, manifest = knowledge_env
    chroma_path = tmp_path / "chroma"
    builder = _builder(repository, root, chroma_path=chroma_path)
    builder.build(manifest, project_id=PROJECT_ID)

    rebuilt = builder.build(manifest, project_id=PROJECT_ID, rebuild=True)

    assert rebuilt.documents_indexed == 2
    assert rebuilt.documents_skipped == 0
    assert len(repository.list_documents(PROJECT_ID)) == 2
