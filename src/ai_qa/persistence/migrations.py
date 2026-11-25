"""Schema 版本与初始化（实现规格 §15.2 末尾）。"""

from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.persistence.tables import Base, SchemaMetaRow

SCHEMA_VERSION = "3"
SCHEMA_VERSION_KEY = "schema_version"


def initialize_schema(engine: Engine, session_factory: sessionmaker[Session]) -> None:
    """建表并记录 schema 版本；版本不一致时拒绝复用。"""
    Base.metadata.create_all(engine)
    with session_factory() as session:
        row = session.get(SchemaMetaRow, SCHEMA_VERSION_KEY)
        if row is None:
            session.add(SchemaMetaRow(key=SCHEMA_VERSION_KEY, value=SCHEMA_VERSION))
            session.commit()
            return
        if row.value in {"1", "2"}:
            # v1/v2 -> v3 都是纯增量：create_all 已补建新表。
            row.value = SCHEMA_VERSION
            session.commit()
            return
        if row.value != SCHEMA_VERSION:
            raise AiQaError(
                ErrorCode.CONFIG_INVALID,
                f"数据库 schema 版本不兼容: {row.value} != {SCHEMA_VERSION}",
                details={"database_schema_version": row.value, "expected": SCHEMA_VERSION},
            )


def read_schema_version(session: Session) -> str | None:
    row = session.get(SchemaMetaRow, SCHEMA_VERSION_KEY)
    return row.value if row is not None else None
