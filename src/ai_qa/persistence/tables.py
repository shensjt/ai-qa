"""SQLite 表定义（实现规格 §15 的核心子集）。

Phase 1 只创建计划、运行、结果、断言和 Artifact 五组表；
PROJECT/TARGET/SUITE/KNOWLEDGE/TOOL_CALL/PERF 在引入对应功能的阶段再建。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SchemaMetaRow(Base):
    __tablename__ = "schema_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256), nullable=False)


class TestPlanRow(Base):
    __tablename__ = "test_plan"

    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    repair_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)
    limits_json: Mapped[str] = mapped_column(Text, nullable=False)
    benchmark_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)

    items: Mapped[list[PlanItemRow]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="PlanItemRow.sequence_no",
    )


class PlanItemRow(Base):
    __tablename__ = "plan_item"

    plan_item_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("test_plan.plan_id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    executor: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_set: Mapped[str] = mapped_column(String(128), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    refs_json: Mapped[str] = mapped_column(Text, nullable=False)

    plan: Mapped[TestPlanRow] = relationship(back_populates="items")


class TestRunRow(Base):
    __tablename__ = "test_run"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("test_plan.plan_id"), nullable=False, index=True
    )
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    test_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    environment_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class RunCaseResultRow(Base):
    __tablename__ = "run_case_result"

    case_result_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("test_run.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_status: Mapped[str] = mapped_column(String(16), nullable=False)
    test_status: Mapped[str] = mapped_column(String(16), nullable=False)
    facts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)

    assertions: Mapped[list[AssertionResultRow]] = relationship(
        back_populates="case_result",
        cascade="all, delete-orphan",
        order_by="AssertionResultRow.assertion_id",
    )


class AssertionResultRow(Base):
    __tablename__ = "assertion_result"

    assertion_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_result_id: Mapped[str] = mapped_column(
        ForeignKey("run_case_result.case_result_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    expected_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)

    case_result: Mapped[RunCaseResultRow] = relationship(back_populates="assertions")


class ArtifactRow(Base):
    __tablename__ = "artifact"

    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("test_run.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_result_id: Mapped[str | None] = mapped_column(
        ForeignKey("run_case_result.case_result_id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
