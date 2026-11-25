"""Markdown 报告与 results.json（实现规格 §14.3 / §16）。"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ai_qa.bootstrap import AppContext
from ai_qa.domain.enums import ExecutionStatus, TestStatus, aggregate_test_status
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.results import ArtifactRef, CaseResult, PerfResult

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


@dataclass(frozen=True)
class ReportArtifacts:
    report: ArtifactRef
    results: ArtifactRef
    payload: dict[str, Any]


class ReportService:
    def __init__(self, context: AppContext) -> None:
        self._context = context
        self._environment = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=StrictUndefined,
        )

    def generate(self, run_id: str) -> ReportArtifacts:
        context = self._context
        run = context.run_repository.get_run(run_id)
        plan = context.plan_repository.get_plan(run.plan_id)
        case_results = context.result_repository.list_case_results(run_id)
        perf = context.perf_repository.get_perf_result(run_id)
        artifacts = context.artifact_repository.list_artifacts(run_id)
        summary = self._read_summary(artifacts)
        trace_events = self._read_trace(run_id)
        trace_counts = dict(Counter(str(event.get("event", "unknown")) for event in trace_events))
        overall_verdict = _overall_verdict(run.test_status, case_results, perf)
        overall_execution = _overall_execution(run.execution_status, case_results)

        try:
            template = self._environment.get_template("report.md.j2")
            markdown = template.render(
                run=run,
                plan=plan,
                case_results=case_results,
                perf=perf,
                artifacts=artifacts,
                summary=summary,
                trace_counts=trace_counts,
                overall_verdict=overall_verdict,
                overall_execution=overall_execution,
                generated_at=run.updated_at.isoformat(),
            )
            payload: dict[str, Any] = {
                "run": run.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "case_results": [result.model_dump(mode="json") for result in case_results],
                "perf": perf.model_dump(mode="json") if perf is not None else None,
                "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
                "summary": summary,
                "overall_verdict": overall_verdict.value,
                "overall_execution": overall_execution.value,
                "trace_events": trace_events,
            }
            report = context.artifact_store.write_text(
                run_id,
                "report.md",
                markdown,
                "report",
                mime="text/markdown; charset=utf-8",
            )
            results = context.artifact_store.write_text(
                run_id,
                "results.json",
                json.dumps(payload, ensure_ascii=False, indent=2),
                "results_json",
                mime="application/json; charset=utf-8",
            )
        except Exception as exc:
            raise AiQaError(
                ErrorCode.REPORT_GENERATION_FAILED,
                f"报告生成失败: {type(exc).__name__}: {exc}"[:300],
                details={"run_id": run_id},
            ) from exc

        context.artifact_repository.save_artifact(report)
        context.artifact_repository.save_artifact(results)
        return ReportArtifacts(report=report, results=results, payload=payload)

    def _read_summary(self, artifacts: list[ArtifactRef]) -> str | None:
        for artifact in artifacts:
            if artifact.name == "summary.md":
                path = self._context.paths.artifacts / artifact.uri
                try:
                    return path.read_text(encoding="utf-8")
                except OSError:
                    return None
        return None

    def _read_trace(self, run_id: str) -> list[dict[str, Any]]:
        path = self._context.paths.artifacts / run_id / "trace.jsonl"
        if not path.is_file():
            return []
        events: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                value = json.loads(line)
                if isinstance(value, dict):
                    events.append(value)
        except (OSError, json.JSONDecodeError):
            return events
        return events


def _overall_verdict(
    run_status: TestStatus | None,
    case_results: list[CaseResult],
    perf: PerfResult | None,
) -> TestStatus:
    if run_status is not None:
        return run_status
    if case_results:
        return aggregate_test_status(result.test_status for result in case_results)
    if perf is not None:
        return perf.test_status
    return TestStatus.INCONCLUSIVE


def _overall_execution(
    run_status: ExecutionStatus | None, case_results: list[CaseResult]
) -> ExecutionStatus:
    if run_status is not None:
        return run_status
    statuses = [result.execution_status for result in case_results]
    if not statuses:
        return ExecutionStatus.SUCCEEDED
    if any(status is ExecutionStatus.TIMED_OUT for status in statuses):
        return ExecutionStatus.TIMED_OUT
    if any(status is ExecutionStatus.CANCELLED for status in statuses):
        return ExecutionStatus.CANCELLED
    if any(status is ExecutionStatus.FAILED for status in statuses):
        return ExecutionStatus.FAILED
    return ExecutionStatus.SUCCEEDED
