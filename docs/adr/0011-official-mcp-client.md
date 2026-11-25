# ADR 0011: Phase 3 使用官方 MCP Python SDK 客户端

- Status: Accepted
- Date: 2026-11-25

## Context

规格 §2 冻结的 MCP Client 是 `langchain-mcp-adapters`，用于 Phase 5 把 MCP 工具暴露给 LangChain Agent。Phase 3 只需要确定性的 stdio 客户端：启动独立 Server、记录 initialize/list/call、校验 structuredContent。此时引入 LangChain Agent 栈既无使用者，也会把阶段依赖提前。

## Decision

Phase 3 的 `McpStdioGateway` 直接使用官方 `mcp` Python SDK（`ClientSession` + `stdio_client`），锁定 `mcp==2.3.0`。Phase 5 若需要把工具接入 LangChain，再评估 `langchain-mcp-adapters` 并新增 ADR；同一代码路径不同时维护两套客户端。

## Consequences

- Phase 3 无 LangChain/DeepSeek 依赖，固定计划 E2E 可在无 API Key 环境运行。
- 协议层（initialize/list/call、structuredContent、isError）由官方 SDK 实现，trace 记录真实事件。
- Phase 5 需要额外决定：复用当前 Gateway 提供结构化工具结果，或新增 LangChain 适配器。届时应保持上层 Port 不变。

## Alternatives

- 立即引入 `langchain-mcp-adapters`：为 Phase 3 带来无用的 Agent 依赖，且其工具封装会丢失部分 structuredContent 细节。
- 自己实现 JSON-RPC：重复官方 SDK 工作，违背"使用官方 MCP Python SDK"的规格要求。
