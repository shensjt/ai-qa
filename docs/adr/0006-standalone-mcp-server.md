# ADR 0006: 独立 MCP Server

- Status: Accepted
- Date: 2026-11-25

## Context

工具执行需要清晰权限边界、可审计协议记录，并避免模型通过直接函数调用绕过校验。

## Decision

使用官方 MCP Python SDK 建立独立 stdio 子进程 Server。Server 按 `run_id`/`plan_id`/`plan_hash` 从持久化存储加载已校验计划，并重新执行参数、hash、目标与 Policy 校验；返回声明 inputSchema/outputSchema 的结构化结果。

## Consequences

- 有真实 initialize/tools/list/tools/call 协议 trace。
- 父进程与 Server 只共享最小环境（绝对路径配置），不共享内存对象。
- 额外复杂度：进程管理、超时、stdio 日志隔离（stdout 只用于协议）。

## Alternatives

在应用内直接 import 工具函数并称为 MCP——拒绝；HTTP MCP 传输推迟。
