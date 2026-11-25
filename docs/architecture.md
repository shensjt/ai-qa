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

## 当前实现（Phase 0）

| 模块 | 职责 |
|---|---|
| `ai_qa/domain/errors.py` | 错误码、ErrorInfo、AiQaError、退出码映射 |
| `ai_qa/settings.py` | 配置 schema（strict）、YAML 加载、CONFIG_INVALID 映射 |
| `ai_qa/log.py` | JSON 日志格式、敏感键脱敏 |
| `ai_qa/doctor.py` | 只读诊断：路径、白名单进程、wrk、可写目录、密钥是否存在 |
| `ai_qa/cli.py` | `--version`、`doctor`，统一错误输出与退出码 |

执行器、MCP Server、RAG、Agent、持久化与报告按 Phase 1～6 逐步加入；各阶段只创建需要的模块，不预先生成空文件。
