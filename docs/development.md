# 开发指南

## 环境准备

```bash
# 安装 uv（已安装则跳过）
curl -LsSf https://astral.sh/uv/install.sh | sh

cd /home/<user>/projects/ai-qa-agent
uv sync --locked          # 按锁文件创建 .venv 并安装
uv run pre-commit install # 可选：启用提交钩子
```

`.python-version` 固定 Python 3.12；uv 会自动下载对应解释器。

### PyPI 直连很慢时

uv.lock 记录的是 PyPI 源，直接换镜像会触发 `--locked` 校验失败。可先用镜像把锁定版本的 wheel 装进 `.venv` 并填充 uv 缓存，再按锁文件同步，这样不会改写 `uv.lock`：

```bash
uv venv
uv export --frozen --format requirements-txt --no-hashes --no-emit-project -o /tmp/ai_qa_requirements.txt
uv pip install --python .venv/bin/python \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  -r /tmp/ai_qa_requirements.txt
uv sync --locked
```

## DeepSeek Agent 配置

```bash
cp .env.example .env
# 编辑 .env：DEEPSEEK_API_KEY=...，必要时设置 DEEPSEEK_MODEL / DEEPSEEK_BASE_URL
chmod 600 .env        # .env 已被 gitignore
uv run ai-qa demo --goal "验证连接复用和路径安全" --config config/settings.yaml
```

`ai-qa` 启动时会加载仓库根目录的 `.env`（不覆盖已有环境变量）。Key 不会传给执行用 MCP Server。

## RAG 依赖与模型

- `torch` 通过 `pyproject.toml` 的 `pytorch-cpu` index 锁定为 CPU wheel；不要在本地用 CUDA 版本替代，否则锁文件会不一致。
- 首次 `knowledge build` 会下载 `Qwen/Qwen3-Embedding-0.6B`（约 1.19GB）。国内建议：

```bash
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1   # 镜像环境下 Xet 可能反复重试
uv run ai-qa knowledge build --project cpp-webserver --config config/settings.yaml
```

- 模型缓存位于 `HF_HOME`（默认 `~/.cache/huggingface`），不进入仓库。
- 模型不可用时检索会显式降级 BM25，`ai-qa knowledge search` 会打印降级原因。

## 配置

1. 复制 `config/settings.example.yaml` 为 `config/settings.yaml`。
2. 把所有 `/absolute/path/...` 替换为 WSL 中的真实绝对路径。
3. 运行 `uv run ai-qa doctor --config config/settings.yaml` 自检。

配置加载顺序（Phase 0 已实现部分）：

```text
Pydantic 内置约束
→ YAML 文件（--config > AI_QA_CONFIG > config/settings.yaml）
→ Pydantic 校验（extra="forbid"）
→ 失败统一映射为 CONFIG_INVALID（退出码 2）
```

路径字段要求绝对路径；存在性与可执行性由 `doctor` 检查，不在 schema 校验阶段假设。

## 常用命令

```bash
make setup        # uv sync --locked
make format       # ruff format .
make lint         # ruff check + ruff format --check
make typecheck    # mypy（strict）
make test         # pytest
make check        # lint + typecheck + test
make doctor       # 对示例配置运行 doctor（预期报错，见 README）
```

`make test-contract` 运行真实 MCP 契约测试（独立 Server 子进程 + 假目标）；`make test-e2e`、`make eval-rag` 在对应阶段之前会明确提示“尚无测试”，不会伪造通过。

固定计划 E2E：

```bash
uv run ai-qa run --plan examples/plans/http_smoke.json --config config/settings.yaml
```

## 测试约定

- `pytest` 默认只跑不需要真实 LLM/真实 C++ 目标的测试。
- 需要真实 LLM 的测试标记 `agent_llm`，默认不进入 CI。
- 契约测试标记 `contract`，端到端标记 `e2e`，评估标记 `eval`，真实目标集成标记 `integration`。
- MCP 契约测试（`tests/contract/test_mcp_server.py`）启动真实 `ai_qa.mcp_server` 子进程，但使用内置假目标，不需要 C++ 二进制或 API Key。
- RAG 评估默认跑 BM25（`make eval-rag`）；真实 Qwen Embedding 评估需要 `AI_QA_RUN_REAL_EMBEDDING=1`，首次运行会下载约 1.19GB 模型，国内建议 `HF_ENDPOINT=https://hf-mirror.com`。
- 真实 C++ 目标测试（`tests/integration/test_real_webserver.py`）默认使用 `/home/cpp/CPP/build/server/web_server`，可用 `AI_QA_CPP_SERVER` 指定其他路径；目标不存在时明确 skip，不伪装通过。
- 真实 DeepSeek 测试标记 `agent_llm`，需要 `DEEPSEEK_API_KEY`；默认 `make check` 通过 `-m 'not agent_llm'` 排除。显式运行：`uv run pytest -m agent_llm -q -s`。Agent 模式的 Key 只从环境变量或本地 `.env` 读取，不进入 MCP Server 环境。
- 不删除或放宽失败断言来制造通过；无法运行的命令与未验证项必须如实报告。

