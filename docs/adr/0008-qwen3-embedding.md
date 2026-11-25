# ADR 0008: Qwen3-Embedding-0.6B

- Status: Accepted
- Date: 2026-11-25

## Context

知识库包含中英文技术文档与代码，需要本地可运行、无需 API 成本的 Embedding，并支持 1024 维度向量。

## Decision

默认使用 `sentence-transformers` 加载 `Qwen/Qwen3-Embedding-0.6B`，经 `EmbeddingProvider` 注入；Chroma 使用 cosine collection。固定记录模型、revision、维度与归一化配置；任一变化必须重建 Collection。模型不可用时显式降级 BM25 并标记。

## Consequences

- 首次运行需下载模型，CPU 可跑但较慢。
- 查询指令格式集中在 Qwen Adapter，按模型卡实现。
- 不混用不同模型向量；Collection 名称包含模型别名与维度。

## Alternatives

BGE、远程 Embedding API、4B/8B 模型——MVP 不采用。
