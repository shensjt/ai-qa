# ai-qa-agent

受约束的 AI 测试 Agent（MVP）：

> RAG 提供带来源的项目知识，DeepSeek 生成结构化测试计划，确定性程序校验计划，Agent 通过真实 MCP 调用测试工具，HTTP/wrk 执行器产生事实，独立规则引擎判定结果，LLM 最后生成总结草稿。

完整规格与阶段计划见 [docs/IMPLEMENTATION_SPEC.md](docs/IMPLEMENTATION_SPEC.md)。

## 当前进度

- **Phase 0：工程骨架**。uv 工程、src layout、Pydantic Settings、统一错误模型、结构化日志、Typer CLI、`ai-qa doctor`、Ruff/mypy/pytest/pre-commit/Makefile/CI。
- **Phase 1：领域模型与计划校验**。状态枚举、TestPlan / CaseFacts / CaseResult / ToolResult、6 条 HTTP case 的 Catalog、四层校验（Schema/Catalog/Policy/Reference）+ plan_hash、修复预算、SQLite Repository（schema version、WAL、busy_timeout）、防路径逃逸的 Artifact Store。
- **Phase 2：真实 C++ Target 与 HTTP 执行**。TargetManager（白名单进程、端口检查、`/healthz` 就绪、进程组 SIGTERM→SIGKILL、finally 清理）、原始 socket HTTP 客户端、6 条用例执行器、独立规则引擎、ExecutionService，以及在真实 C++ WebServer 上的端到端验证。

尚未实现（后续阶段）：MCP Server、RAG、DeepSeek Agent、报告、固定计划 CLI 与 Demo。

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
```

`doctor` 只做只读检查（配置 schema、目标路径、白名单进程、wrk、可写目录、密钥是否设置），不启动被测服务。示例配置里的 `/absolute/path/...` 是占位符，因此对示例配置运行 `doctor` 会报告 ERROR 并以退出码 2 结束，这是预期行为。

## 退出码

| 退出码 | 含义 |
|---|---|
| 0 | 成功（或 doctor 无 ERROR） |
| 2 | 配置或命令参数错误 |
| 3 | 计划生成/校验失败（后续阶段） |
| 4 | 测试 verdict 为 FAIL（后续阶段） |
| 5 | 工具、环境或基础设施 ERROR（后续阶段） |
| 6 | 用户取消（后续阶段） |
| 7 | 内部未处理错误（后续阶段） |

## 质量门槛

```bash
make format      # ruff format .
make lint        # ruff check + format --check
make typecheck   # mypy（strict）
make test        # pytest
make check       # lint + typecheck + test
```

## 已知限制

- 已完成 Phase 0～2；没有 MCP、RAG 或 LLM。
- 计划校验、持久化与 HTTP 执行已有单元/集成测试；固定计划还没有 CLI 入口（Phase 3）。
- 数据库只建立计划、运行、结果、断言、Artifact 五组表；PROJECT/TARGET/SUITE/KNOWLEDGE/TOOL_CALL/PERF 在对应阶段加入。
- 真实 C++ 集成测试默认寻找 `/home/cpp/CPP/build/server/web_server`，可用 `AI_QA_CPP_SERVER` 覆盖；找不到时明确 skip。
- 已知被测 C++ WebServer 缺陷：请求在请求行之后分片到达会返回 405，`http.split_request` 会判 FAIL。这是产品行为问题，不是测试框架错误。
- `make test-contract` / `test-e2e` / `eval-rag` 在对应阶段之前没有测试可收集。
- 示例配置不包含真实 C++ 路径，`doctor` 无法通过；本地 `config/settings.yaml` 已被 gitignore。
- 开发约束见 [CLAUDE.md](CLAUDE.md)，开发流程见 [docs/development.md](docs/development.md)。
