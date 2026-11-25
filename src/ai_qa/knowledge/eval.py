"""RAG 评估指标（实现规格 §10.6）。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.knowledge import RetrievalSnapshot


class RagCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    expected_sources: list[str] = Field(min_length=1)
    expected_terms: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class RagMetrics:
    cases: int
    recall_at_5: float
    mrr: float
    no_result_rate: float
    citation_valid_rate: float
    term_coverage: float
    degraded_rate: float

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, indent=2)


def load_eval_cases(path: Path) -> list[RagCase]:
    if not path.is_file():
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            f"RAG 评估集不存在: {path}",
            details={"path": str(path)},
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            "RAG 评估集读取失败",
            details={"path": str(path), "error": str(exc)[:300]},
        ) from exc
    if not isinstance(raw, list):
        raise AiQaError(ErrorCode.CONFIG_INVALID, "RAG 评估集顶层必须是列表")
    try:
        return [RagCase.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            "RAG 评估集校验失败",
            details={"issues": str(exc)[:500]},
        ) from exc


def evaluate_cases(
    cases: list[RagCase],
    search: Callable[[str], RetrievalSnapshot],
    *,
    top_k: int = 5,
) -> RagMetrics:
    if not cases:
        return RagMetrics(0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    recall_hits = 0
    reciprocal_rank = 0.0
    no_result = 0
    degraded = 0
    valid_items = 0
    total_items = 0
    term_hits = 0

    for case in cases:
        snapshot = search(case.question)
        if not snapshot.items:
            no_result += 1
        if snapshot.degraded:
            degraded += 1
        top_items = snapshot.items[:top_k]

        relevant_rank: int | None = None
        for rank, item in enumerate(top_items, start=1):
            expected = item.source_path in case.expected_sources
            if expected and relevant_rank is None:
                relevant_rank = rank
        if relevant_rank is not None:
            recall_hits += 1
            reciprocal_rank += 1.0 / relevant_rank

        combined_text = "\n".join(item.text for item in top_items)
        if all(term in combined_text for term in case.expected_terms):
            term_hits += 1

        for item in snapshot.items:
            total_items += 1
            if (
                item.ref_id
                and item.chunk_id
                and item.content_hash
                and item.source_path
                and item.text
            ):
                valid_items += 1

    count = len(cases)
    return RagMetrics(
        cases=count,
        recall_at_5=recall_hits / count,
        mrr=reciprocal_rank / count,
        no_result_rate=no_result / count,
        citation_valid_rate=(valid_items / total_items) if total_items else 1.0,
        term_coverage=term_hits / count,
        degraded_rate=degraded / count,
    )
