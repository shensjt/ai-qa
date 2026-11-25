from __future__ import annotations

import asyncio
import json
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from ai_qa.adapters.mcp_client import McpServerLaunchConfig, McpStdioGateway
from ai_qa.application.ports import McpCallResult, ToolCall, ToolDescriptor
from ai_qa.bootstrap import AppContext, build_app_context
from ai_qa.domain.enums import PlanMode, RunState
from ai_qa.domain.enums import TestStatus as Status
from ai_qa.domain.errors import ErrorCode
from ai_qa.domain.models import new_test_run, utc_now
from ai_qa.domain.plans import StoredPlan
from ai_qa.domain.results import RunResult, ToolFailureResult, ToolResult
from ai_qa.settings import load_settings
from ai_qa.validation.policy import policy_from_settings
from ai_qa.validation.validator import PlanValidator, ValidationContext

pytestmark = pytest.mark.contract

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET_ID = "cpp-webserver-local"
SECRET_MARKER = "TOP-SECRET-MARKER-12345"

FAKE_WRK = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "wrk 4.2.0"; exit 0; fi
cat <<'OUT'
Running 5s test @ http://127.0.0.1/
  2 threads and 10 connections
  12345 requests in 5.00s, 10.00MB read
Requests/sec:   2469.00
Transfer/sec:      2.00MB
Socket errors: connect 0, read 0, write 0, timeout 0
OUT
"""

FAKE_TARGET = """#!/usr/bin/env python3
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
for flag in ("--host", "--port", "--root", "--threads", "--timeout", "--cache-bytes"):
    parser.add_argument(flag)
args, _ = parser.parse_known_args()

HTML = b"<html><body>fixture-index</body></html>"
SECRET = b"TOP-SECRET-MARKER-12345"

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, b'{"status":"ok"}', "application/json")
        elif ".." in self.path or "%2e" in self.path.lower() or "%2f" in self.path.lower():
            self._send(403, b"403 Forbidden\\n", "text/plain")
        elif self.path == "/outside_secret.txt":
            self._send(200, SECRET, "text/plain")
        else:
            self._send(200, HTML, "text/html")

    def do_HEAD(self):
        self.do_GET()

    def log_message(self, *args):
        pass

server = ThreadingHTTPServer((args.host, int(args.port)), Handler)
server.serve_forever()
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class McpTestEnv:
    context: AppContext
    plan: StoredPlan
    run_id: str
    trace_path: Path
    config_path: Path


