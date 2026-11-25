"""四层计划校验：Schema → Catalog → Policy → Reference + plan_hash（规格 §11.3）。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai_qa.domain.enums import PlanMode, PlanStatus
from ai_qa.domain.errors import ErrorCode
from ai_qa.domain.models import new_id, utc_now
from ai_qa.domain.plans import (
    ResolvedPlanItem,
    RunLimits,
    StoredPlan,
    TestPlan,
    compute_plan_hash,
)
from ai_qa.validation.catalog import CaseCatalog, CaseSpec
from ai_qa.validation.params import validate_http_path, validate_param_value
from ai_qa.validation.policy import ValidationPolicy


class ValidationContext(BaseModel):
    """校验上下文：Agent 模式必须携带当前 RetrievalSnapshot 的 ref 集合。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: PlanMode
    snapshot_ref_ids: frozenset[str] | None = None

    @model_validator(mode="after")
    def _require_snapshot_for_agent(self) -> Self:
        if self.mode is PlanMode.AGENT_PLAN and self.snapshot_ref_ids is None:
            raise ValueError("AGENT_PLAN 模式必须提供 RetrievalSnapshot 的 ref_id 集合")
        return self


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    path: str = ""
    message: str
    allowed: list[str] | None = None
    repairable: bool = False


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: StoredPlan | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.plan is not None and not self.issues


def _format_loc(loc: tuple[Any, ...]) -> str:
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += ("." if path else "") + str(part)
    return path


def _issue(
    code: ErrorCode,
    path: str,
    message: str,
    *,
    allowed: list[str] | None = None,
    repairable: bool = True,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        path=path,
        message=message,
        allowed=allowed,
        repairable=repairable,
    )


