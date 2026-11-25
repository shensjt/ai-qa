# AI QA Agent 开发约束

开始任何开发前，必须阅读 `docs/IMPLEMENTATION_SPEC.md`，并只执行用户当前指定的 Phase。

- 遵守 domain/application/adapters 依赖方向。
- 不伪造 MCP，不允许 LLM 决定 verdict，不提供任意 shell。
- 未通过 Validator 和 plan_hash 校验的计划不得执行。
- 外部进程必须设置超时并在 finally 中清理。
- 不删除、跳过或放宽失败测试来制造通过。
- 每阶段运行定向测试和 `make check`，如实报告未运行项。
- 不跨 Phase 增加功能，不自行 git commit 或 git push。
- 设计变更必须同步更新规格或 ADR。
