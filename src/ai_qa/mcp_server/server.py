"""独立 stdio MCP Server（实现规格 §12.1）。

stdout 只允许 MCP 协议消息；日志走 stderr。
"""

from __future__ import annotations

import json
from typing import Any

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from pydantic import ValidationError

from ai_qa import __version__
from ai_qa.bootstrap import AppContext, build_app_context
from ai_qa.domain.errors import AiQaError, ErrorCode, ErrorInfo
from ai_qa.domain.results import ToolFailureResult
from ai_qa.mcp_server.schemas import (
    GetRunResultArgs,
    RunBenchmarkArgs,
    RunCasesArgs,
    get_run_result_schema,
    run_args_schema,
    run_result_schema,
)
from ai_qa.mcp_server.tools import QaMcpTools


def tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="run_http_cases",
            title="Run HTTP cases",
            description="按 run_id / plan_id / plan_hash 执行已校验计划中的 HTTP 用例。",
            inputSchema=run_args_schema(),
            outputSchema=run_result_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=False,
            ),
        ),
        types.Tool(
            name="run_http_benchmark",
            title="Run HTTP benchmark",
            description="Phase 3 仅做授权与计划校验；wrk 执行在 Phase 6 实现。",
            inputSchema=run_args_schema(),
            outputSchema=run_result_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=False,
            ),
        ),
        types.Tool(
            name="get_run_result",
            title="Get run result",
            description="只读返回 Run、CaseResult、PerfResult 和 Artifact 引用。",
            inputSchema=GetRunResultArgs.model_json_schema(),
            outputSchema=get_run_result_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
    ]


def create_server(tools: QaMcpTools) -> Server[Any]:
    async def handle_list_tools(
        _ctx: Any, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tool_definitions())

    async def handle_call_tool(
        _ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        arguments = params.arguments if params.arguments is not None else {}
        structured, is_error = await _dispatch(tools, params.name, arguments)
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps(structured, ensure_ascii=False),
                )
            ],
            structuredContent=structured,
            isError=is_error,
        )

    return Server(
        name="ai-qa",
        version=__version__,
        on_list_tools=handle_list_tools,
        on_call_tool=handle_call_tool,
    )


async def _dispatch(
    tools: QaMcpTools, name: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    try:
        if name == "run_http_cases":
            return await tools.run_http_cases(RunCasesArgs.model_validate(arguments))
        if name == "run_http_benchmark":
            return await tools.run_http_benchmark(RunBenchmarkArgs.model_validate(arguments))
        if name == "get_run_result":
            return await tools.get_run_result(GetRunResultArgs.model_validate(arguments))
        raise AiQaError(ErrorCode.MCP_TOOL_ERROR, f"未知工具: {name}")
    except ValidationError as exc:
        error = ToolFailureResult(
            tool=name,
            error=ErrorInfo(
                code=ErrorCode.MCP_TOOL_ERROR,
                message="参数 Schema 校验失败",
                details={"issues": _schema_issues(exc)},
            ),
        )
        return error.model_dump(mode="json"), True
    except AiQaError as exc:
        failure = ToolFailureResult(tool=name, error=exc.to_error_info())
        return failure.model_dump(mode="json"), True


def _schema_issues(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "loc": ".".join(str(part) for part in error["loc"]),
            "msg": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors(include_url=False)[:10]
    ]


async def main() -> None:
    context: AppContext = build_app_context()
    try:
        server = create_server(QaMcpTools(context))
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        context.close()
