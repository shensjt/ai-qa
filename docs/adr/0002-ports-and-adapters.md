# ADR 0002: Ports and Adapters

- Status: Accepted
- Date: 2026-11-25

## Context

项目会同时涉及 LLM、向量库、SQLite、MCP、子进程执行等多种外部技术，若框架类型直接进入业务层，测试和替换都会困难。

## Decision

领域与应用层只依赖抽象 Port（ChatPlanner、KnowledgeRetriever、EmbeddingProvider、VectorStore、McpToolGateway、TestExecutor、RuleEvaluator、Repository、ArtifactStore、ReportGenerator）。由 `bootstrap.py` 作为 composition root 注入具体实现。

## Consequences

- 单测可用 Fake/临时目录/自建 socket 替换外部实现。
- 第三方 SDK 升级被限制在 adapters 内。
- 需要维护接口与 Fake 实现的一致性契约。

## Alternatives

业务层直接调用框架 API；测试时全局打补丁——放弃，难以保证错误语义一致。
