# ADR 0004: Case Catalog 白名单

- Status: Accepted
- Date: 2026-11-25

## Context

LLM 生成的测试计划可能虚构用例、传入任意参数，或让计划与执行内容不一致。

## Decision

所有可执行用例必须来自代码审查后的 Case Catalog，计划只能引用 `case_id` 与 Catalog 声明的受限参数。校验时把 Catalog 与 PlanItem 合并为内部 `ResolvedPlanItem`，执行器只接收 ResolvedPlanItem。

## Consequences

- 新增用例必须修改受信 Catalog，不能由模型自由生成。
- 参数类型、范围、默认值、超时和 rule_set 都有唯一来源。
- Catalog 的扩展需要代码审查与测试。

## Alternatives

自由生成测试脚本、模型提供 expected/rule 表达式——拒绝。
