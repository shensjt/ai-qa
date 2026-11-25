"""Case Catalog 加载与完整性校验（实现规格 §9）。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.validation.params import ParamSpec, validate_param_spec


class CaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_.-]*$")
    executor: Literal["http"]
    description: str = Field(min_length=1)
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    timeout_seconds: int = Field(gt=0)
    rule_set: str = Field(min_length=1)
    artifact_policy: Literal["http_exchange", "raw_http_exchange"]
    enabled: bool = True


class _CatalogDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    suite_id: str = Field(min_length=1)
    cases: list[CaseSpec] = Field(min_length=1)


@dataclass(frozen=True)
class CaseCatalog:
    schema_version: str
    suite_id: str
    cases: Mapping[str, CaseSpec]

    def get(self, case_id: str) -> CaseSpec | None:
        return self.cases.get(case_id)

    def case_ids(self) -> tuple[str, ...]:
        return tuple(self.cases)


def _validation_issues(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "loc": ".".join(str(part) for part in error["loc"]),
            "msg": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors(include_url=False)[:20]
    ]


def _load_mapping(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            f"Catalog 文件不存在: {path}",
            details={"path": str(path)},
        )
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            f"无法读取 Catalog: {path}",
            details={"path": str(path)},
        ) from exc
    except yaml.YAMLError as exc:
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            "Catalog 不是合法 YAML",
            details={"path": str(path), "error": str(exc)[:500]},
        ) from exc
    if not isinstance(raw, dict):
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            "Catalog 顶层必须是映射",
            details={"path": str(path)},
        )
    return raw


def load_catalog(path: Path) -> CaseCatalog:
    """加载 Catalog；所有结构错误映射为 CONFIG_INVALID。"""
    raw = _load_mapping(path)

    try:
        document = _CatalogDocument.model_validate(raw)
    except ValidationError as exc:
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            "Catalog 校验失败",
            details={"path": str(path), "issues": _validation_issues(exc)},
        ) from exc

    seen: set[str] = set()
    duplicates: list[str] = []
    for case in document.cases:
        if case.case_id in seen:
            duplicates.append(case.case_id)
        seen.add(case.case_id)
    if duplicates:
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            "Catalog 存在重复 case_id",
            details={"path": str(path), "duplicates": sorted(set(duplicates))},
        )

    problems: list[dict[str, str]] = []
    for case in document.cases:
        for param_name, spec in case.params.items():
            problem = validate_param_spec(spec)
            if problem is not None:
                problems.append({"case_id": case.case_id, "param": param_name, "msg": problem})
    if problems:
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            "Catalog 参数定义不合法",
            details={"path": str(path), "issues": problems},
        )

    cases = {case.case_id: case for case in document.cases}
    return CaseCatalog(
        schema_version=document.schema_version,
        suite_id=document.suite_id,
        cases=MappingProxyType(cases),
    )
