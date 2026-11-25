# ADR 0007: SQLite + Chroma + Artifact Store

- Status: Accepted
- Date: 2026-11-25

## Context

关系型审计数据、向量检索和大体积原始证据的访问模式不同，放进同一存储会互相拖累。

## Decision

- SQLite（SQLAlchemy 2）保存 Run、Plan、CaseResult、Assertion、ToolCall 等业务事实，是 verdict 的唯一权威来源。
- Chroma 只保存向量与 chunk 映射，负责检索。
- Artifact Store 保存原始报文、日志、wrk 输出和报告，按 run 目录组织，记录 SHA-256 与 MIME。

## Consequences

- Chroma 与 SQLite 无分布式事务：入库必须幂等，并提供重建/对账命令。
- 大日志不进入 SQLite；返回给 LLM 的文本单独截断。
- SQLite 需启用 WAL、外键与 busy_timeout，写入型 Run 串行。

## Alternatives

全部塞进向量库、对象存储优先——不采用。
