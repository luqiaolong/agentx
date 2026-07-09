# Proposal: 后端代码质量优化

## 背景

2026-07-09 对 `backend/app/` 全量代码审查发现 **8 个 Critical**、**16 个 High**、**30+ 个 Medium** 问题，覆盖框架违规（R1/R2/R3/R5/R11/R12/R13/R14/R15/R16/R18）、死代码、重复实现、运行时 bug 与硬编码。本提案旨在按风险与收益分批消除技术债，使后端严格对齐 AGENTS.md §1.1 框架优先序与 §3 反面清单。

关键发现：
1. **死代码与契约违规**：`deep/recovery.py` 零调用；`resumed` SSE 事件未在 §13 契约且前端不处理（违反 §18 红线）；`approval_flow.run_approval_loop` 与 `execution.run_agent_with_approval` 重复实现。
2. **Team 路径完全绕开框架**：`orchestrator.py`/`scheduler.py` 227+248 行手写状态机与并发，违反 R1/R2/R13；事件路由块在 scheduler.py 三处重复。
3. **子代理重复代码**：`rag_agent.py`≈`web_agent.py`（95% 重复）；`custom_agent.py` 逐字重复 base.py 的 5 个 `@tool` 定义；`make_cli_tools` 死代码。
4. **运行时 bug**：`milvus_client._on_skip` 闭包未绑定/残留 `i`；`mcp/client.py` tool_count 报总数 + stdio 子进程泄漏；`asyncio.run()` 在 LangChain 适配器同步方法中崩溃。
5. **框架合规缺口**：`graph.py` 手写历史截断（R11）+ StateGraph-hack 写 checkpoint（R18）；`rag_retrieve.py` 绕开 LangChain 适配器（R3/R16）；`RubricMiddleware` 未接入生产。

## 目标

1. **消除契约违规与死代码**（P0，零风险）：删除 `recovery.py`、`resumed` 事件、死导入、`make_cli_tools`。
2. **修复运行时 bug**（P1）：milvus 闭包、mcp tool_count/泄漏、asyncio.run 崩溃、sandbox store 阻塞。
3. **消除重复代码**（P1）：合并 rag/web 子代理为共享工厂；删除 custom_agent 重复工具；合并审批循环。
4. **Team 路径迁移到 LangGraph**（P2，最大收益）：用 `StateGraph` + `Send` 并行替代手写编排，消除 5 个 Critical R1/R2/R13 违规。
5. **框架合规**（P3）：`trim_messages` 替代手写截断；`rag_retrieve` 改用 LangChain 适配器；接入 `RubricMiddleware`。
6. **清理**（P4）：硬编码外置、过时注释更新、`Literal` 类型强化、重复 frozenset 合并。

## 非目标

- 不重构 SSE 事件契约结构（仅删除未契约的 `resumed` 事件）。
- 不改前端组件结构（除非删除 `resumed` 处理逻辑，当前前端无该逻辑故零改动）。
- 不替换 `SessionSandbox` 安全模型。
- 不改 Tauri/Rust 层。
- 不改 eval 框架核心结构（仅接入 RubricMiddleware 到生产）。
- 不做性能 profiling 驱动的微优化（仅修明显阻塞 bug）。

## 涉及范围

### P0 死代码/契约（零风险删除）
- `backend/app/deep/recovery.py` — 整文件删除
- `backend/app/deep/agent.py` — 删除 6 个 `as _xxx` 死导入
- `backend/app/deep/execution.py` — 删除 `resumed` 事件（L159）
- `backend/app/security/approval_flow.py` — 删除 `resumed` 事件（L538）
- `backend/app/subagents/base.py` — 删除 `make_cli_tools` 死代码 + 占位赋值

### P1 Bug 修复
- `backend/app/vectorstore/milvus_client.py` — 修 `_on_skip` 闭包 bug（L332-345）
- `backend/app/mcp/client.py` — 修 tool_count（L168-184）+ stdio 子进程泄漏（L225-238）
- `backend/app/embedding/tei_client.py` — 修 `asyncio.run()` 崩溃（L225,231）
- `backend/app/vectorstore/milvus_client.py` — 修 `asyncio.run()` 崩溃（L685,707）
- `backend/app/sandbox/store.py` — 同步 sqlite3 包 `asyncio.to_thread`

