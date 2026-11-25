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

`make test-contract`、`make test-e2e`、`make eval-rag` 已保留；在 Phase 3/4 引入对应测试前，它们会明确提示“尚无测试”，不会伪造通过。

## 测试约定

- `pytest` 默认只跑不需要真实 LLM/真实 C++ 目标的测试。
- 需要真实 LLM 的测试标记 `agent_llm`，默认不进入 CI。
- 契约测试标记 `contract`，端到端标记 `e2e`，评估标记 `eval`，真实目标集成标记 `integration`。
- 不删除或放宽失败断言来制造通过；无法运行的命令与未验证项必须如实报告。

## 阶段状态

| Phase | 内容 | 状态 |
|---|---|---|
| 0 | 仓库与工程骨架 | 已完成 |
| 1 | 领域模型、Catalog、Validator、Repository | 已完成 |
| 2 | C++ Target、6 条 HTTP 用例、Rule | 未开始 |
| 3 | 真实 MCP Server 与固定计划 E2E | 未开始 |
| 4 | Qwen Embedding、Chroma、BM25 | 未开始 |
| 5 | DeepSeek Agent 与修复循环 | 未开始 |
| 6 | wrk、报告与 Demo | 未开始 |
| 7 | 故障注入、安全、发布候选 | 未开始 |

## 代码结构（当前）

```text
src/ai_qa/
├─ __init__.py       # 版本
├─ __main__.py       # python -m ai_qa
├─ cli.py            # Typer 入口
├─ settings.py       # Pydantic 配置模型与加载
├─ doctor.py         # 只读环境诊断
├─ log.py            # JSON 日志与脱敏
├─ domain/
│  ├─ enums.py       # 状态枚举与保守聚合
│  ├─ errors.py      # ErrorCode / ErrorInfo / AiQaError / 退出码
│  ├─ models.py      # TestRun 与 ID 生成
│  ├─ plans.py       # TestPlan / ResolvedPlanItem / StoredPlan / plan_hash
│  └─ results.py     # CaseFacts / Assertion / CaseResult / ToolResult
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
└─ application/
   └─ ports.py       # Repository / ArtifactStore 协议
```

依赖方向：`cli/doctor/validation/persistence/artifacts` → `application.ports`/`domain`。`domain` 不导入任何第三方框架；Repository 不跨线程共享 Session。

## 提交约定

- 每个 Phase 形成可由人工审查的小步提交，由仓库所有者确认后提交。
- 编码助手不自行 `git commit` / `git push`。
- 设计变化同步更新 `docs/IMPLEMENTATION_SPEC.md` 或新增 ADR。
