# ADR 0010: 短压测只做 smoke

- Status: Accepted
- Date: 2026-11-25

## Context

单次 wrk 运行的指标受机器负载、预热、缓存与网络抖动影响，不足以支撑容量结论或泄漏判定。

## Decision

MVP 的 wrk 执行器只做 `benchmark smoke`：默认约 10 秒、受资源上限约束、保存原始输出与解析指标。无历史基线时性能状态为 INCONCLUSIVE；资源增长最多标 WARN。完整流程（预热、三次重复、中位数）推迟到后续阶段。

## Consequences

- 报告不得声称长稳、容量或自动发现泄漏。
- 指标用于建立可复现的采集链路，而不是性能门槛。

## Alternatives

单次 wrk 设定性能门槛、长时间压测——不采用。
