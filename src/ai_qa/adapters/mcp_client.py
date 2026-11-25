"""外部协议适配器：官方 MCP SDK stdio 客户端。"""

from __future__ import annotations

import time
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError

from ai_qa.adapters.trace import JsonlTrace
from ai_qa.application.ports import McpCallResult, ToolCall, ToolDescriptor
from ai_qa.domain.errors import AiQaError, ErrorCode


@dataclass(frozen=True)
class McpServerLaunchConfig:
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: Path | None = None


class McpStdioGateway:
    """启动独立 Server 子进程，记录 initialize/list/call 的真实协议事件。"""

    def __init__(self, launch: McpServerLaunchConfig, trace_path: Path) -> None:
        self._launch = launch
        self._trace = JsonlTrace(trace_path)
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def __aenter__(self) -> Self:
        parameters = StdioServerParameters(
            command=self._launch.command,
            args=list(self._launch.args),
            env=dict(self._launch.env),
            cwd=self._launch.cwd,
        )
        read_stream, write_stream = await self._stack.enter_async_context(stdio_client(parameters))
        self._session = await self._stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        info = await self._session.initialize()
        self._trace.event(
            "mcp_initialized",
            server_name=info.server_info.name,
            server_version=info.server_info.version,
            protocol_version=info.protocol_version,
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._stack.aclose()
        self._session = None

    async def list_tools(self) -> list[ToolDescriptor]:
        session = self._require_session()
        result = await session.list_tools()
        tools = [
            ToolDescriptor(
                name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.input_schema),
                output_schema=(
                    dict(tool.output_schema) if tool.output_schema is not None else None
                ),
            )
            for tool in result.tools
        ]
        self._trace.event("mcp_tools_listed", count=len(tools), tools=[tool.name for tool in tools])
        return tools

    async def call_tool(
        self, call: ToolCall, *, timeout_seconds: float | None = None
    ) -> McpCallResult:
        session = self._require_session()
        started = time.monotonic()
        self._trace.event("tool_call_requested", tool=call.name, arguments=call.arguments)
        try:
            result = await session.call_tool(
                call.name,
                dict(call.arguments),
                read_timeout_seconds=timeout_seconds,
            )
        except MCPError as exc:
            self._trace.event("tool_call_failed", tool=call.name, error=str(exc))
            raise AiQaError(
                ErrorCode.MCP_PROTOCOL_ERROR,
                f"MCP 调用失败: {exc}",
                details={"tool": call.name},
            ) from exc

        if not isinstance(result, types.CallToolResult):
            raise AiQaError(
                ErrorCode.MCP_PROTOCOL_ERROR,
                f"非预期的 MCP 返回类型: {type(result).__name__}",
                details={"tool": call.name},
            )

        structured = result.structured_content
        if structured is not None and not isinstance(structured, dict):
            raise AiQaError(
                ErrorCode.MCP_PROTOCOL_ERROR,
                "structuredContent 不是 JSON 对象",
                details={"tool": call.name},
            )

        text = "".join(
            block.text for block in result.content if isinstance(block, types.TextContent)
        )
        duration_ms = (time.monotonic() - started) * 1000.0
        self._trace.event(
            "tool_call_finished",
            tool=call.name,
            is_error=bool(result.is_error),
            duration_ms=round(duration_ms, 3),
            structured_keys=sorted(structured) if structured else [],
        )
        return McpCallResult(
            tool=call.name,
            is_error=bool(result.is_error),
            structured=structured,
            text=text,
            duration_ms=duration_ms,
        )

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise AiQaError(ErrorCode.MCP_PROTOCOL_ERROR, "MCP 会话尚未初始化")
        return self._session