class PlanValidator:
    def __init__(self, catalog: CaseCatalog, policy: ValidationPolicy) -> None:
        self._catalog = catalog
        self._policy = policy

    def validate(
        self,
        raw: str | bytes | Mapping[str, Any],
        context: ValidationContext,
    ) -> ValidationResult:
        plan, parse_issues = self._parse(raw)
        if plan is None:
            return ValidationResult(issues=parse_issues)

        issues: list[ValidationIssue] = []
        issues.extend(self._check_target(plan))
        issues.extend(self._check_items(plan, context))
        issues.extend(self._check_limits(plan))
        issues.extend(self._check_benchmark(plan))
        if issues:
            return ValidationResult(issues=issues)

        return ValidationResult(plan=self._build_stored_plan(plan, context))

    def _parse(
        self, raw: str | bytes | Mapping[str, Any]
    ) -> tuple[TestPlan | None, list[ValidationIssue]]:
        payload: Any = raw
        if isinstance(raw, bytes):
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return None, [_issue(ErrorCode.PLAN_JSON_INVALID, "", f"计划 JSON 无法解析: {exc}")]
        elif isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                return None, [_issue(ErrorCode.PLAN_JSON_INVALID, "", f"计划 JSON 无法解析: {exc}")]

        if not isinstance(payload, Mapping):
            return None, [_issue(ErrorCode.PLAN_JSON_INVALID, "", "计划顶层必须是 JSON 对象")]

        try:
            return TestPlan.model_validate(payload), []
        except ValidationError as exc:
            issues = [
                _issue(
                    ErrorCode.PLAN_SCHEMA_INVALID,
                    _format_loc(error["loc"]),
                    error["msg"],
                )
                for error in exc.errors(include_url=False)
            ]
            return None, issues

    def _check_target(self, plan: TestPlan) -> list[ValidationIssue]:
        if plan.target_id != self._policy.expected_target_id:
            return [
                _issue(
                    ErrorCode.TARGET_NOT_FOUND,
                    "target_id",
                    f"未知 target_id: {plan.target_id}",
                    allowed=[self._policy.expected_target_id],
                )
            ]
        return []

    def _check_items(self, plan: TestPlan, context: ValidationContext) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        seen: set[str] = set()

        for index, item in enumerate(plan.items):
            case = self._catalog.get(item.case_id)
            if case is None or not case.enabled:
                issues.append(
                    _issue(
                        ErrorCode.UNKNOWN_CASE,
                        f"items[{index}].case_id",
                        f"Catalog 中不存在或未启用的 case_id: {item.case_id}",
                        allowed=sorted(self._catalog.case_ids()),
                    )
                )
                continue

            if item.case_id in seen:
                issues.append(
                    _issue(
                        ErrorCode.DUPLICATE_CASE,
                        f"items[{index}].case_id",
                        f"case 重复: {item.case_id}；MVP 不允许重复执行",
                    )
                )
            seen.add(item.case_id)

            issues.extend(self._check_params(index, item.params, case))
            for ref_index, ref in enumerate(item.refs):
                issues.extend(self._check_ref(ref, f"items[{index}].refs[{ref_index}]", context))

        return issues

    def _check_params(
        self, index: int, params: Mapping[str, Any], case: CaseSpec
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        allowed = sorted(case.params)

        for name in params:
            if name not in case.params:
                issues.append(
                    _issue(
                        ErrorCode.PARAM_NOT_ALLOWED,
                        f"items[{index}].params.{name}",
                        f"该用例不允许参数 {name}",
                        allowed=allowed,
                    )
                )

        for name, spec in case.params.items():
            if name not in params:
                continue
            problem = validate_param_value(spec, params[name])
            if problem is not None:
                issues.append(
                    _issue(
                        ErrorCode.PARAM_INVALID,
                        f"items[{index}].params.{name}",
                        problem,
                    )
                )

        return issues

    def _check_ref(self, ref: str, path: str, context: ValidationContext) -> list[ValidationIssue]:
        if not ref.strip():
            return [_issue(ErrorCode.REFERENCE_INVALID, path, "ref_id 不能为空")]
        if context.snapshot_ref_ids is None:
            return [
                _issue(
                    ErrorCode.REFERENCE_INVALID,
                    path,
                    "固定计划模式缺少 RetrievalSnapshot，无法验证非空 refs",
                )
            ]
        if ref not in context.snapshot_ref_ids:
            return [
                _issue(
                    ErrorCode.REFERENCE_INVALID,
                    path,
                    f"ref_id 不属于当前 RetrievalSnapshot: {ref}",
                )
            ]
        return []

    def _effective_limits(self, plan: TestPlan) -> RunLimits:
        return RunLimits(
            max_cases=min(plan.limits.max_cases, self._policy.max_cases),
            run_timeout_seconds=min(
                plan.limits.run_timeout_seconds, self._policy.run_timeout_seconds
            ),
        )

    def _check_limits(self, plan: TestPlan) -> list[ValidationIssue]:
        limits = self._effective_limits(plan)
        if len(plan.items) > limits.max_cases:
            return [
                _issue(
                    ErrorCode.POLICY_VIOLATION,
                    "items",
                    f"计划包含 {len(plan.items)} 个用例，超过允许的 {limits.max_cases}",
                )
            ]
        return []

    def _check_benchmark(self, plan: TestPlan) -> list[ValidationIssue]:
        benchmark = plan.benchmark
        if benchmark is None or not benchmark.enabled:
            return []

        issues: list[ValidationIssue] = []
        if not self._policy.benchmark_allowed:
            issues.append(
                _issue(
                    ErrorCode.POLICY_VIOLATION,
                    "benchmark",
                    "压测未获授权；需要 --allow-benchmark 或配置开启",
                )
            )
        if benchmark.duration_seconds > self._policy.benchmark_max_duration_seconds:
            issues.append(
                _issue(
                    ErrorCode.POLICY_VIOLATION,
                    "benchmark.duration_seconds",
                    f"压测时长不能超过 {self._policy.benchmark_max_duration_seconds} 秒",
                )
            )
        if benchmark.connections > self._policy.benchmark_max_connections:
            issues.append(
                _issue(
                    ErrorCode.POLICY_VIOLATION,
                    "benchmark.connections",
                    f"连接数不能超过 {self._policy.benchmark_max_connections}",
                )
            )
        if benchmark.threads > self._policy.benchmark_max_threads:
            issues.append(
                _issue(
                    ErrorCode.POLICY_VIOLATION,
                    "benchmark.threads",
                    f"线程数不能超过 {self._policy.benchmark_max_threads}",
                )
            )

        problem = validate_http_path(benchmark.path)
        if problem is not None:
            issues.append(_issue(ErrorCode.PARAM_INVALID, "benchmark.path", problem))

        return issues

    def _merge_params(self, case: CaseSpec, params: Mapping[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {name: spec.default for name, spec in case.params.items()}
        for name, value in params.items():
            if name in case.params:
                merged[name] = value
        return merged

    def _build_stored_plan(self, plan: TestPlan, context: ValidationContext) -> StoredPlan:
        resolved: list[ResolvedPlanItem] = []
        for sequence_no, item in enumerate(plan.items, start=1):
            case = self._catalog.get(item.case_id)
            if case is None:  # 校验通过后不可能发生
                raise AssertionError(f"catalog 缺少 case: {item.case_id}")
            resolved.append(
                ResolvedPlanItem(
                    case_id=item.case_id,
                    sequence_no=sequence_no,
                    executor=case.executor,
                    rule_set=case.rule_set,
                    timeout_seconds=case.timeout_seconds,
                    artifact_policy=case.artifact_policy,
                    description=case.description,
                    params=self._merge_params(case, item.params),
                    refs=list(item.refs),
                )
            )

        benchmark = plan.benchmark
        return StoredPlan(
            plan_id=new_id("plan"),
            plan_hash=compute_plan_hash(plan),
            status=PlanStatus.VALIDATED,
            mode=context.mode,
            plan=plan,
            items=resolved,
            limits=self._effective_limits(plan),
            benchmark=benchmark if benchmark is not None and benchmark.enabled else None,
            repair_count=0,
            created_at=utc_now(),
        )
