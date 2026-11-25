# ADR 0005: 独立规则引擎

- Status: Accepted
- Date: 2026-11-25

## Context

若由 LLM 判断 PASS/FAIL，结果不可重复、不可审计，且容易受提示注入影响。

## Decision

verdict 只由确定性 RuleEvaluator 根据 CaseFacts 计算。Rule 不调用 LLM、不修改事实、不依赖随机状态；同一 facts 多次执行结果必须一致。LLM 只能基于已持久化的 verdict 生成总结草稿。

## Consequences

- 结果可复现、可单测；每个 FAIL 必须带 evidence_refs。
- 新增断言需要实现并测试 Rule，而不是改提示词。
- 规则覆盖不足时只能得到 INCONCLUSIVE，而不是模型猜测。

## Alternatives

LLM 直接判定、模型生成断言表达式——拒绝。
