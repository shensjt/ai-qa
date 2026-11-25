from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from ai_qa.agent.planner import PlanProposer
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.knowledge import RetrievalItem, RetrievalQuery, RetrievalSnapshot
from ai_qa.domain.models import utc_now
from ai_qa.validation.catalog import load_catalog
from ai_qa.validation.policy import ValidationPolicy
from ai_qa.validation.validator import PlanValidator

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET = "cpp-webserver-local"


class ScriptedChat:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[tuple[str, str]] = []

    async def complete(self, *, system: str, user: str) -> str:
        self.prompts.append((system, user))
        if not self.responses:
            raise AssertionError("没有更多脚本响应")
        return self.responses.pop(0)


def _policy(**overrides: Any) -> ValidationPolicy:
    base: dict[str, Any] = {
        "expected_target_id": TARGET,
        "max_cases": 6,
        "run_timeout_seconds": 120,
        "benchmark_allowed": False,
        "benchmark_max_duration_seconds": 30,
        "benchmark_max_connections": 64,
        "benchmark_max_threads": 4,
        "max_plan_repairs": 2,
    }
    base.update(overrides)
    return ValidationPolicy(**base)


def _snapshot(*refs: str) -> RetrievalSnapshot:
    items = [
        RetrievalItem(
            ref_id=ref,
            chunk_id=f"chunk_{ref}",
            doc_id="doc_1",
            source_path="knowledge/project/runtime-contract.md",
            section="健康检查",
            chunk_index=index,
            content_hash=f"hash_{ref}",
            priority="P0",
            source_type="project_doc",
            score=0.9,
            text="健康检查使用 /healthz，返回 status 和 commit。",
        )
        for index, ref in enumerate(refs)
    ]
    return RetrievalSnapshot(
        query=RetrievalQuery(query="健康检查", project_id="cpp-webserver"),
        items=items,
        created_at=utc_now(),
    )


def _plan(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": "1.0",
        "goal": "smoke",
        "target_id": TARGET,
        "items": [{"case_id": "http.get_index", "params": {}}],
        "benchmark": None,
        "limits": {"max_cases": 6, "run_timeout_seconds": 120},
    }
    data.update(overrides)
    return data


def _proposer(chat: ScriptedChat, **policy_overrides: Any) -> PlanProposer:
    policy = _policy(**policy_overrides)
    catalog = load_catalog(REPO_ROOT / "config" / "cases" / "http.yaml")
    return PlanProposer(chat, PlanValidator(catalog, policy), catalog, policy)


def test_valid_plan_accepted_without_repair() -> None:
    chat = ScriptedChat([json.dumps(_plan())])

    proposal = asyncio.run(
        _proposer(chat).propose(goal="smoke", target_id=TARGET, snapshot=_snapshot())
    )

    assert proposal.plan.repair_count == 0
    assert len(proposal.attempts) == 1
    assert proposal.attempts[0].issues == []
    assert proposal.plan.plan.target_id == TARGET


def test_fenced_json_is_extracted() -> None:
    chat = ScriptedChat(["```json\n" + json.dumps(_plan()) + "\n```"])

    proposal = asyncio.run(
        _proposer(chat).propose(goal="smoke", target_id=TARGET, snapshot=_snapshot())
    )

    assert proposal.plan.repair_count == 0


def test_invalid_json_is_repaired() -> None:
    chat = ScriptedChat(["{not json", json.dumps(_plan())])

    proposal = asyncio.run(
        _proposer(chat).propose(goal="smoke", target_id=TARGET, snapshot=_snapshot())
    )

    assert proposal.plan.repair_count == 1
    assert len(proposal.attempts) == 2
    assert proposal.attempts[0].issues[0].code is ErrorCode.PLAN_JSON_INVALID


def test_unknown_case_is_repaired() -> None:
    bad = _plan(items=[{"case_id": "http.made_up", "params": {}}])
    chat = ScriptedChat([json.dumps(bad), json.dumps(_plan())])

    proposal = asyncio.run(
        _proposer(chat).propose(goal="smoke", target_id=TARGET, snapshot=_snapshot())
    )

    assert proposal.plan.repair_count == 1
    assert proposal.attempts[0].issues[0].code is ErrorCode.UNKNOWN_CASE
    assert "http.made_up" in chat.prompts[1][1]


def test_bad_reference_is_repaired() -> None:
    bad = _plan(items=[{"case_id": "http.get_index", "params": {}, "refs": ["ref_999"]}])
    good = _plan(items=[{"case_id": "http.get_index", "params": {}, "refs": ["ref_1"]}])
    chat = ScriptedChat([json.dumps(bad), json.dumps(good)])

    proposal = asyncio.run(
        _proposer(chat).propose(goal="smoke", target_id=TARGET, snapshot=_snapshot("ref_1"))
    )

    assert proposal.plan.repair_count == 1
    assert proposal.attempts[0].issues[0].code is ErrorCode.REFERENCE_INVALID


def test_benchmark_removed_by_repair() -> None:
    bad = _plan(
        benchmark={
            "enabled": True,
            "tool": "wrk",
            "path": "/",
            "duration_seconds": 10,
            "connections": 10,
            "threads": 2,
        }
    )
    chat = ScriptedChat([json.dumps(bad), json.dumps(_plan())])

    proposal = asyncio.run(
        _proposer(chat).propose(goal="smoke", target_id=TARGET, snapshot=_snapshot())
    )

    assert any(issue.code is ErrorCode.POLICY_VIOLATION for issue in proposal.attempts[0].issues)
    assert proposal.plan.benchmark is None


def test_repair_exhaustion() -> None:
    chat = ScriptedChat(["{bad"] * 3)

    with pytest.raises(AiQaError) as excinfo:
        asyncio.run(_proposer(chat).propose(goal="smoke", target_id=TARGET, snapshot=_snapshot()))

    assert excinfo.value.code is ErrorCode.PLAN_REPAIR_EXHAUSTED
    assert excinfo.value.details["attempts"] == 3
    assert len(chat.prompts) == 3
