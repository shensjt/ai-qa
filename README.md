# ai-qa-agent

受约束的 AI 测试 Agent（MVP）：

> RAG 提供带来源的项目知识，DeepSeek 生成结构化测试计划，确定性程序校验计划，Agent 通过真实 MCP 调用测试工具，HTTP/wrk 执行器产生事实，独立规则引擎判定结果，LLM 最后生成总结草稿。

完整规格与阶段计划见 [docs/IMPLEMENTATION_SPEC.md](docs/IMPLEMENTATION_SPEC.md)。

## 当前进度

- **Phase 0：工程骨架**。uv 工程、src layout、Pydantic Settings、统一错误模型、结构化日志、Typer CLI、`ai-qa doctor`、Ruff/mypy/pytest/pre-commit/Makefile/CI。
- **Phase 1：领域模型与计划校验**。状态枚举、TestPlan / CaseFacts / CaseResult / ToolResult、6 条 HTTP case 的 Catalog、四层校验（Schema/Catalog/Policy/Reference）+ plan_hash、修复预算、SQLite Repository（schema version、WAL、busy_timeout）、防路径逃逸的 Artifact Store。
- **Phase 2：真实 C++ Target 与 HTTP 执行**。TargetManager（白名单进程、端口检查、`/healthz` 就绪、进程组 SIGTERM→SIGKILL、finally 清理）、原始 socket HTTP 客户端、6 条用例执行器、独立规则引擎、ExecutionService，以及在真实 C++ WebServer 上的端到端验证。
- **Phase 3：真实 MCP Server 与固定计划 E2E**。独立 stdio MCP Server（`run_http_cases` / `run_http_benchmark` 占位 / `get_run_result`）、服务端 plan_hash 与 Policy 复验、官方 SDK 客户端、initialize/list/call trace、`ai-qa run` 固定计划 CLI。无需 LLM / API Key。

尚未实现（后续阶段）：RAG、DeepSeek Agent、wrk 压测、报告与 Demo。

## 环境要求

- WSL2 / Ubuntu
- Python 3.11+（仓库通过 `.python-version` 固定 3.12，uv 会自动安装）
- [uv](https://docs.astral.sh/uv/)
- 后续阶段需要：可运行的 C++ WebServer、wrk

## 快速开始

```bash
uv sync --locked
uv run ai-qa --help
uv run ai-qa doctor --config config/settings.example.yaml
make check
```

使用前把 `config/settings.example.yaml` 复制为 `config/settings.yaml`，填入真实绝对路径。`config/settings.yaml` 与 `.env` 已被 `.gitignore` 忽略，不会进入版本库。

PyPI 直连较慢时，可参考 [docs/development.md](docs/development.md#pypi-直连很慢时) 用镜像填充缓存，同时保持 `uv.lock` 不变。

## 命令

```bash
ai-qa --version
ai-qa doctor [--config PATH] [--json]
ai-qa run --plan examples/plans/http_smoke.json --config config/settings.yaml
```

`doctor` 只做只读检查（配置 schema、目标路径、白名单进程、wrk、可写目录、密钥是否设置），不启动被测服务。示例配置里的 `/absolute/path/...` 是占位符，因此对示例配置运行 `doctor` 会报告 ERROR 并以退出码 2 结束，这是预期行为。

`run` 读取固定计划，经校验与持久化后启动独立 MCP Server 子进程，通过真实 `initialize` / `tools/list` / `tools/call` 执行 6 条 HTTP 用例，再以 `get_run_result` 只读回读。不需要 LLM 或 API Key。trace 写入 `artifacts/<run_id>/trace.jsonl`。

## 退出码

| 退出码 | 含义 |
|---|---|
| 0 | 成功（或 doctor 无 ERROR） |
| 2 | 配置或命令参数错误 |
| 3 | 计划生成/校验失败 |
| 4 | 工作流完成，测试 verdict 为 FAIL |
| 5 | 工具、环境或基础设施 ERROR |
| 6 | 用户取消 |
| 7 | 内部未处理错误 |

## 质量门槛

```bash
make format      # ruff format .
make lint        # ruff check + format --check
make typecheck   # mypy（strict）
make test        # pytest
make check       # lint + typecheck + test
```

## 已知限制

- 已完成 Phase 0～3；没有 RAG、LLM 或 wrk 压测。
- `run_http_benchmark` 在 Phase 3 只做参数/计划校验，调用返回明确的“尚未实现（Phase 6）”工具错误，不伪造成功。
- MCP 客户端使用官方 `mcp` Python SDK（见 ADR 0011）；LangChain 接入留到 Phase 5。
- 数据库只建立计划、运行、结果、断言、Artifact 五组表；PROJECT/TARGET/SUITE/KNOWLEDGE/TOOL_CALL/PERF 在对应阶段加入。
- 真实 C++ 集成测试默认寻找 `/home/cpp/CPP/build/server/web_server`，可用 `AI_QA_CPP_SERVER` 覆盖；找不到时明确 skip。
- 已知被测 C++ WebServer 缺陷：请求在请求行之后分片到达会返回 405，`http.split_request` 会判 FAIL。这是产品行为问题，不是测试框架错误。
- `make eval-rag` 在 Phase 4 之前没有测试可收集。
- 示例配置不包含真实 C++ 路径，`doctor` 无法通过；本地 `config/settings.yaml` 已被 gitignore。
- 开发约束见 [CLAUDE.md](CLAUDE.md)，开发流程见 [docs/development.md](docs/development.md)。
