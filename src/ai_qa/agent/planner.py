"""计划生成与修复循环（实现规格 §11.1～§11.4）。"""

from __future__ import annotations

import json
from typing import Any

from ai_qa.agent.prompts import (
    PLANNER_SYSTEM,
    REPAIR_SYSTEM,
    plan_schema_text,
    plan_user_prompt,
    repair_user_prompt,
)
from ai_qa.agent.schemas import PlanAttempt, PlanProposal
from ai_qa.application.ports import ChatPlanner
from ai_qa.domain.enums import PlanMode
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.knowledge import RetrievalSnapshot
from ai_qa.validation.catalog import CaseCatalog
from ai_qa.validation.policy import ValidationPolicy
from ai_qa.validation.repair import PlanRepairBudget
from ai_qa.validation.validator import (
    PlanValidator,
    ValidationContext,
    ValidationIssue,
)


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("计划顶层必须是 JSON 对象")
    return value


class PlanProposer:
    def __init__(
        self,
        chat: ChatPlanner,
        validator: PlanValidator,
        catalog: CaseCatalog,
        policy: ValidationPolicy,
    ) -> None:
        self._chat = chat
        self._validator = validator
        self._catalog = catalog
        self._policy = policy
        self._max_repairs = policy.max_plan_repairs
        self._system = PLANNER_SYSTEM.format(schema=plan_schema_text())

    async def propose(
        self,
        *,
        goal: str,
        target_id: str,
        snapshot: RetrievalSnapshot,
    ) -> PlanProposal:
        context = ValidationContext(
            mode=PlanMode.AGENT_PLAN,
            snapshot_ref_ids=frozenset(item.ref_id for item in snapshot.items),
        )
        raw = await self._chat.complete(
            system=self._system,
            user=plan_user_prompt(
                goal=goal,
                target_id=target_id,
                catalog=self._catalog,
                snapshot=snapshot,
                policy=self._policy,
            ),
        )
        budget = PlanRepairBudget(self._max_repairs)
        attempts: list[PlanAttempt] = []
        issues: list[ValidationIssue] = []

        for attempt in range(self._max_repairs + 1):
            parsed: dict[str, Any] | None = None
            try:
                parsed = _extract_json_object(raw)
            except (ValueError, json.JSONDecodeError) as exc:
                issues = [
                    ValidationIssue(
                        code=ErrorCode.PLAN_JSON_INVALID,
                        path="",
                        message=f"计划不是合法 JSON 对象: {exc}",
                        repairable=True,
                    )
                ]
            else:
                result = self._validator.validate(parsed, context)
                issues = result.issues
                if result.ok and result.plan is not None:
                    attempts.append(PlanAttempt(attempt=attempt, raw_text=raw[:8000]))
                    return PlanProposal(
                        plan=result.plan.model_copy(update={"repair_count": attempt}),
                        attempts=attempts,
                    )

            attempts.append(PlanAttempt(attempt=attempt, raw_text=raw[:8000], issues=issues))

            if issues and not any(issue.repairable for issue in issues):
                raise AiQaError(
                    ErrorCode.PLAN_SCHEMA_INVALID,
                    "计划包含不可修复的问题",
                    details={"issues": [issue.model_dump(mode="json") for issue in issues]},
                )
            if attempt >= self._max_repairs:
                raise AiQaError(
                    ErrorCode.PLAN_REPAIR_EXHAUSTED,
                    f"计划修复次数已用尽（上限 {self._max_repairs}）",
                    details={
                        "attempts": len(attempts),
                        "issues": [issue.model_dump(mode="json") for issue in issues],
                    },
                )

            budget.consume()
            raw = await self._chat.complete(
                system=REPAIR_SYSTEM,
                user=repair_user_prompt(
                    goal=goal,
                    target_id=target_id,
                    raw_plan=raw,
                    issues=issues,
                    catalog=self._catalog,
                    policy=self._policy,
                ),
            )

        raise AiQaError(
            ErrorCode.PLAN_REPAIR_EXHAUSTED,
            "计划修复循环异常终止",
            details={"attempts": len(attempts)},
        )
