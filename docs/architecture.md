# 架构说明

本文描述依赖方向与各层职责。完整设计与不变量以 [IMPLEMENTATION_SPEC.md](IMPLEMENTATION_SPEC.md) 为准。

## 依赖方向

```text
cli / adapters / mcp_server / persistence / report
                      ↓
                 application
                      ↓
                    domain
```

- `domain`：纯数据模型、状态枚举、错误码、Port 协议；禁止导入 LangChain、Chroma、SQLAlchemy、MCP SDK、Typer。
- `application`：编排用例，只依赖 domain 和抽象 Port，不直接实例化外部服务。
- `adapters`：实现 Port，可依赖第三方 SDK。
- `mcp_server`：应用入口边界，只调用应用服务。
- `bootstrap.py`：composition root，负责创建并注入具体实现（有实际服务后引入）。

## 主链路（目标形态）

```text
CLI → RunOrchestrator
    → KnowledgeService（RAG）→ RetrievalSnapshot
    → TestAgent（DeepSeek 规划）
    → PlanValidator（Schema/Catalog/Policy/Reference + plan_hash）
    → MCP Client → 独立 QA MCP Server（stdio）
    → ExecutionService → TargetManager / Executor → CaseFacts
    → RuleEvaluator → AssertionResult / CaseResult（唯一 verdict 来源）
    → ReportService（Markdown + JSON/JSONL）
```

## 关键不变量

- LLM 只生成计划草稿和总结草稿，不决定 PASS/FAIL。
- 计划必须先校验并持久化 VALIDATED，MCP Server 按 `plan_id`/`plan_hash` 加载；不信任模型重复传入的计划正文。
- Executor 只产事实，RuleEvaluator 只判 verdict，Reporter 不修改事实与 verdict。
- 无任意 shell、任意路径、任意 URL；外部进程使用 argv 数组、超时和 `finally` 清理。
- 工具/环境错误映射为 ERROR，不能写成产品 FAIL；FAIL 必须带断言与证据。

## 当前实现（Phase 0～5）

| 模块 | 职责 |
|---|---|
| `ai_qa/domain/enums.py` | TestStatus / ExecutionStatus / RunState / PlanMode / 保守聚合 |
| `ai_qa/domain/errors.py` | 错误码、ErrorInfo、AiQaError、退出码映射 |
| `ai_qa/domain/plans.py` | TestPlan、PlanItem、ResolvedPlanItem、StoredPlan、规范化 plan_hash |
| `ai_qa/domain/results.py` | CaseFacts、AssertionResult、CaseResult、ToolResult、RunResult |
| `ai_qa/domain/models.py` | TestRun、ID 生成、UTC 时间 |
| `ai_qa/settings.py` | 配置 schema（strict）、YAML 加载、CONFIG_INVALID 映射 |
| `ai_qa/bootstrap.py` | composition root：绝对路径解析、SQLite、Catalog、Service 装配 |
| `ai_qa/log.py` | JSON 日志格式、敏感键脱敏 |
| `ai_qa/doctor.py` | 只读诊断：路径、白名单进程、wrk、可写目录、密钥是否存在 |
| `ai_qa/cli.py` | `--version`、`doctor`、`run`，统一错误输出与退出码 |
| `ai_qa/validation/catalog.py` | 6 条 HTTP case 的受信 Catalog 与完整性校验 |
| `ai_qa/validation/params.py` | 参数类型、request-target 与攻击路径白名单 |
| `ai_qa/validation/policy.py` | 从 Settings 推导服务端资源上限 |
| `ai_qa/validation/validator.py` | Schema/Catalog/Policy/Reference 四层校验，产出 StoredPlan |
| `ai_qa/validation/repair.py` | 计划修复预算（最多 2 次） |
| `ai_qa/persistence/*` | SQLite schema version、WAL/busy_timeout、四个 Repository |
| `ai_qa/artifacts/store.py` | 原子写入、SHA-256、防 `../`/符号链接逃逸 |
| `ai_qa/execution/http_protocol.py` | 原始 HTTP/1.1 客户端、Content-Length 边界、多响应缓冲 |
| `ai_qa/execution/http_executor.py` | 6 条用例的事实采集（GET/HEAD/keep-alive/pipeline/split/traversal） |
| `ai_qa/execution/target_manager.py` | 白名单进程、端口检查、`/healthz` 就绪、进程组 SIGTERM→SIGKILL、日志收集 |
| `ai_qa/execution/registry.py` | Executor 按 kind 分发 |
| `ai_qa/rules/http_rules.py` | 确定性断言（状态、长度、连接复用、顺序、秘密泄露） |
| `ai_qa/rules/evaluator.py` | CaseFacts → CaseResult，区分 ERROR 与产品 FAIL |
| `ai_qa/application/execution_service.py` | 启动目标、逐条执行、规则判定、持久化、finally 清理 |
| `ai_qa/application/fixed_plan_runner.py` | 固定计划校验、持久化、真实 MCP 调用与只读回读 |
| `ai_qa/application/ports.py` | Repository/ArtifactStore/Target/Executor/Evaluator/MCP Gateway 协议 |
| `ai_qa/adapters/mcp_client.py` | 官方 MCP SDK stdio 客户端；initialize/list/call trace |
| `ai_qa/adapters/trace.py` | JSONL 协议 trace |
| `ai_qa/mcp_server/server.py` | 独立 stdio Server、工具声明与调用分发 |
| `ai_qa/mcp_server/tools.py` | 服务端 plan_hash/目标/Policy 复验与 ExecutionService 调用 |
| `ai_qa/mcp_server/schemas.py` | 工具输入模型与 input/output JSON Schema |
| `ai_qa/knowledge/chunking.py` | Markdown 标题/代码块/表格感知切块与稳定 hash |
| `ai_qa/knowledge/ingest.py` | manifest、增量入库、陈旧 chunk 清理、Collection 校验 |
| `ai_qa/knowledge/bm25.py` | 中文单字+双字 BM25 显式降级 |
| `ai_qa/knowledge/eval.py` | Recall@5、MRR、无结果率、引用合法率 |
| `ai_qa/adapters/qwen_embeddings.py` | Qwen3-Embedding-0.6B、query 指令、模型元数据 |
| `ai_qa/adapters/chroma_store.py` | Chroma cosine collection、metadata 不一致拒绝复用 |
| `ai_qa/application/knowledge_service.py` | 知识构建、向量检索、BM25 降级、RetrievalSnapshot/ref_id |
| `ai_qa/adapters/deepseek_chat.py` | ChatDeepSeek：JSON 规划/修复与工具决策 |
| `ai_qa/agent/planner.py` | AGENT_PLAN 生成、四层校验、最多 2 次修复 |
| `ai_qa/agent/tool_agent.py` | 零参数工具、ID 注入、调用上限、总结不能改 verdict |
| `ai_qa/agent/prompts.py` | 规划/修复/工具/总结提示词与上下文预算 |
| `ai_qa/application/agent_runner.py` | RAG → 计划 → 校验 → MCP → 总结的端到端编排 |
| `ai_qa/application/mcp_payloads.py` | structuredContent 客户端契约校验 |

wrk 与报告按 Phase 6 逐步加入；各阶段只创建需要的模块，不预先生成空文件。
