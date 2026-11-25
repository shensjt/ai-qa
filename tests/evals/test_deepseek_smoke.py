from __future__ import annotations

import asyncio
import json
import os

import pytest

from ai_qa.adapters.deepseek_chat import DeepSeekChat
from ai_qa.application.ports import ToolDescriptor
from ai_qa.settings import load_local_env

pytestmark = pytest.mark.agent_llm


def _require_key() -> None:
    load_local_env()
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        pytest.skip("DEEPSEEK_API_KEY 未配置，跳过真实 DeepSeek smoke test")


def test_deepseek_json_output() -> None:
    _require_key()

    async def flow() -> None:
        chat = DeepSeekChat(timeout=60.0)
        text = await chat.complete(
            system="只输出一个 JSON 对象，不要解释，不要 Markdown。",
            user='返回 {"ok": true, "kind": "smoke"}',
        )
        payload = json.loads(text)
        assert payload["ok"] is True
        assert payload["kind"] == "smoke"

    asyncio.run(flow())


def test_deepseek_tool_call() -> None:
    _require_key()

    async def flow() -> None:
        chat = DeepSeekChat(timeout=60.0)
        decision = await chat.decide(
            system="你是工具选择器。必须调用 run_http_cases，不要输出文本。",
            user="调用 run_http_cases",
            tools=[
                ToolDescriptor(
                    name="run_http_cases",
                    description="执行已校验计划中的 HTTP 用例",
                    input_schema={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                )
            ],
            history=[],
        )
        assert any(call.name == "run_http_cases" for call in decision.calls)

    asyncio.run(flow())
