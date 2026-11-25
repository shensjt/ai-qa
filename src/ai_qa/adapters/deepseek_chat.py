"""DeepSeek ChatModel 适配器（langchain-deepseek）。"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ai_qa.application.ports import (
    ToolCall,
    ToolDecision,
    ToolDescriptor,
    ToolExchange,
)
from ai_qa.domain.errors import AiQaError, ErrorCode

DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


class DeepSeekChat:
    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        key = (api_key or os.environ.get("DEEPSEEK_API_KEY", "")).strip()
        if not key:
            raise AiQaError(
                ErrorCode.CONFIG_INVALID,
                "未配置 DEEPSEEK_API_KEY；Agent 模式需要它，固定计划模式可用 ai-qa run",
                details={"hint": "export DEEPSEEK_API_KEY=... 或写入本地 .env（已被 gitignore）"},
            )
        from langchain_deepseek import ChatDeepSeek

        self._model = ChatDeepSeek(
            model_name=model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL),
            api_key=key,
            base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
            timeout=timeout,
        )

    async def complete(self, *, system: str, user: str) -> str:
        messages = [SystemMessage(content=system), HumanMessage(content=user)]
        try:
            response = await self._model.ainvoke(messages)
        except Exception as exc:  # 不记录密钥或完整请求体
            raise AiQaError(
                ErrorCode.LLM_REQUEST_FAILED,
                f"DeepSeek 请求失败: {type(exc).__name__}: {exc}"[:300],
            ) from exc
        text = _message_text(response)
        if not text.strip():
            raise AiQaError(ErrorCode.LLM_OUTPUT_INVALID, "DeepSeek 返回了空内容")
        return text

    async def decide(
        self,
        *,
        system: str,
        user: str,
        tools: list[ToolDescriptor],
        history: list[ToolExchange],
    ) -> ToolDecision:
        messages: list[Any] = [
            SystemMessage(content=system),
            HumanMessage(content=user),
        ]
        for exchange in history:
            messages.append(
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": exchange.tool,
                            "args": exchange.arguments,
                            "id": exchange.call_id,
                            "type": "tool_call",
                        }
                    ],
                )
            )
            messages.append(
                ToolMessage(
                    content=exchange.result_text,
                    tool_call_id=exchange.call_id,
                )
            )

        tool_specs = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                },
            }
            for tool in tools
        ]
        try:
            bound = self._model.bind_tools(tool_specs)
            response = await bound.ainvoke(messages)
        except Exception as exc:
            raise AiQaError(
                ErrorCode.LLM_REQUEST_FAILED,
                f"DeepSeek 工具调用失败: {type(exc).__name__}: {exc}"[:300],
            ) from exc

        calls = [
            ToolCall(
                name=str(call.get("name", "")),
                arguments=call.get("args") or {},
                call_id=call.get("id"),
            )
            for call in getattr(response, "tool_calls", []) or []
        ]
        return ToolDecision(calls=calls, text=_message_text(response))