### P1 重复消除
- `backend/app/subagents/rag_agent.py` + `web_agent.py` — 合并为 `base.py:build_builtin_subagent`
- `backend/app/subagents/custom_agent.py` — 删除重复工具定义，改用 `make_*_tools()` + 过滤
- `backend/app/security/approval_flow.py` — 删除 `run_approval_loop`，迁移 10 处测试到 `execution.run_agent_with_approval`

### P2 Team 路径迁移（最大收益）
- `backend/app/team/orchestrator.py` — `run_team_path` 重写为 LangGraph `StateGraph`
- `backend/app/team/scheduler.py` — 7 分支 if/elif 拆为 `add_conditional_edges` 节点
- `backend/app/team/blackboard.py` — 转为 `TeamState(TypedDict)` + reducer
- `backend/app/team/aggregator.py` — 作为 StateGraph 聚合节点

### P3 框架合规
- `backend/app/router/graph.py` — `trim_messages` 替代手写截断（L291-293）
- `backend/app/router/graph.py` — `_append_messages_to_checkpointer` 用正确 checkpointer API（L437-521）
- `backend/app/tools/rag_retrieve.py` — 改用 `LangChainTeiEmbeddings`/`LangChainMilvusVectorStore`
- `backend/app/deep/harness.py` — 接入 `RubricMiddleware` 到 `create_agent`

### P4 清理
- 多文件 — 硬编码值外置到 `Settings`
- 多文件 — 更新 `interrupt_before` 过时注释为 `interrupt_on`
- `backend/app/api/schemas.py` — `decision`/`transport` 改 `Literal`
- `backend/app/security/approval_flow.py` — 合并重复 frozenset
- `backend/app/config/subagents.py` — 删除陈旧 `cli_execute`
- `backend/app/security/dangerous_tools.py` — 加入 `execute` 到 `FORBIDDEN_SUBAGENT_TOOLS`

## 验收标准

1. **P0**：`grep -r "from app.deep.recovery" backend/ tests/` 返回空；`grep -r "resumed" frontend/renderer/` 返回空；`ruff check` 通过。
2. **P1 Bug**：新增单测覆盖 milvus `_on_skip` 空列表、mcp tool_count 按服务器计数、asyncio.run 不在 async 上下文崩溃；现有测试全绿。
3. **P1 重复**：`rag_agent.py`+`web_agent.py` 总行数减少 ≥150 行；`custom_agent.py` 减少 ≥90 行；`approval_flow.run_approval_loop` 删除后 10 处测试迁移完成。
4. **P2 Team**：`orchestrator.py` 用 `StateGraph`；`scheduler.py` 7 分支拆为节点；`Blackboard` 为 `TypedDict`；Team 路径所有现有测试通过。
5. **P3 合规**：`graph.py` 无手写 `history[-max_msgs:]`；`_append_messages_to_checkpointer` 删除或改用 `aput`；`rag_retrieve.py` 调用 LangChain 适配器；`RubricMiddleware` 在 `create_agent` 中条件接入。
6. **P4 清理**：`grep -r "interrupt_before" backend/app/` 仅在历史注释或 ADR 中出现；`Settings` 含新增配置项；`schemas.py` 无 `str` 类型枚举字段。
7. **全局**：`uv run pytest tests/python/unit -m "not integration"` 全绿；`uv run ruff check backend/` 无错误。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| Team 迁移破坏现有行为 | 保留旧实现分支至迁移完成；A/B 对比 blackboard 结果；分批提交可 revert |
| asyncio.run 修复改语义 | 仅在无运行循环时用 `run_until_complete`，有循环时抛明确错误 |
| 审批循环合并导致测试 patch 失效 | 迁移测试时 patch `execution._is_interrupted` 而非本地副本 |
| RubricMiddleware 增加延迟 | 默认关闭，仅 rubric 显式配置时启用 |
| LangGraph checkpoint API 变化 | 锁定 LangGraph 版本；迁移前查阅官方 `BaseCheckpointSaver.aput` 文档 |

## 回滚策略

- 每个 P 级独立分支，可单独 revert。
- P0/P1 为纯删除/修复，无行为变化，回滚即恢复原状。
- P2 Team 迁移保留旧 `run_team_path` 在 git 历史，必要时 cherry-pick 恢复。
- P3 框架合规改动小且局部，回滚成本低。