@pytest.fixture
def mcp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    target_root = tmp_path / "target"
    www = target_root / "www"
    www.mkdir(parents=True)
    (www / "index.html").write_text("<html><body>fixture-index</body></html>", encoding="utf-8")
    (target_root / "outside_secret.txt").write_text(SECRET_MARKER, encoding="utf-8")
    executable = target_root / "fake_server"
    executable.write_text(FAKE_TARGET, encoding="utf-8")
    executable.chmod(0o755)
    fake_wrk = target_root / "wrk"
    fake_wrk.write_text(FAKE_WRK, encoding="utf-8")
    fake_wrk.chmod(0o755)

    port = _free_port()
    data: dict[str, Any] = yaml.safe_load(
        (REPO_ROOT / "config" / "settings.example.yaml").read_text(encoding="utf-8")
    )
    data["project"]["repo_root"] = str(target_root)
    data["target"]["target_id"] = TARGET_ID
    data["target"]["executable_path"] = str(executable)
    data["target"]["working_directory"] = str(target_root)
    data["target"]["www_root"] = str(www)
    data["target"]["port"] = port
    data["runtime"]["allowed_processes"] = [str(executable), str(fake_wrk)]
    data["benchmark"]["wrk_path"] = str(fake_wrk)
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    sqlite_path = tmp_path / "ai_qa.db"
    artifact_root = tmp_path / "artifacts"
    catalog_path = REPO_ROOT / "config" / "cases" / "http.yaml"
    monkeypatch.setenv("SQLITE_PATH", str(sqlite_path))
    monkeypatch.setenv("ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("AI_QA_CATALOG", str(catalog_path))

    context = build_app_context(config_path)
    settings = load_settings(config_path).settings
    policy = policy_from_settings(settings, allow_benchmark=True)
    plan_data = {
        "schema_version": "1.0",
        "goal": "mcp contract",
        "target_id": TARGET_ID,
        "items": [
            {"case_id": "http.get_index", "params": {}},
            {"case_id": "http.head", "params": {}},
            {"case_id": "http.keep_alive", "params": {}},
            {"case_id": "http.pipelined", "params": {}},
            {"case_id": "http.split_request", "params": {"split_sizes": [1, 3, 7]}},
            {"case_id": "http.path_traversal", "params": {}},
        ],
        "benchmark": {
            "enabled": True,
            "tool": "wrk",
            "path": "/",
            "duration_seconds": 10,
            "connections": 10,
            "threads": 2,
        },
        "limits": {"max_cases": 6, "run_timeout_seconds": 120},
    }
    validated = PlanValidator(context.catalog, policy).validate(
        plan_data, ValidationContext(mode=PlanMode.FIXED_PLAN)
    )
    assert validated.plan is not None, [i.model_dump() for i in validated.issues]
    plan = validated.plan
    context.plan_repository.save_plan(plan)
    run = new_test_run(plan_id=plan.plan_id, target_id=TARGET_ID, mode=PlanMode.FIXED_PLAN)
    run.state = RunState.VALIDATED
    run.updated_at = utc_now()
    context.run_repository.create_run(run)

    env = McpTestEnv(
        context=context,
        plan=plan,
        run_id=run.run_id,
        trace_path=artifact_root / run.run_id / "trace.jsonl",
        config_path=config_path,
    )
    yield env
    context.close()


def _gateway(env: McpTestEnv) -> McpStdioGateway:
    return McpStdioGateway(
        McpServerLaunchConfig(
            command=sys.executable,
            args=("-m", "ai_qa.mcp_server"),
            env=env.context.paths.server_env(),
        ),
        env.trace_path,
    )


def _call_arguments(env: McpTestEnv, *, plan_hash: str | None = None) -> dict[str, str]:
    return {
        "run_id": env.run_id,
        "plan_id": env.plan.plan_id,
        "plan_hash": plan_hash if plan_hash is not None else env.plan.plan_hash,
    }


def _run_flow(
    env: McpTestEnv,
) -> tuple[list[ToolDescriptor], McpCallResult, McpCallResult]:
    async def flow() -> tuple[list[ToolDescriptor], McpCallResult, McpCallResult]:
        async with _gateway(env) as gateway:
            tools = await gateway.list_tools()
            run_result = await gateway.call_tool(
                ToolCall(name="run_http_cases", arguments=_call_arguments(env)),
                timeout_seconds=60,
            )
            readback = await gateway.call_tool(
                ToolCall(name="get_run_result", arguments={"run_id": env.run_id}),
                timeout_seconds=30,
            )
        return tools, run_result, readback

    return asyncio.run(flow())


def _call_single(env: McpTestEnv, call: ToolCall) -> McpCallResult:
    async def flow() -> McpCallResult:
        async with _gateway(env) as gateway:
            return await gateway.call_tool(call, timeout_seconds=60)

    return asyncio.run(flow())


def test_list_tools_declares_schemas(mcp_env: McpTestEnv) -> None:
    async def flow() -> list[ToolDescriptor]:
        async with _gateway(mcp_env) as gateway:
            return await gateway.list_tools()

    tools = asyncio.run(flow())
    by_name = {tool.name: tool for tool in tools}

    assert set(by_name) == {"run_http_cases", "run_http_benchmark", "get_run_result"}
    for tool in tools:
        assert tool.input_schema["type"] == "object"
        assert tool.output_schema is not None
        assert "properties" in tool.output_schema
    assert set(by_name["run_http_cases"].input_schema["properties"]) == {
        "run_id",
        "plan_id",
        "plan_hash",
    }


def test_fixed_plan_runs_through_real_mcp(mcp_env: McpTestEnv) -> None:
    tools, response, readback = _run_flow(mcp_env)

    assert {tool.name for tool in tools} >= {"run_http_cases", "get_run_result"}
    assert response.is_error is False
    assert response.structured is not None
    tool_result = ToolResult.model_validate(response.structured)
    assert tool_result.run_id == mcp_env.run_id
    assert tool_result.execution_status.value == "SUCCEEDED"
    assert len(tool_result.case_results) == 6
    assert tool_result.test_status is Status.PASS
    assert tool_result.artifacts

    assert json.loads(response.text) == response.structured

    assert readback.is_error is False
    assert readback.structured is not None
    run_result = RunResult.model_validate(readback.structured)
    assert run_result.run.run_id == mcp_env.run_id
    assert len(run_result.case_results) == 6
    assert run_result.artifacts
    assert run_result.perf is None

    events = [
        json.loads(line)["event"]
        for line in mcp_env.trace_path.read_text(encoding="utf-8").splitlines()
    ]
    assert "mcp_initialized" in events
    assert "mcp_tools_listed" in events
    assert events.count("tool_call_finished") == 2


def test_benchmark_smoke(mcp_env: McpTestEnv) -> None:
    async def flow() -> tuple[McpCallResult, McpCallResult, McpCallResult]:
        async with _gateway(mcp_env) as gateway:
            cases = await gateway.call_tool(
                ToolCall(name="run_http_cases", arguments=_call_arguments(mcp_env)),
                timeout_seconds=60,
            )
            bench = await gateway.call_tool(
                ToolCall(
                    name="run_http_benchmark",
                    arguments=_call_arguments(mcp_env),
                ),
                timeout_seconds=60,
            )
            readback = await gateway.call_tool(
                ToolCall(name="get_run_result", arguments={"run_id": mcp_env.run_id}),
                timeout_seconds=30,
            )
        return cases, bench, readback

    cases, bench, readback = asyncio.run(flow())

    assert cases.is_error is False
    assert bench.is_error is False
    assert bench.structured is not None
    tool_result = ToolResult.model_validate(bench.structured)
    assert tool_result.tool == "run_http_benchmark"
    assert tool_result.execution_status.value == "SUCCEEDED"
    assert tool_result.test_status is Status.INCONCLUSIVE
    assert any(artifact.name.endswith("wrk.txt") for artifact in tool_result.artifacts)

    assert readback.is_error is False and readback.structured is not None
    run_result = RunResult.model_validate(readback.structured)
    assert run_result.perf is not None
    assert run_result.perf.tool == "wrk"
    assert run_result.perf.metrics["requests_per_sec"] == 2469.0


def test_plan_hash_mismatch_rejected(mcp_env: McpTestEnv) -> None:
    response = _call_single(
        mcp_env,
        ToolCall(
            name="run_http_cases",
            arguments=_call_arguments(mcp_env, plan_hash="sha256:" + "0" * 64),
        ),
    )

    assert response.is_error is True
    assert response.structured is not None
    failure = ToolFailureResult.model_validate(response.structured)
    assert failure.error.code is ErrorCode.PLAN_HASH_MISMATCH
    assert not (mcp_env.context.paths.artifacts / "_target-logs" / "target.stdout.log").exists()


def test_invalid_arguments_return_tool_error(mcp_env: McpTestEnv) -> None:
    response = _call_single(
        mcp_env,
        ToolCall(
            name="run_http_cases",
            arguments={"run_id": mcp_env.run_id, "plan_id": mcp_env.plan.plan_id},
        ),
    )

    assert response.is_error is True
    assert response.structured is not None
    failure = ToolFailureResult.model_validate(response.structured)
    assert failure.error.code is ErrorCode.MCP_TOOL_ERROR
    assert "Schema" in failure.error.message


def test_cli_run_fixed_plan(mcp_env: McpTestEnv, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ai_qa.cli import app

    plan_data = mcp_env.plan.plan.model_dump(mode="json")
    plan_data["benchmark"] = None
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan_data), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["run", "--plan", str(plan_file), "--config", str(mcp_env.config_path)],
    )

    assert result.exit_code == 0, result.output
    assert "verdict: PASS" in result.output
    assert "trace:" in result.output
