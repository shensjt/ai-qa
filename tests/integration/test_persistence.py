from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ai_qa.domain.enums import (
    ExecutionStatus,
    PlanMode,
    RunState,
)
from ai_qa.domain.enums import (
    TestStatus as Status,
)
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.models import new_test_run, utc_now
from ai_qa.domain.plans import StoredPlan, compute_plan_hash
from ai_qa.domain.results import (
    ArtifactRef,
    AssertionResult,
    CaseFacts,
    CaseResult,
)
from ai_qa.persistence.database import create_session_factory, create_sqlite_engine
from ai_qa.persistence.migrations import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    initialize_schema,
    read_schema_version,
)
from ai_qa.persistence.repositories import (
    SqlArtifactRepository,
    SqlPlanRepository,
    SqlResultRepository,
    SqlRunRepository,
)
from ai_qa.persistence.tables import SchemaMetaRow
from ai_qa.validation.catalog import CaseCatalog
from ai_qa.validation.policy import ValidationPolicy
from ai_qa.validation.validator import PlanValidator, ValidationContext

TARGET = "cpp-webserver-local"


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = create_sqlite_engine(tmp_path / "ai_qa.db")
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    factory = create_session_factory(engine)
    initialize_schema(engine, factory)
    return factory


def _stored_plan(catalog: CaseCatalog) -> StoredPlan:
    policy = ValidationPolicy(
        expected_target_id=TARGET,
        max_cases=6,
        run_timeout_seconds=120,
        benchmark_allowed=False,
        benchmark_max_duration_seconds=30,
        benchmark_max_connections=64,
        benchmark_max_threads=4,
        max_plan_repairs=2,
    )
    plan: dict[str, Any] = {
        "schema_version": "1.0",
        "goal": "smoke",
        "target_id": TARGET,
        "items": [
            {"case_id": "http.get_index", "params": {}},
            {
                "case_id": "http.split_request",
                "params": {"split_sizes": [1, 2]},
                "refs": [],
            },
        ],
        "benchmark": None,
        "limits": {"max_cases": 6, "run_timeout_seconds": 60},
    }
    result = PlanValidator(catalog, policy).validate(
        plan, ValidationContext(mode=PlanMode.FIXED_PLAN)
    )
    assert result.plan is not None
    return result.plan


def test_schema_version_and_pragmas(engine: Engine, session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        assert read_schema_version(session) == SCHEMA_VERSION

    initialize_schema(engine, session_factory)

    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000


def test_schema_version_mismatch_rejected(
    engine: Engine, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        row = session.get(SchemaMetaRow, SCHEMA_VERSION_KEY)
        assert row is not None
        row.value = "999"
        session.commit()

    with pytest.raises(AiQaError) as excinfo:
        initialize_schema(engine, session_factory)

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID


def test_plan_roundtrip(session_factory: sessionmaker[Session], http_catalog: CaseCatalog) -> None:
    plan = _stored_plan(http_catalog)
    repository = SqlPlanRepository(session_factory)

    repository.save_plan(plan)
    loaded = repository.get_plan(plan.plan_id)

    assert loaded == plan
    assert compute_plan_hash(loaded.plan) == loaded.plan_hash


def test_missing_plan_raises(session_factory: sessionmaker[Session]) -> None:
    repository = SqlPlanRepository(session_factory)

    with pytest.raises(AiQaError) as excinfo:
        repository.get_plan("plan_missing")

    assert excinfo.value.code is ErrorCode.INTERNAL_ERROR


def test_run_roundtrip(session_factory: sessionmaker[Session], http_catalog: CaseCatalog) -> None:
    plan = _stored_plan(http_catalog)
    SqlPlanRepository(session_factory).save_plan(plan)
    run_repository = SqlRunRepository(session_factory)
    run = new_test_run(
        plan_id=plan.plan_id,
        target_id=TARGET,
        mode=PlanMode.FIXED_PLAN,
        commit="abc123",
        environment={"os": "linux"},
    )
    run_repository.create_run(run)

    loaded = run_repository.get_run(run.run_id)
    assert loaded == run

    run.state = RunState.RUNNING
    run.updated_at = utc_now()
    run.execution_status = ExecutionStatus.SUCCEEDED
    run.test_status = Status.WARN
    run_repository.update_run(run)

    updated = run_repository.get_run(run.run_id)
    assert updated.state is RunState.RUNNING
    assert updated.execution_status is ExecutionStatus.SUCCEEDED
    assert updated.test_status is Status.WARN


def test_case_results_roundtrip(
    engine: Engine,
    session_factory: sessionmaker[Session],
    http_catalog: CaseCatalog,
) -> None:
    plan = _stored_plan(http_catalog)
    SqlPlanRepository(session_factory).save_plan(plan)
    run = new_test_run(plan_id=plan.plan_id, target_id=TARGET, mode=PlanMode.FIXED_PLAN)
    SqlRunRepository(session_factory).create_run(run)

    now = utc_now()
    artifact = ArtifactRef(
        artifact_id="art_1",
        run_id=run.run_id,
        name="cases/http_get_index.txt",
        kind="case_log",
        uri=f"{run.run_id}/cases/http_get_index.txt",
        mime="text/plain",
        size_bytes=2,
        sha256="a" * 64,
        created_at=now,
    )
    facts = CaseFacts(
        case_id="http.get_index",
        started_at=now,
        ended_at=now,
        target={"host": "127.0.0.1"},
        request={"method": "GET", "path": "/"},
        observed={"status": 200},
        artifacts=[artifact],
    )
    result = CaseResult(
        case_id="http.get_index",
        execution_status=ExecutionStatus.SUCCEEDED,
        test_status=Status.PASS,
        facts=facts,
        assertions=[
            AssertionResult(
                rule_id="http.status",
                name="status is 200",
                status=Status.PASS,
                expected=200,
                observed=200,
                message="ok",
                evidence_refs=[artifact.artifact_id],
            )
        ],
    )

    repository = SqlResultRepository(session_factory)
    repository.save_case_result(run.run_id, result)

    loaded = repository.list_case_results(run.run_id)
    assert loaded == [result]

    artifacts = SqlArtifactRepository(session_factory)
    artifacts.save_artifact(artifact)
    assert artifacts.list_artifacts(run.run_id) == [artifact]
