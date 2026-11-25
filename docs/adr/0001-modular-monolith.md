# ADR 0001: 模块化单体

- Status: Accepted
- Date: 2026-11-25

## Context

MVP 需要快速交付、便于排障，且单人维护。拆分服务会引入部署、网络与一致性问题，与当前目标不符。

## Decision

采用单一 Python 仓库、模块化单体：按 domain/application/adapters 分层，模块间通过 Port 隔离。

## Consequences

- 交付和调试成本低；跨层调用必须遵守依赖方向。
- 后续可把 MCP Server、执行器等进程边界单独抽出，而不重写领域模型。

## Alternatives

微服务、独立执行服务、消息队列异步编排——均推迟到有真实规模需求时再评估。
