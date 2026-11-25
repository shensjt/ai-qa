# ADR 0003: 单 Agent + 确定性 Orchestrator

- Status: Accepted
- Date: 2026-11-25

## Context

LLM 排障与规划能力强，但不可靠、不可审计。执行测试和写状态若交给自治 Agent，会失去权限边界和可重复性。

## Decision

只保留一个 TestAgent，分为两阶段：`propose_plan`（生成 TestPlan 草稿）与 `execute_validated_plan`（在校验通过后才获得 MCP 工具）。阶段切换、状态迁移和权限由 RunOrchestrator 决定。

## Consequences

- 规划保留模型能力，执行保持确定性。
- Agent 不能直接写 RunState，也不能决定 verdict。

## Alternatives

多 Agent 协作、完全自主 Agent、LangGraph 自由流程——MVP 阶段不采用。
