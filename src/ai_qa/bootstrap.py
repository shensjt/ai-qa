"""Composition root（实现规格 §4.2）。

CLI 与 MCP Server 都通过这里装配 settings、SQLite、Catalog、Artifact Store；
Server 进程不依赖父进程内存对象，只依赖绝对路径环境变量。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ai_qa.adapters.chroma_store import ChromaVectorStore
from ai_qa.adapters.qwen_embeddings import QwenEmbeddingProvider
from ai_qa.application.execution_service import ExecutionService
from ai_qa.application.knowledge_service import KnowledgeService
from ai_qa.artifacts.store import FileArtifactStore
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.knowledge import EmbeddingMetadata
from ai_qa.execution.http_executor import HttpExecutor
from ai_qa.execution.registry import ExecutorRegistry
from ai_qa.execution.target_manager import TargetManager, target_config_from_settings
from ai_qa.persistence.database import create_session_factory, create_sqlite_engine
from ai_qa.persistence.migrations import initialize_schema
from ai_qa.persistence.repositories import (
    SqlArtifactRepository,
    SqlKnowledgeRepository,
    SqlPerfResultRepository,
    SqlPlanRepository,
    SqlResultRepository,
    SqlRunRepository,
)
from ai_qa.rules.evaluator import RuleEvaluatorImpl
from ai_qa.settings import AppSettings, load_settings, resolve_config_path
from ai_qa.validation.catalog import CaseCatalog, load_catalog

DEFAULT_SQLITE_PATH = ".data/ai_qa.db"
DEFAULT_ARTIFACT_ROOT = "artifacts"
DEFAULT_CATALOG_PATH = "config/cases/http.yaml"
DEFAULT_CHROMA_PATH = ".data/chroma"
DEFAULT_MANIFEST_PATH = "knowledge/manifests/cpp_webserver.yaml"

ENV_CONFIG = "AI_QA_CONFIG"
ENV_SQLITE = "SQLITE_PATH"
ENV_ARTIFACT_ROOT = "ARTIFACT_ROOT"
ENV_CATALOG = "AI_QA_CATALOG"
ENV_CHROMA = "CHROMA_PATH"
ENV_MANIFEST = "AI_QA_KNOWLEDGE_MANIFEST"


@dataclass(frozen=True)
class RuntimePaths:
    config: Path
    sqlite: Path
    artifacts: Path
    catalog: Path
    chroma: Path
    manifest: Path

    def server_env(self) -> dict[str, str]:
        """传给 MCP Server 的最小环境；只包含绝对路径和日志级别。"""
        return {
            ENV_CONFIG: str(self.config),
            ENV_SQLITE: str(self.sqlite),
            ENV_ARTIFACT_ROOT: str(self.artifacts),
            ENV_CATALOG: str(self.catalog),
            ENV_CHROMA: str(self.chroma),
            "PATH": os.environ.get("PATH", ""),
            "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO"),
        }


@dataclass
class AppContext:
    settings: AppSettings
    paths: RuntimePaths
    engine: Engine
    session_factory: sessionmaker[Session]
    plan_repository: SqlPlanRepository
    run_repository: SqlRunRepository
    result_repository: SqlResultRepository
    artifact_repository: SqlArtifactRepository
    perf_repository: SqlPerfResultRepository
    knowledge_repository: SqlKnowledgeRepository
    artifact_store: FileArtifactStore
    catalog: CaseCatalog

    def close(self) -> None:
        self.engine.dispose()


def resolve_runtime_paths(config_path: Path | None = None) -> RuntimePaths:
    config = resolve_config_path(config_path).expanduser().resolve()
    sqlite = Path(os.environ.get(ENV_SQLITE, DEFAULT_SQLITE_PATH)).expanduser().resolve()
    artifacts = (
        Path(os.environ.get(ENV_ARTIFACT_ROOT, DEFAULT_ARTIFACT_ROOT)).expanduser().resolve()
    )
    catalog = Path(os.environ.get(ENV_CATALOG, DEFAULT_CATALOG_PATH)).expanduser().resolve()
    chroma = Path(os.environ.get(ENV_CHROMA, DEFAULT_CHROMA_PATH)).expanduser().resolve()
    manifest = Path(os.environ.get(ENV_MANIFEST, DEFAULT_MANIFEST_PATH)).expanduser().resolve()
    return RuntimePaths(
        config=config,
        sqlite=sqlite,
        artifacts=artifacts,
        catalog=catalog,
        chroma=chroma,
        manifest=manifest,
    )


def build_app_context(config_path: Path | None = None) -> AppContext:
    paths = resolve_runtime_paths(config_path)
    settings = load_settings(paths.config).settings
    engine = create_sqlite_engine(paths.sqlite)
    session_factory = create_session_factory(engine)
    initialize_schema(engine, session_factory)
    catalog = load_catalog(paths.catalog)
    artifact_store = FileArtifactStore(paths.artifacts)
    return AppContext(
        settings=settings,
        paths=paths,
        engine=engine,
        session_factory=session_factory,
        plan_repository=SqlPlanRepository(session_factory),
        run_repository=SqlRunRepository(session_factory),
        result_repository=SqlResultRepository(session_factory),
        artifact_repository=SqlArtifactRepository(session_factory),
        perf_repository=SqlPerfResultRepository(session_factory),
        knowledge_repository=SqlKnowledgeRepository(session_factory),
        artifact_store=artifact_store,
        catalog=catalog,
    )


def build_execution_service(context: AppContext) -> ExecutionService:
    target = TargetManager(
        target_config_from_settings(context.settings),
        log_dir=context.paths.artifacts / "_target-logs",
    )
    return ExecutionService(
        target=target,
        registry=ExecutorRegistry([HttpExecutor()]),
        evaluator=RuleEvaluatorImpl(),
        artifact_store=context.artifact_store,
        result_repository=context.result_repository,
        artifact_repository=context.artifact_repository,
    )


def build_knowledge_service(context: AppContext) -> KnowledgeService:
    settings = context.settings
    if settings.knowledge.distance != "cosine":
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            "Phase 4 的 Qwen Embedding 只支持 cosine collection",
            details={"distance": settings.knowledge.distance},
        )
    knowledge_root = Path(settings.project.knowledge_root).expanduser()
    if not knowledge_root.is_absolute():
        knowledge_root = Path.cwd() / knowledge_root

    def embedding_factory() -> QwenEmbeddingProvider:
        return QwenEmbeddingProvider(
            settings.knowledge.embedding_model,
            device=settings.knowledge.embedding_device,
        )

    def store_factory(metadata: EmbeddingMetadata, reset: bool) -> ChromaVectorStore:
        return ChromaVectorStore(
            context.paths.chroma,
            project_id=settings.project.project_id,
            metadata=metadata,
            reset=reset,
        )

    return KnowledgeService(
        context.knowledge_repository,
        settings,
        knowledge_root=knowledge_root,
        embedding_provider_factory=embedding_factory,
        vector_store_factory=store_factory,
    )


def detect_secret_marker(settings: AppSettings) -> str | None:
    """受控 fixture 约定：www_root 的平级目录可放 outside_secret.txt。"""
    candidate = settings.target.www_root.expanduser().parent / "outside_secret.txt"
    if not candidate.is_file():
        return None
    try:
        text = candidate.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None
