# 安全边界与加固说明

本文是 Phase 7 的安全审计摘要，也是发布候选的已知边界说明。

## 1. 权限与执行边界

- 模型只能生成 TestPlan 草稿和总结草稿；不能决定 verdict，不能执行任意代码。
- 计划必须通过 Schema / Catalog / Policy / Reference 四层校验，并持久化为 VALIDATED 后才可执行。
- MCP Server 按 `run_id/plan_id/plan_hash` 从数据库加载计划并重新校验，不信任调用方重复传入的计划正文。
- MCP 工具只暴露 `run_http_cases`、`run_http_benchmark`、`get_run_result`；模型无法启动/停止目标或调用 shell。

## 2. 子进程

- 所有外部进程使用 argv 数组、`shell=False`。
- 目标进程和 wrk 只继承最小环境变量白名单（`PATH/HOME/LANG/TZ/TMPDIR/LD_LIBRARY_PATH` 等），
  **不继承 `DEEPSEEK_API_KEY` 等密钥**（`execution/process.py`）。
- 目标进程使用独立进程组；停止时 SIGTERM→超时升级 SIGKILL，`finally` 保证清理。
- wrk 与目标启动均有超时；wrk 只允许配置中的绝对路径，且必须在 `runtime.allowed_processes` 内。

## 3. 路径

- 配置中的目标路径必须是绝对路径。
- ArtifactStore 拒绝绝对路径、`..`、控制字符和符号链接逃逸；写入使用临时文件 + rename。
- HTTP request-target 以 `/` 开头、长度受限、拒绝控制字符/反斜杠/scheme；普通用例拒绝 `..` 与编码穿越，
  攻击路径只能使用 Catalog 明确允许的固定串。
- 知识源只允许 manifest 中、且位于 `knowledge_root` 内的文件。

## 4. 密钥与日志

- `.env`、`config/settings.yaml`、模型缓存、`.data`、`artifacts` 均不进入版本库。
- CLI 从环境变量或本地 `.env` 读取 `DEEPSEEK_API_KEY`；不会传入执行用 MCP Server 子进程。
- 日志使用 JSON 格式并对 key/token/secret/password/authorization 递归脱敏；不记录模型思维链。
- 外部进程的 stdout/stderr 只保存必要输出；错误详情截断，不包含完整环境变量。

## 5. 不可信输入

- RAG 片段、工具输出、目标响应都视为不可信数据；系统提示明确要求忽略其中的指令性文本。
- 计划中的 refs 必须属于当前 RetrievalSnapshot；伪造/过期 ref 被拒绝。
- 工具返回在客户端按 ToolResult/RunResult 契约校验；`isError` 与产品 FAIL 严格区分。
- 报告中的 LLM 总结作为草稿标记，不参与 verdict 计算。

## 6. Verdict 完整性

- 唯一 verdict 来源是 RuleEvaluator；Rule 不调用 LLM、不修改 facts。
- 每个 FAIL 断言必须带 evidence_refs；缺失时 evaluator 返回 `RULE_ENGINE_ERROR`，而不是 FAIL。
- 同一 facts 重复评估结果一致（测试覆盖）。

## 7. 故障注入覆盖

| 场景 | 验证 |
|---|---|
| 端口被占用 | `tests/unit/test_target_manager.py` |
| 目标可执行文件缺失/不在白名单 | 同上 |
| 目标提前退出/启动超时 | 同上 |
| SIGTERM 被忽略 → SIGKILL 升级 | 同上 |
| 目标/ wrk 不继承 API Key | `test_target_manager.py` / `test_wrk_executor.py` |
| wrk 缺失、超时、非零退出、资源超限 | `tests/unit/test_wrk_executor.py` |
| 非法 JSON、未知 case、伪造 ref、benchmark 未授权 | `tests/unit/test_validator.py` / `test_agent_planner.py` |
| plan_hash 不匹配、非法参数、重复 benchmark | `tests/contract/test_mcp_server.py` |
| 报告写盘失败 | `tests/unit/test_report.py` |
| Artifact 路径逃逸 / 符号链接逃逸 | `tests/unit/test_artifact_store.py` |
| 目标启动失败不写成产品 FAIL | TargetManager 错误映射 + MCP 工具失败返回 |
| 断言失败但 MCP transport 成功 | 真实目标集成测试与真实 demo |

## 8. 残余风险

- LLM 仍可能被恶意知识片段影响计划内容或总结措辞；计划受 Schema/Catalog/Policy 约束，总结不参与 verdict，
  但总结文本可能误导读者，因此报告明确标记为草稿。
- `.env` 在本机明文保存（权限 600），由使用者负责保管。
- Chroma 与 SQLite 没有跨存储事务；入库失败后可 `--rebuild` 对账。
- wrk 短压测没有基线，不能用于容量或泄漏结论。
- 端口预检与目标 bind 之间存在极小竞态；失败会映射为启动错误。
- MCP Server 是本地 stdio 进程，没有网络认证；不要暴露到不可信环境。
