"""Agent 编排：RAG → 计划/修复 → 校验 → MCP 工具 → 总结。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ai_qa.adapters.deepseek_chat import DeepSeekChat
from ai_qa.adapters.mcp_client import McpServerLaunchConfig, McpStdioGateway
from ai_qa.agent.planner import PlanProposer
from ai_qa.agent.schemas import AgentExecution, AgentRunOutcome
from ai_qa.agent.tool_agent import ToolAgent
from ai_qa.bootstrap import AppContext, build_knowledge_service
from ai_qa.domain.enums import PlanMode, RunState
from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.models import new_test_run, utc_now
from ai_qa.validation.policy import policy_from_settings
from ai_qa.validation.validator import PlanValidator


class AgentRunner:
    def __init__(
        self,
        context: AppContext,
        *,
        allow_benchmark: bool = False,
        top_k: int | None = None,
    ) -> None:
        self._context = context
        self._allow_benchmark = allow_benchmark
        self._top_k = top_k

    async def execute(self, goal: str, *, target_id: str | None = None) -> AgentRunOutcome:
        context = self._context
        settings = context.settings
        configured_target = settings.target.target_id
        if target_id is not None and target_id != configured_target:
            raise AiQaError(
                ErrorCode.TARGET_NOT_FOUND,
                f"--target {target_id} 与配置的 target_id {configured_target} 不一致",
                details={
                    "requested": target_id,
                    "configured": configured_target,
                },
            )

        chat = DeepSeekChat(timeout=float(settings.agent.llm_timeout_seconds))
        snapshot = build_knowledge_service(context).search(goal, top_k=self._top_k)

        policy = policy_from_settings(settings, allow_benchmark=self._allow_benchmark)
        proposer = PlanProposer(
            chat,
            PlanValidator(context.catalog, policy),
            context.catalog,
            policy,
        )
        proposal = await proposer.propose(
            goal=goal,
            target_id=configured_target,
            snapshot=snapshot,
        )
        plan = proposal.plan

        context.plan_repository.save_plan(plan)
        run = new_test_run(
            plan_id=plan.plan_id,
            target_id=configured_target,
            mode=PlanMode.AGENT_PLAN,
            environment={
                "goal": goal,
                "python": sys.version.split()[0],
                "config": str(context.paths.config),
                "catalog": str(context.paths.catalog),
                "retrieval_degraded": snapshot.degraded,
            },
        )
        run.state = RunState.VALIDATED
        run.updated_at = utc_now()
        context.run_repository.create_run(run)

        trace_path = context.paths.artifacts / run.run_id / "trace.jsonl"
        launch = McpServerLaunchConfig(
            command=sys.executable,
            args=("-m", "ai_qa.mcp_server"),
            env=context.paths.server_env(),
        )
        async with McpStdioGateway(launch, trace_path) as gateway:
            await gateway.list_tools()
            tool_agent = ToolAgent(
                chat=chat,
                summarizer=chat,
                gateway=gateway,
                max_tool_calls=settings.agent.max_tool_calls,
                max_result_chars=settings.runtime.max_log_chars_for_llm,
            )
            tool_result, run_result, exchanges, summary = await tool_agent.execute(
                run=run, plan=plan
            )

        summary_artifact = context.artifact_store.write_text(
            run.run_id,
            "summary.md",
            summary,
            "llm_summary",
            mime="text/markdown; charset=utf-8",
        )
        context.artifact_repository.save_artifact(summary_artifact)
        repair_artifact = None
        if plan.repair_count > 0:
            repair_artifact = context.artifact_store.write_text(
                run.run_id,
                "plan_repairs.json",
                json.dumps(
                    [attempt.model_dump(mode="json") for attempt in proposal.attempts],
                    ensure_ascii=False,
                    indent=2,
                ),
                "plan_repair",
                mime="application/json; charset=utf-8",
            )
            context.artifact_repository.save_artifact(repair_artifact)

        return AgentRunOutcome(
            run=run,
            proposal=proposal,
            execution=AgentExecution(
                tool_result=tool_result,
                run_result=run_result,
                summary=summary,
                summary_artifact=summary_artifact,
                repair_artifact=repair_artifact,
                exchanges=exchanges,
            ),
            trace_path=Path(trace_path),
            retrieval_degraded=snapshot.degraded,
        )
