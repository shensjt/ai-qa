# ADR 0012: Phase 5 使用 ChatDeepSeek + 显式工具调用循环

- Status: Accepted
- Date: 2026-11-25

## Context

规格 §2 冻结 Agent 使用 LangChain `create_agent`，§11.1 要求只有计划通过校验后才向 Agent 提供 MCP 工具。Phase 5 还需要：每次工具调用可审计、限制调用次数、记录 call_id、模型不能传 run_id/plan_id/plan_hash、总结不能修改 verdict。当前环境没有 DeepSeek API Key，`create_agent` 的 LangGraph 状态机难以用 Fake 覆盖到同等程度。

## Decision

使用 `ChatDeepSeek` + 显式 tool-calling loop（`ToolCallingChat` Port）：

- 规划/修复/总结通过 `ChatPlanner.complete()`；
- 工具决策通过 `ToolCallingChat.decide()`；
- 暴露给模型的只有两个零参数工具：`run_http_cases`、`get_run_result`；
- run_id/plan_id/plan_hash 由编排层注入，模型无法修改；
- 循环在编排层执行，强制调用上限、禁止重复执行、禁止未知工具。

## Consequences

- 权限、调用次数、call_id、错误分类都能在 Fake Chat 下完整单测。
- 不引入 LangGraph/`create_agent` 的状态与中间件复杂度；依赖仍为 LangChain（`langchain-deepseek`）。
- 代价：失去 `create_agent` 内置的多步规划/工具重试能力。若后续需要，必须新增 ADR 并替换整个循环，不能两套并存。

## Alternatives

- `create_agent` + LangGraph：更贴近规格冻结项，但在无真实 API Key 的情况下难以验证工具授权和调用上限。
- 直接调用 DeepSeek OpenAI 兼容接口：绕过 LangChain，偏离规格的 LLM 选型。
