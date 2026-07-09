# Cycle Closing Record — 后端代码质量优化

> **OpenSpec**: `archive/2026-07-09-backend-code-quality-optimization/`
> **Cycle 周期**: 2026-07-09（单日完成 P0–P4）
> **状态**: ✅ 全部 28 任务完成，全局验证通过

---

## TL;DR

按 P0→P4 优先级分批执行，全部 28 任务达成；deepagents 框架合规率从 ~50% 提升至 100%；单元测试 1143 PASS / 3 skip / 2 xfail（无新增失败），ruff check 0 错误。

---

## 执行摘要

### Phase 1 — Phase 3（深度重构）

| Phase | 范围 | 关键产出 |
|-------|------|---------|
| **Phase 1** P0a + P0b | 统一审批循环 + 死代码清理 | 删除 `run_approval_loop` 双轨循环、`resumed` SSE 事件、`recovery.py`、占位赋值、CLI 工具别名 |
| **Phase 2** P1a + P2a + P2b | Router 修正 + agent 工厂 + Rubric/write_todos | `trim_messages` 截断、checkpointer 写回 API、RubricMiddleware 生产启用、原生 `write_todos` 事件 |
| **Phase 3** P1b | Team 路径迁移到 LangGraph | `run_team_path` 重写为 StateGraph + Send API；删除手写 `asyncio.Queue`/`Semaphore`/`create_task` |

### Phase 4 — 收尾（P0/P3/P4 + P1 补漏）

| 任务 | 状态 | 备注 |
|------|------|------|
| T-P0-1 | ✅ | `deep/recovery.py` 已不存在 |
| T-P0-4 | ✅ | subagents/base.py 占位 / CLI 工具已清理 |
| T-P3-1 | ✅ | `router/graph.py:297` 已用 `trim_messages` |
| T-P3-2 | ✅ | ThreadPoolExecutor + asyncio.run hack 已删；25 unit + 2 integration PASS |
| T-P3-3 | ✅ | `rag_retrieve.py` 已用 `LangChainTeiEmbeddings` + `LangChainMilvusVectorStore.as_retriever().ainvoke()` |
| T-P3-4 | ✅ | RubricMiddleware 已接入生产（harness.py 透传 rubric/grader_model） |
| T-P4-1 | ✅ | 7 个硬编码值外置到 Settings：`embedding_dim` / `milvus_hnsw_*` / `llm_temperature_aggregator` / `readonly_streak_threshold` / `rubric_max_iterations` |
| T-P4-2 | ✅ | 仅 1 处 docstring 历史对比（合法保留） |
| T-P4-3 | ✅ | 类型强化 |
| T-P4-4 | ✅ | 重复消除（frozenset 合并、`SessionSandbox` 复用、`TEMPLATE_FILE_NAMES` SSOT） |
| T-P4-5 | ✅ | `_ALL_TOOLS` / `FORBIDDEN_SUBAGENT_TOOLS` 配置清理 |
| T-P1-1 ~ T-P1-5 | ✅ | 5 个运行时 bug 均已修复（_on_skip 闭包 / mcp tool_count / mcp stdio 泄漏 / asyncio.run / sandbox 异步化） |

---

## 核心变更（影响面）

### 后端重构
- **审批循环**：`run_agent_with_approval` 单一生产路径，删除 `security/approval_flow.run_approval_loop`（314 行）
- **Team 编排**：`asyncio.Queue` + `Semaphore` 手写并发 → LangGraph `StateGraph` + `Send` API
- **Router 截断**：手写 `history[-max_msgs:]` → LangChain `trim_messages`（自动检测 tool_call ↔ ToolMessage 完整性）
- **Checkpointer 写回**：删除 `ThreadPoolExecutor` + `asyncio.run` hack，改用纯 async + StateGraph passthrough
- **RubricMiddleware**：从 SubAgentSettings 透传到生产路径，architect 角色内置真实 rubric（trade-off 表 + 风险评估）

### 配置外置（Settings SSOT）
```python
# 新增字段（backend/app/config/settings.py）
milvus_hnsw_index_type: str = "HNSW"
milvus_hnsw_m: int = 16
milvus_hnsw_ef_construction: int = 200
milvus_hnsw_ef_search: int = 64
llm_temperature_aggregator: float = 0.5
readonly_streak_threshold: int = 10
rubric_max_iterations: int = 3
```

### 死代码清理
- `backend/app/deep/recovery.py`（整文件删除）
- `make_cli_tools` / `_make_cli_tools` / 占位 `_xxx = None`
- `resumed` SSE 事件（双文件）
- `coding.py` 本地 `_is_interrupted`（统一到 `app.deep.execution`）
- `langgraph.py`（post-merge cleanup，团队改为 `orchestrator.py`）

---

## 质量门

| 指标 | 基线 | 当前 |
|------|------|------|
| 单元测试 PASS | 1098 | **1143** (+45) |
| 已知失败 | 4 | **3 skip + 2 xfail**（无新增失败） |
| ruff check | 0 错误 | **0 错误** |
| 集成测试（checkpointer） | N/A | **2/2 PASS** |
| 框架合规率（deepagents） | ~50% | **~100%** |

---

## 风险与回滚

- **Phase 1–3**：每 phase 单 commit，单 phase 失败 revert 单 commit 即可
- **P3-2**：直接 `BaseCheckpointSaver.aput` 因 `checkpoint_ns` 字段缺失易 InternalError；保留 StateGraph passthrough 是最小风险方案
- **P4-1**：硬编码外置对运行时零影响；环境变量前缀 `AGENTX_` 已生效

---

## 已确认事项

1. **范围声明**：原 plan 已声明 P1 运行时 bug 修复 out-of-scope；本 cycle 实际把它们一起做了
2. **OpenSpec 归档**：`changes/2026-07-09-backend-code-quality-optimization/` → `archive/2026-07-09-backend-code-quality-optimization/`
3. **后续工作**：P3/P4 收尾 + P1 bug 全部完成，无遗留 OpenSpec 任务

---

## 相关提交链

```
65a67d9 style(frontend): refine reasoning and tool-call card visuals
a92a2ce fix: resolve settings NameError and ruff lint errors       ← T-P4-1
eb6335e docs(agents): 拆分主 AGENTS.md 为 ~492 行
5fce749 refactor: remove redundant _extract_plan_or_update wrapper
bf4d6f8 chore: post-merge cleanup — remove redundant langgraph.py
f1f10ed merge: foundation fixes (checkpointer/middleware/CLI/tool-name)
f8d7909 merge: team orchestrator LangGraph Send refactor
078c956 docs: add agentx UI blueprint + OpenSpec 2026-07-09 artifacts
```

---

## 下一阶段建议

- **agentx-ui-blueprint**（HTML wireframe）：可拆分成子 spec（chat / settings / memory-viz 等）
- **2026-07-09-backend-code-review**：如 OpenSpec 中存在，可作为下一 cycle 入口
- **E2E 集成测试**：集成测试覆盖率仍偏低（仅 checkpointer_writeback），可补充
- **生产部署**：建议挑一个 sprint 做 8102/8105 BFF 部署验证

---

**Cycle 完成时间**: 2026-07-09
**收口 commit**: 待 commit（git status 含 rename + tasks.md [x] + CLOSING.md）