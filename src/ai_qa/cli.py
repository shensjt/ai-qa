"""Typer CLI 入口（实现规格 §16）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from ai_qa import __version__
from ai_qa.agent.schemas import AgentRunOutcome
from ai_qa.application.agent_runner import AgentRunner
from ai_qa.application.fixed_plan_runner import FixedPlanExecution, FixedPlanRunner
from ai_qa.bootstrap import build_app_context, build_knowledge_service
from ai_qa.doctor import CheckStatus, DoctorReport, DoctorService
from ai_qa.domain.enums import TestStatus
from ai_qa.domain.errors import AiQaError, ErrorCode, exit_code_for
from ai_qa.domain.knowledge import RetrievalSnapshot
from ai_qa.log import configure_logging
from ai_qa.settings import load_local_env, load_settings, resolve_config_path
from ai_qa.validation.validator import PlanValidationError

app = typer.Typer(
    help="AI QA Agent：受约束的 AI 测试 Agent（MVP）。",
    no_args_is_help=True,
    invoke_without_command=True,
    add_completion=False,
)
knowledge_app = typer.Typer(help="知识库构建与检索。", no_args_is_help=True)
app.add_typer(knowledge_app, name="knowledge")

_STATUS_LABEL = {
    CheckStatus.OK: "[OK]  ",
    CheckStatus.WARN: "[WARN]",
    CheckStatus.ERROR: "[ERR] ",
}


@app.callback()
def main(
    version: Annotated[bool, typer.Option("--version", help="显示版本并退出")] = False,
    log_level: Annotated[
        str, typer.Option("--log-level", help="日志级别，如 INFO / DEBUG")
    ] = "INFO",
) -> None:
    """配置日志并处理全局选项。"""
    load_local_env()
    configure_logging(log_level)
    if version:
        typer.echo(__version__)
        raise typer.Exit(code=0)


@app.command()
def doctor(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="配置文件路径；默认取 AI_QA_CONFIG 或 config/settings.yaml"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="以 JSON 输出诊断结果")] = False,
) -> None:
    """检查配置、路径与外部依赖；不启动被测服务。"""
    try:
        loaded = load_settings(resolve_config_path(config))
    except AiQaError as exc:
        _emit_fatal(exc, as_json=as_json)

    report = DoctorService(loaded.settings, loaded.config_path).run()
    if as_json:
        typer.echo(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        _render_report(report, loaded.config_path)

    if report.has_errors:
        raise typer.Exit(code=2)


@app.command()
def run(
    plan: Annotated[Path, typer.Option("--plan", help="固定计划 JSON 路径")],
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            help="配置文件路径；默认取 AI_QA_CONFIG 或 config/settings.yaml",
        ),
    ] = None,
    target: Annotated[str | None, typer.Option("--target", help="目标 ID；必须与配置一致")] = None,
    allow_benchmark: Annotated[
        bool, typer.Option("--allow-benchmark", help="授权本机短压测")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="以 JSON 输出")] = False,
) -> None:
    """用固定计划经真实 MCP Server 执行；不需要 LLM / API Key。"""
    try:
        context = build_app_context(config)
    except AiQaError as exc:
        _emit_fatal(exc, as_json=as_json)

    try:
        execution = asyncio.run(
            FixedPlanRunner(context, allow_benchmark=allow_benchmark).execute(
                plan, target_id=target
            )
        )
    except PlanValidationError as exc:
        _emit_validation_error(exc, as_json=as_json)
    except AiQaError as exc:
        _emit_fatal(exc, as_json=as_json)
    finally:
        context.close()

    if as_json:
        typer.echo(
            json.dumps(
                execution.tool_result.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _render_execution(execution)
    raise typer.Exit(code=_verdict_exit_code(execution.tool_result.test_status))


def _emit_fatal(exc: AiQaError, *, as_json: bool) -> NoReturn:
    if as_json:
        payload = exc.to_error_info().model_dump(mode="json")
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"错误 [{exc.code.value}]: {exc.message}", err=True)
        for key, value in exc.details.items():
            typer.echo(f"  {key}: {value}", err=True)
    raise typer.Exit(code=exit_code_for(exc.code))


def _render_report(report: DoctorReport, config_path: Path) -> None:
    typer.echo(f"配置文件: {config_path}")
    for check in report.checks:
        typer.echo(f"{_STATUS_LABEL[check.status]} {check.check:<26} {check.message}")
    verdict = "通过" if not report.has_errors else "未通过"
    typer.echo(f"\n结果: {verdict}（错误 {report.error_count}，警告 {report.warning_count}）")


def _emit_validation_error(exc: PlanValidationError, *, as_json: bool) -> NoReturn:
    if as_json:
        payload = {
            "code": exc.code.value,
            "message": exc.message,
            "issues": [issue.model_dump(mode="json") for issue in exc.issues],
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"计划校验失败（{len(exc.issues)} 项）:", err=True)
        for issue in exc.issues:
            typer.echo(f"  [{issue.code.value}] {issue.path}: {issue.message}", err=True)
    raise typer.Exit(code=exit_code_for(exc.code))


def _render_execution(execution: FixedPlanExecution) -> None:
    typer.echo(f"run_id: {execution.run.run_id}")
    typer.echo(f"plan_id: {execution.run.plan_id}")
    typer.echo(f"trace: {execution.trace_path}")
    typer.echo("")
    for case in execution.tool_result.case_results:
        typer.echo(f"{case.case_id:<24} {case.execution_status.value:<10} {case.test_status.value}")
    typer.echo(f"\nverdict: {execution.tool_result.test_status.value}")
    typer.echo(f"artifacts: {len(execution.run_result.artifacts)}")
    typer.echo(f"case_results: {len(execution.run_result.case_results)}")


def _verdict_exit_code(status: TestStatus) -> int:
    if status is TestStatus.ERROR:
        return 5
    if status is TestStatus.FAIL:
        return 4
    return 0


@knowledge_app.command("build")
def knowledge_build(
    project: Annotated[str, typer.Option("--project", help="项目 ID")],
    manifest: Annotated[Path | None, typer.Option("--manifest", help="manifest 路径")] = None,
    rebuild: Annotated[bool, typer.Option("--rebuild", help="清空并重建该项目知识")] = False,
    config: Annotated[Path | None, typer.Option("--config", help="配置文件路径")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="以 JSON 输出")] = False,
) -> None:
    """按 manifest 构建/增量更新知识库。"""
    context = None
    try:
        context = build_app_context(config)
        if project != context.settings.project.project_id:
            raise AiQaError(
                ErrorCode.CONFIG_INVALID,
                f"--project {project} 与配置中的 project_id 不一致",
                details={"configured": context.settings.project.project_id},
            )
        report = build_knowledge_service(context).build(
            manifest or context.paths.manifest, rebuild=rebuild
        )
    except AiQaError as exc:
        _emit_fatal(exc, as_json=as_json)
    finally:
        if context is not None:
            context.close()

    if as_json:
        typer.echo(report.model_dump_json(indent=2))
        return
    typer.echo(f"project: {report.project_id}")
    typer.echo(
        f"documents: total={report.documents_total} indexed={report.documents_indexed} "
        f"skipped={report.documents_skipped} deleted={report.documents_deleted}"
    )
    typer.echo(f"chunks written: {report.chunks_written}")
    typer.echo(f"collection: {report.collection or '(BM25 only)'}")
    if report.degraded:
        typer.echo(f"[WARN] 降级: {report.degradation_reason}")
    if report.smoke_hits:
        typer.echo("smoke hits:")
        for hit in report.smoke_hits:
            typer.echo(f"  - {hit}")


@knowledge_app.command("search")
def knowledge_search(
    query: Annotated[str, typer.Argument(help="查询文本")],
    top_k: Annotated[int, typer.Option("--top-k", min=1, max=50)] = 8,
    source_type: Annotated[
        list[str] | None, typer.Option("--source-type", help="可重复，按来源类型过滤")
    ] = None,
    config: Annotated[Path | None, typer.Option("--config", help="配置文件路径")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="以 JSON 输出")] = False,
) -> None:
    """检索知识库并展示引用来源。"""
    context = None
    try:
        context = build_app_context(config)
        snapshot = build_knowledge_service(context).search(
            query,
            top_k=top_k,
            source_types=source_type,
        )
    except AiQaError as exc:
        _emit_fatal(exc, as_json=as_json)
    finally:
        if context is not None:
            context.close()

    if as_json:
        typer.echo(json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    _render_snapshot(snapshot)


def _render_snapshot(snapshot: RetrievalSnapshot) -> None:
    if snapshot.degraded:
        typer.echo(f"[WARN] BM25 降级: {snapshot.degradation_reason}")
    if not snapshot.items:
        typer.echo("无结果")
        return
    for item in snapshot.items:
        typer.echo(
            f"[{item.ref_id}] score={item.score:.4f} {item.priority} "
            f"{item.source_path} :: {item.section}"
        )
        preview = " ".join(item.text.split())[:160]
        typer.echo(f"    {preview}")


@app.command()
def demo(
    goal: Annotated[str, typer.Option("--goal", help="测试目标，例如：验证连接复用和路径安全")],
    config: Annotated[Path | None, typer.Option("--config", help="配置文件路径")] = None,
    target: Annotated[str | None, typer.Option("--target", help="目标 ID")] = None,
    top_k: Annotated[int | None, typer.Option("--top-k", help="RAG 检索条数")] = None,
    allow_benchmark: Annotated[
        bool, typer.Option("--allow-benchmark", help="授权本机短压测")
    ] = False,
    fallback_plan: Annotated[
        Path | None,
        typer.Option("--fallback-plan", help="Agent 失败时使用的固定计划"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="以 JSON 输出")] = False,
) -> None:
    """RAG + DeepSeek 规划 + 真实 MCP 执行 + 总结草稿。"""
    context = None
    outcome: AgentRunOutcome | None = None
    fallback: FixedPlanExecution | None = None
    try:
        context = build_app_context(config)
        try:
            outcome = asyncio.run(
                AgentRunner(
                    context,
                    allow_benchmark=allow_benchmark,
                    top_k=top_k,
                ).execute(goal, target_id=target)
            )
        except AiQaError as exc:
            if fallback_plan is None:
                raise
            typer.echo(
                f"[WARN] Agent 失败（{exc.code.value}），降级固定计划: {exc.message}",
                err=True,
            )
            fallback = asyncio.run(
                FixedPlanRunner(context, allow_benchmark=allow_benchmark).execute(
                    fallback_plan, target_id=target
                )
            )
    except AiQaError as exc:
        _emit_fatal(exc, as_json=as_json)
    finally:
        if context is not None:
            context.close()

    if fallback is not None:
        if as_json:
            typer.echo(
                json.dumps(
                    fallback.tool_result.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            _render_execution(fallback)
        raise typer.Exit(code=_verdict_exit_code(fallback.tool_result.test_status))

    assert outcome is not None
    if as_json:
        payload = {
            "run_id": outcome.run.run_id,
            "plan_id": outcome.proposal.plan.plan_id,
            "repairs": outcome.proposal.plan.repair_count,
            "retrieval_degraded": outcome.retrieval_degraded,
            "trace": str(outcome.trace_path),
            "tool_result": outcome.execution.tool_result.model_dump(mode="json"),
            "summary": outcome.execution.summary,
            "summary_artifact": outcome.execution.summary_artifact.model_dump(mode="json"),
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _render_agent_outcome(outcome)
    raise typer.Exit(code=_verdict_exit_code(outcome.execution.tool_result.test_status))


def _render_agent_outcome(outcome: AgentRunOutcome) -> None:
    plan = outcome.proposal.plan
    typer.echo(f"run_id: {outcome.run.run_id}")
    typer.echo(f"plan_id: {plan.plan_id}")
    typer.echo(f"repairs: {plan.repair_count}")
    typer.echo(f"trace: {outcome.trace_path}")
    if outcome.retrieval_degraded:
        typer.echo("[WARN] RAG 检索已降级为 BM25")
    typer.echo("")
    for case in outcome.execution.tool_result.case_results:
        typer.echo(f"{case.case_id:<24} {case.execution_status.value:<10} {case.test_status.value}")
    typer.echo(f"\nverdict: {outcome.execution.tool_result.test_status.value}")
    typer.echo(f"artifacts: {len(outcome.execution.run_result.artifacts)}")
    typer.echo(f"summary artifact: {outcome.execution.summary_artifact.uri}")
    typer.echo("\n--- summary draft ---")
    typer.echo(outcome.execution.summary)
