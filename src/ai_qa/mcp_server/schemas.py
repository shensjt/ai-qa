"""MCP 工具输入模型与 JSON Schema（实现规格 §12.2～§12.5）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_qa.domain.results import RunResult, ToolResult


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunCasesArgs(_StrictModel):
    run_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class RunBenchmarkArgs(RunCasesArgs):
    """与 run_http_cases 相同的受限入参；服务端从已校验计划读取压测参数。"""


class GetRunResultArgs(_StrictModel):
    run_id: str = Field(min_length=1)


def run_args_schema() -> dict[str, Any]:
    return RunCasesArgs.model_json_schema()


def run_result_schema() -> dict[str, Any]:
    return ToolResult.model_json_schema()


def get_run_result_schema() -> dict[str, Any]:
    return RunResult.model_json_schema()
