"""Typer CLI 入口（实现规格 §16）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from ai_qa import __version__
from ai_qa.doctor import CheckStatus, DoctorReport, DoctorService
from ai_qa.domain.errors import AiQaError, exit_code_for
from ai_qa.log import configure_logging
from ai_qa.settings import load_settings, resolve_config_path

app = typer.Typer(
    help="AI QA Agent：受约束的 AI 测试 Agent（MVP）。",
    no_args_is_help=True,
    invoke_without_command=True,
    add_completion=False,
)

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