## 阶段状态

| Phase | 内容 | 状态 |
|---|---|---|
| 0 | 仓库与工程骨架 | 已完成 |
| 1 | 领域模型、Catalog、Validator、Repository | 已完成 |
| 2 | C++ Target、6 条 HTTP 用例、Rule | 已完成 |
| 3 | 真实 MCP Server 与固定计划 E2E | 已完成 |
| 4 | Qwen Embedding、Chroma、BM25 | 已完成 |
| 5 | DeepSeek Agent 与修复循环 | 已完成 |
| 6 | wrk、报告与 Demo | 未开始 |
| 7 | 故障注入、安全、发布候选 | 未开始 |

## 代码结构（当前）

```text
src/ai_qa/
├─ __init__.py       # 版本
├─ __main__.py       # python -m ai_qa
├─ cli.py            # Typer 入口（doctor / run）
├─ settings.py       # Pydantic 配置模型与加载
├─ bootstrap.py      # composition root：路径解析、DB、Catalog、Service 装配
├─ doctor.py         # 只读环境诊断
├─ log.py            # JSON 日志与脱敏
├─ domain/
│  ├─ enums.py       # 状态枚举与保守聚合
│  ├─ errors.py      # ErrorCode / ErrorInfo / AiQaError / 退出码
│  ├─ models.py      # TestRun 与 ID 生成
│  ├─ plans.py       # TestPlan / ResolvedPlanItem / StoredPlan / plan_hash
│  └─ results.py     # CaseFacts / Assertion / CaseResult / ToolResult / RunResult
├─ validation/
│  ├─ catalog.py     # Case Catalog 加载与完整性校验
│  ├─ params.py      # 参数类型与 request-target 安全规则
│  ├─ policy.py      # 服务端资源上限
│  ├─ validator.py   # 四层校验 + plan_hash
│  └─ repair.py      # 修复预算
├─ persistence/
│  ├─ database.py    # SQLite engine / Session 工厂（WAL、busy_timeout）
│  ├─ tables.py      # 五组核心表
│  ├─ migrations.py  # schema 版本
│  └─ repositories.py# Plan / Run / Result / Artifact Repository
├─ artifacts/
│  └─ store.py       # 原子写入、防路径逃逸
├─ execution/
│  ├─ http_protocol.py  # 原始 HTTP/1.1 客户端与响应解析
│  ├─ http_executor.py  # 6 条用例的事实采集
│  ├─ target_manager.py # 目标进程生命周期
│  └─ registry.py       # Executor 注册表
├─ rules/
│  ├─ http_rules.py  # 确定性断言
│  └─ evaluator.py   # CaseFacts → CaseResult（唯一 verdict 来源）
├─ adapters/
│  ├─ mcp_client.py  # 官方 SDK stdio 客户端（initialize/list/call）
│  ├─ deepseek_chat.py   # ChatDeepSeek：JSON 规划与工具决策
│  ├─ qwen_embeddings.py # Qwen3-Embedding-0.6B（sentence-transformers）
│  ├─ chroma_store.py    # Chroma cosine collection 与 metadata 校验
│  └─ trace.py       # JSONL 协议 trace
├─ knowledge/
│  ├─ chunking.py    # Markdown 感知切块（标题/代码块/表格/重叠）
│  ├─ ingest.py      # manifest、hash、增量重建、陈旧清理
│  ├─ bm25.py        # 中文单字+双字 BM25 降级
│  └─ eval.py        # Recall@5 / MRR / 引用合法率
├─ agent/
│  ├─ prompts.py    # 规划/修复/工具/总结提示词
│  ├─ planner.py    # 计划生成与最多 2 次修复
│  ├─ tool_agent.py # 受约束工具循环（零参数工具、ID 注入、调用上限）
│  └─ schemas.py    # PlanProposal / AgentExecution
├─ mcp_server/
│  ├─ __main__.py    # python -m ai_qa.mcp_server
│  ├─ server.py      # 工具声明、调用分发、stdio 入口
│  ├─ tools.py       # 服务端复验 + ExecutionService
│  └─ schemas.py     # 工具输入/输出 JSON Schema
└─ application/
   ├─ ports.py       # Repository / ArtifactStore / Target / Executor / Evaluator / Gateway 协议
   ├─ execution_service.py  # 启停目标 + 逐条执行 + 持久化
   ├─ fixed_plan_runner.py  # 固定计划 E2E 编排（CLI）
   ├─ mcp_payloads.py       # structuredContent 契约校验
   └─ agent_runner.py       # RAG → 计划/修复 → MCP → 总结
```

依赖方向：`cli/validation/persistence/artifacts/execution/rules/adapters/mcp_server` → `application.ports`/`domain`。`domain` 不导入任何第三方框架；Repository 不跨线程共享 Session；MCP Server 只读绝对路径环境变量，不依赖父进程内存。

## 提交约定

- 每个 Phase 形成可由人工审查的小步提交，由仓库所有者确认后提交。
- 编码助手不自行 `git commit` / `git push`。
- 设计变化同步更新 `docs/IMPLEMENTATION_SPEC.md` 或新增 ADR。
