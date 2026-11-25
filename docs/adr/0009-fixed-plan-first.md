# ADR 0009: 先固定计划后接 LLM

- Status: Accepted
- Date: 2026-11-25

## Context

若第一天就把执行链路绑定到模型，模型/网络问题会掩盖执行器、MCP、规则引擎的问题，也无法在无 API Key 环境演示。

## Decision

先实现固定计划（FIXED_PLAN）模式：加载已校验计划，经真实 MCP 执行并产出报告，全程不需要 LLM。校验、Catalog、Policy、plan_hash 与安全规则在两种模式下完全一致，固定计划不是绕过校验的后门。

## Consequences

- 无 AI 也能验证执行主链路，并作为 Agent 失败时的降级演示。
- 两种校验上下文必须区分：Agent 模式要求携带 RetrievalSnapshot；固定计划模式允许 refs 为空，但非空 refs 仍须可验证。

## Alternatives

先接 LLM 再补执行链——不采用。
