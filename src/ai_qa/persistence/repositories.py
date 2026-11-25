"""SQLite Repository 实现（实现规格 §7 / §15）。

每个方法创建独立 Session；不跨线程或 asyncio.to_thread 共享 Session。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ai_qa.domain.enums import (
    ExecutionStatus,
    PlanMode,
    PlanStatus,
    RunState,
    TestStatus,
)
from ai_qa.domain.errors import AiQaError, ErrorCode, ErrorInfo
from ai_qa.domain.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    Priority,
)
from ai_qa.domain.models import TestRun, new_id, utc_now
from ai_qa.domain.plans import (
    BenchmarkSpec,
    ResolvedPlanItem,
    RunLimits,
    StoredPlan,
    TestPlan,
)
from ai_qa.domain.results import (
    ArtifactRef,
    AssertionResult,
    CaseFacts,
    CaseResult,
    PerfResult,
)
from ai_qa.persistence.tables import (
    ArtifactRow,
    AssertionResultRow,
    KnowledgeChunkRow,
    KnowledgeDocumentRow,
    PerfResultRow,
    PlanItemRow,
    RunCaseResultRow,
    TestPlanRow,
    TestRunRow,
)


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_json(text: str) -> Any:
    return json.loads(text)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _missing(entity: str, identifier: str) -> AiQaError:
    return AiQaError(
        ErrorCode.INTERNAL_ERROR,
        f"{entity}不存在: {identifier}",
        details={"entity": entity, "id": identifier},
    )


def _duration_ms(facts: CaseFacts | None) -> int | None:
    if facts is None:
        return None
    return max(0, int((facts.ended_at - facts.started_at).total_seconds() * 1000))


class SqlPlanRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_plan(self, plan: StoredPlan) -> None:
        row = TestPlanRow(
            plan_id=plan.plan_id,
            target_id=plan.plan.target_id,
            goal=plan.plan.goal,
            status=plan.status.value,
            mode=plan.mode.value,
            plan_hash=plan.plan_hash,
            repair_count=plan.repair_count,
            plan_json=_dump_json(plan.plan.model_dump(mode="json")),
            limits_json=_dump_json(plan.limits.model_dump(mode="json")),
            benchmark_json=(
                _dump_json(plan.benchmark.model_dump(mode="json"))
                if plan.benchmark is not None
                else None
            ),
            created_at=_iso(plan.created_at),
        )
        for item in plan.items:
            row.items.append(
                PlanItemRow(
                    plan_item_id=f"{plan.plan_id}:{item.sequence_no}",
                    plan_id=plan.plan_id,
                    case_id=item.case_id,
                    sequence_no=item.sequence_no,
                    executor=item.executor,
                    rule_set=item.rule_set,
                    timeout_seconds=item.timeout_seconds,
                    artifact_policy=item.artifact_policy,
                    description=item.description,
                    params_json=_dump_json(item.params),
                    refs_json=_dump_json(item.refs),
                )
            )

        with self._session_factory() as session:
            session.add(row)
            session.commit()

    def get_plan(self, plan_id: str) -> StoredPlan:
        with self._session_factory() as session:
            row = session.get(TestPlanRow, plan_id)
            if row is None:
                raise _missing("计划", plan_id)

            items = [
                ResolvedPlanItem(
                    case_id=item.case_id,
                    sequence_no=item.sequence_no,
                    executor=item.executor,
                    rule_set=item.rule_set,
                    timeout_seconds=item.timeout_seconds,
                    artifact_policy=item.artifact_policy,
                    description=item.description,
                    params=_load_json(item.params_json),
                    refs=list(_load_json(item.refs_json)),
                )
                for item in row.items
            ]
            return StoredPlan(
                plan_id=row.plan_id,
                plan_hash=row.plan_hash,
                status=PlanStatus(row.status),
                mode=PlanMode(row.mode),
                plan=TestPlan.model_validate(_load_json(row.plan_json)),
                items=items,
                limits=RunLimits.model_validate(_load_json(row.limits_json)),
                benchmark=(
                    BenchmarkSpec.model_validate(_load_json(row.benchmark_json))
                    if row.benchmark_json is not None
                    else None
                ),
                repair_count=row.repair_count,
                created_at=_to_dt(row.created_at),
            )


class SqlRunRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_run(self, run: TestRun) -> None:
        row = TestRunRow(
            run_id=run.run_id,
            plan_id=run.plan_id,
            target_id=run.target_id,
            mode=run.mode.value,
            state=run.state.value,
            execution_status=(
                run.execution_status.value if run.execution_status is not None else None
            ),
            test_status=run.test_status.value if run.test_status is not None else None,
            commit=run.commit,
            environment_json=_dump_json(run.environment),
            created_at=_iso(run.created_at),
            updated_at=_iso(run.updated_at),
        )
        with self._session_factory() as session:
            session.add(row)
            session.commit()

    def update_run(self, run: TestRun) -> None:
        with self._session_factory() as session:
            row = session.get(TestRunRow, run.run_id)
            if row is None:
                raise _missing("运行", run.run_id)
            row.state = run.state.value
            row.execution_status = (
                run.execution_status.value if run.execution_status is not None else None
            )
            row.test_status = run.test_status.value if run.test_status is not None else None
            row.commit = run.commit
            row.environment_json = _dump_json(run.environment)
            row.updated_at = _iso(run.updated_at)
            session.commit()

    def get_run(self, run_id: str) -> TestRun:
        with self._session_factory() as session:
            row = session.get(TestRunRow, run_id)
            if row is None:
                raise _missing("运行", run_id)
            return TestRun(
                run_id=row.run_id,
                plan_id=row.plan_id,
                target_id=row.target_id,
                mode=PlanMode(row.mode),
                state=RunState(row.state),
                execution_status=(
                    ExecutionStatus(row.execution_status)
                    if row.execution_status is not None
                    else None
                ),
                test_status=(TestStatus(row.test_status) if row.test_status is not None else None),
                commit=row.commit,
                environment=_load_json(row.environment_json),
                created_at=_to_dt(row.created_at),
                updated_at=_to_dt(row.updated_at),
            )


class SqlResultRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_case_result(self, run_id: str, result: CaseResult) -> None:
        row = RunCaseResultRow(
            case_result_id=new_id("case"),
            run_id=run_id,
            case_id=result.case_id,
            execution_status=result.execution_status.value,
            test_status=result.test_status.value,
            facts_json=(
                _dump_json(result.facts.model_dump(mode="json"))
                if result.facts is not None
                else None
            ),
            error_json=(
                _dump_json(result.error.model_dump(mode="json"))
                if result.error is not None
                else None
            ),
            duration_ms=_duration_ms(result.facts),
            created_at=_iso(utc_now()),
        )
        for assertion in result.assertions:
            row.assertions.append(
                AssertionResultRow(
                    assertion_id=new_id("assert"),
                    case_result_id=row.case_result_id,
                    rule_id=assertion.rule_id,
                    name=assertion.name,
                    status=assertion.status.value,
                    expected_json=(
                        _dump_json(assertion.expected) if assertion.expected is not None else None
                    ),
                    observed_json=(
                        _dump_json(assertion.observed) if assertion.observed is not None else None
                    ),
                    message=assertion.message,
                    evidence_json=_dump_json(assertion.evidence_refs),
                )
            )

        with self._session_factory() as session:
            session.add(row)
            session.commit()

    def list_case_results(self, run_id: str) -> list[CaseResult]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(RunCaseResultRow)
                    .where(RunCaseResultRow.run_id == run_id)
                    .order_by(RunCaseResultRow.created_at, RunCaseResultRow.case_result_id)
                )
                .scalars()
                .all()
            )
            return [self._to_case_result(row) for row in rows]

    def _to_case_result(self, row: RunCaseResultRow) -> CaseResult:
        assertions = [
            AssertionResult(
                rule_id=assertion.rule_id,
                name=assertion.name,
                status=TestStatus(assertion.status),
                expected=(
                    _load_json(assertion.expected_json)
                    if assertion.expected_json is not None
                    else None
                ),
                observed=(
                    _load_json(assertion.observed_json)
                    if assertion.observed_json is not None
                    else None
                ),
                message=assertion.message,
                evidence_refs=list(_load_json(assertion.evidence_json)),
            )
            for assertion in row.assertions
        ]
        return CaseResult(
            case_id=row.case_id,
            execution_status=ExecutionStatus(row.execution_status),
            test_status=TestStatus(row.test_status),
            facts=(
                CaseFacts.model_validate(_load_json(row.facts_json))
                if row.facts_json is not None
                else None
            ),
            assertions=assertions,
            error=(
                ErrorInfo.model_validate(_load_json(row.error_json))
                if row.error_json is not None
                else None
            ),
        )


class SqlArtifactRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_artifact(self, artifact: ArtifactRef, case_result_id: str | None = None) -> None:
        row = ArtifactRow(
            artifact_id=artifact.artifact_id,
            run_id=artifact.run_id,
            case_result_id=case_result_id,
            name=artifact.name,
            kind=artifact.kind,
            uri=artifact.uri,
            mime=artifact.mime,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
            created_at=_iso(artifact.created_at),
        )
        with self._session_factory() as session:
            session.add(row)
            session.commit()

    def list_artifacts(self, run_id: str) -> list[ArtifactRef]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(ArtifactRow)
                    .where(ArtifactRow.run_id == run_id)
                    .order_by(ArtifactRow.created_at, ArtifactRow.artifact_id)
                )
                .scalars()
                .all()
            )
            return [
                ArtifactRef(
                    artifact_id=row.artifact_id,
                    run_id=row.run_id,
                    name=row.name,
                    kind=row.kind,
                    uri=row.uri,
                    mime=row.mime,
                    size_bytes=row.size_bytes,
                    sha256=row.sha256,
                    created_at=_to_dt(row.created_at),
                )
                for row in rows
            ]


def _to_knowledge_document(row: KnowledgeDocumentRow) -> KnowledgeDocument:
    return KnowledgeDocument(
        doc_id=row.doc_id,
        project_id=row.project_id,
        source_path=row.source_path,
        source_type=row.source_type,
        priority=cast(Priority, row.priority),
        commit=row.commit,
        content_hash=row.content_hash,
        updated_at=_to_dt(row.updated_at),
    )


def _to_knowledge_chunk(row: KnowledgeChunkRow) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=row.chunk_id,
        doc_id=row.doc_id,
        project_id=row.project_id,
        source_path=row.source_path,
        source_type=row.source_type,
        priority=cast(Priority, row.priority),
        commit=row.commit,
        section=row.section,
        chunk_index=row.chunk_index,
        content_hash=row.content_hash,
        text=row.text,
    )


class SqlKnowledgeRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_document(self, project_id: str, source_path: str) -> KnowledgeDocument | None:
        with self._session_factory() as session:
            row = session.execute(
                select(KnowledgeDocumentRow).where(
                    KnowledgeDocumentRow.project_id == project_id,
                    KnowledgeDocumentRow.source_path == source_path,
                )
            ).scalar_one_or_none()
            return _to_knowledge_document(row) if row is not None else None

    def list_documents(self, project_id: str) -> list[KnowledgeDocument]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(KnowledgeDocumentRow)
                    .where(KnowledgeDocumentRow.project_id == project_id)
                    .order_by(KnowledgeDocumentRow.source_path)
                )
                .scalars()
                .all()
            )
            return [_to_knowledge_document(row) for row in rows]

    def save_document(self, document: KnowledgeDocument, chunks: list[KnowledgeChunk]) -> None:
        with self._session_factory() as session:
            existing = session.get(KnowledgeDocumentRow, document.doc_id)
            if existing is None:
                session.add(
                    KnowledgeDocumentRow(
                        doc_id=document.doc_id,
                        project_id=document.project_id,
                        source_path=document.source_path,
                        source_type=document.source_type,
                        priority=document.priority,
                        commit=document.commit,
                        content_hash=document.content_hash,
                        updated_at=_iso(document.updated_at),
                    )
                )
            else:
                existing.project_id = document.project_id
                existing.source_path = document.source_path
                existing.source_type = document.source_type
                existing.priority = document.priority
                existing.commit = document.commit
                existing.content_hash = document.content_hash
                existing.updated_at = _iso(document.updated_at)

            session.execute(
                delete(KnowledgeChunkRow).where(KnowledgeChunkRow.doc_id == document.doc_id)
            )
            for chunk in chunks:
                session.add(
                    KnowledgeChunkRow(
                        chunk_id=chunk.chunk_id,
                        doc_id=chunk.doc_id,
                        project_id=chunk.project_id,
                        source_path=chunk.source_path,
                        source_type=chunk.source_type,
                        priority=chunk.priority,
                        commit=chunk.commit,
                        section=chunk.section,
                        chunk_index=chunk.chunk_index,
                        content_hash=chunk.content_hash,
                        text=chunk.text,
                    )
                )
            session.commit()

    def delete_document(self, doc_id: str) -> list[str]:
        with self._session_factory() as session:
            chunk_ids = list(
                session.execute(
                    select(KnowledgeChunkRow.chunk_id).where(KnowledgeChunkRow.doc_id == doc_id)
                )
                .scalars()
                .all()
            )
            session.execute(delete(KnowledgeChunkRow).where(KnowledgeChunkRow.doc_id == doc_id))
            session.execute(
                delete(KnowledgeDocumentRow).where(KnowledgeDocumentRow.doc_id == doc_id)
            )
            session.commit()
            return chunk_ids

    def delete_project(self, project_id: str) -> list[str]:
        with self._session_factory() as session:
            chunk_ids = list(
                session.execute(
                    select(KnowledgeChunkRow.chunk_id).where(
                        KnowledgeChunkRow.project_id == project_id
                    )
                )
                .scalars()
                .all()
            )
            session.execute(
                delete(KnowledgeChunkRow).where(KnowledgeChunkRow.project_id == project_id)
            )
            session.execute(
                delete(KnowledgeDocumentRow).where(KnowledgeDocumentRow.project_id == project_id)
            )
            session.commit()
            return chunk_ids

    def list_chunks(self, project_id: str) -> list[KnowledgeChunk]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(KnowledgeChunkRow)
                    .where(KnowledgeChunkRow.project_id == project_id)
                    .order_by(KnowledgeChunkRow.doc_id, KnowledgeChunkRow.chunk_index)
                )
                .scalars()
                .all()
            )
            return [_to_knowledge_chunk(row) for row in rows]

    def get_chunks(self, chunk_ids: list[str]) -> list[KnowledgeChunk]:
        if not chunk_ids:
            return []
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(KnowledgeChunkRow).where(KnowledgeChunkRow.chunk_id.in_(chunk_ids))
                )
                .scalars()
                .all()
            )
            return [_to_knowledge_chunk(row) for row in rows]


class SqlPerfResultRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_perf_result(self, perf: PerfResult) -> None:
        row = PerfResultRow(
            perf_result_id=perf.perf_result_id,
            run_id=perf.run_id,
            tool=perf.tool,
            tool_version=perf.tool_version,
            command_json=_dump_json(perf.command),
            target_url=perf.target_url,
            config_json=_dump_json(perf.config),
            metrics_json=_dump_json(perf.metrics),
            environment_json=_dump_json(perf.environment),
            exit_code=perf.exit_code,
            stdout_artifact_json=_dump_json(perf.raw_stdout.model_dump(mode="json")),
            stderr_artifact_json=(
                _dump_json(perf.raw_stderr.model_dump(mode="json"))
                if perf.raw_stderr is not None
                else None
            ),
            metrics_artifact_json=(
                _dump_json(perf.metrics_artifact.model_dump(mode="json"))
                if perf.metrics_artifact is not None
                else None
            ),
            test_status=perf.test_status.value,
            notes_json=_dump_json(perf.notes),
            created_at=_iso(perf.created_at),
        )
        with self._session_factory() as session:
            existing = session.get(PerfResultRow, perf.perf_result_id)
            if existing is None:
                session.add(row)
            else:
                session.merge(row)
            session.commit()

    def get_perf_result(self, run_id: str) -> PerfResult | None:
        with self._session_factory() as session:
            row = session.execute(
                select(PerfResultRow).where(PerfResultRow.run_id == run_id)
            ).scalar_one_or_none()
            if row is None:
                return None
            return PerfResult(
                perf_result_id=row.perf_result_id,
                run_id=row.run_id,
                tool=row.tool,
                tool_version=row.tool_version,
                command=_load_json(row.command_json),
                target_url=row.target_url,
                config=_load_json(row.config_json),
                metrics=_load_json(row.metrics_json),
                environment=_load_json(row.environment_json),
                exit_code=row.exit_code,
                raw_stdout=ArtifactRef.model_validate(_load_json(row.stdout_artifact_json)),
                raw_stderr=(
                    ArtifactRef.model_validate(_load_json(row.stderr_artifact_json))
                    if row.stderr_artifact_json is not None
                    else None
                ),
                metrics_artifact=(
                    ArtifactRef.model_validate(_load_json(row.metrics_artifact_json))
                    if row.metrics_artifact_json is not None
                    else None
                ),
                test_status=TestStatus(row.test_status),
                notes=list(_load_json(row.notes_json)),
                created_at=_to_dt(row.created_at),
            )
