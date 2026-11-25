"""RuleEvaluator：把 CaseFacts 转成 CaseResult（唯一 verdict 来源）。"""

from __future__ import annotations

from ai_qa.domain.enums import ExecutionStatus, TestStatus, aggregate_test_status
from ai_qa.domain.errors import ErrorCode, ErrorInfo
from ai_qa.domain.plans import ResolvedPlanItem
from ai_qa.domain.results import CaseFacts, CaseResult
from ai_qa.rules.http_rules import evaluate_rule_set


class RuleEvaluatorImpl:
    def evaluate_case(self, item: ResolvedPlanItem, facts: CaseFacts) -> CaseResult:
        if facts.executor_error is not None:
            execution_status = (
                ExecutionStatus.TIMED_OUT
                if facts.executor_error.code in {ErrorCode.CASE_TIMEOUT, ErrorCode.TOOL_TIMEOUT}
                else ExecutionStatus.FAILED
            )
            return CaseResult(
                case_id=item.case_id,
                execution_status=execution_status,
                test_status=TestStatus.ERROR,
                facts=facts,
                assertions=[],
                error=facts.executor_error,
            )

        assertions = evaluate_rule_set(item.rule_set, item, facts)
        if assertions is None:
            return CaseResult(
                case_id=item.case_id,
                execution_status=ExecutionStatus.SUCCEEDED,
                test_status=TestStatus.ERROR,
                facts=facts,
                assertions=[],
                error=ErrorInfo(
                    code=ErrorCode.RULE_ENGINE_ERROR,
                    message=f"未知 rule_set: {item.rule_set}",
                ),
            )

        for assertion in assertions:
            if assertion.status is TestStatus.FAIL and not assertion.evidence_refs:
                return CaseResult(
                    case_id=item.case_id,
                    execution_status=ExecutionStatus.SUCCEEDED,
                    test_status=TestStatus.ERROR,
                    facts=facts,
                    assertions=assertions,
                    error=ErrorInfo(
                        code=ErrorCode.RULE_ENGINE_ERROR,
                        message=f"FAIL 缺少 evidence_refs: {assertion.rule_id}",
                    ),
                )

        test_status = aggregate_test_status(assertion.status for assertion in assertions)
        return CaseResult(
            case_id=item.case_id,
            execution_status=ExecutionStatus.SUCCEEDED,
            test_status=test_status,
            facts=facts,
            assertions=assertions,
            error=None,
        )
