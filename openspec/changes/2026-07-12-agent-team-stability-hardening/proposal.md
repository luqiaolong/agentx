# Proposal: AgentTeam 稳定性硬化（Phase 1 — P0 直击痛点）

## Why

AgentTeam 模式当前存在一个被反复验证的硬故障：375 秒 SSE 断流（trace=`ee346b48ce5a41a7`）。
根因不是网络抖动，而是后端配置形同虚设 + 前端终态不可靠 + 异常路径重复发射事件。

具体地：

1. **配置声明但从不生效**。[backend/app/config/agents.py:109-111](../../../backend/app/config/agents.py#L109-L111) 声明了
   `max_parallel=3` 与 `subtask_timeout=300`，[backend/app/config/settings.py:187-189](../../../backend/app/config/settings.py#L187-L189)
   又镜像了一份，但全仓 grep 在 `backend/app/team/` 下零处读取这两个字段。
   [backend/app/team/orchestrator.py:192-233](../../../backend/app/team/orchestrator.py#L192-L233) 的 `_dispatch_node`
   用 LangGraph `Send` API 做全量 fan-out，既无 `asyncio.Semaphore` 也无 `asyncio.wait_for`；
   [backend/app/team/scheduler.py:186-229](../../../backend/app/team/scheduler.py#L186-L229) `_run_subtask_stream`
   与 [backend/app/team/scheduler.py:232-364](../../../backend/app/team/scheduler.py#L232-L364) `_run_team_role_subtask`
   同样没有任何超时包装。单个子代理卡死一次 LLM 调用，整条 SSE 就会被拖到 375s+。

2. **`team_done` 不是终态事件**。[frontend/renderer/hooks/useChatStream.ts:459-481](../../../frontend/renderer/hooks/useChatStream.ts#L459-L481)
   处理 `team_done` 时**显式不调用** `setSessionRunning(false)`（注释见 476-478 行），把收尾责任推给后续 `done` 事件。
   一旦 `done` 在 SSE 断流恢复路径中丢失，`TeamNodeCard` 永远停在 `running` 状态。
   [frontend/renderer/lib/api/chat.ts:336-418](../../../frontend/renderer/lib/api/chat.ts#L336-L418) `tryRecoverResult`
   在 SSE 断流恢复时只合成 `token` + `done`，从不合成 `team_done`，直接导致团队卡死。

3. **恢复窗口不足 + 无退避**。[chat.ts:347-348](../../../frontend/renderer/lib/api/chat.ts#L347-L348)
   以 5s/3s 间隔轮询两个端点共 120s。对于 375s+ 的团队运行，120s 必然不够；
   且无指数退避，对已过载的后端是雪上加霜。

4. **不活跃超时太短**。[chat.ts:188-191](../../../frontend/renderer/lib/api/chat.ts#L188-L191)
   `INACTIVITY_TIMEOUT=90s`，但单个子代理跑大型 RAG 检索或多分钟 `execute` 命令时，
   完全可能在 >90s 内不产生任何 SSE 事件（注释已承认 `_send_lock` 会阻塞 ping），
   触发误判断流。

5. **错误路径双发 `team_done`**。[backend/app/team/orchestrator.py:620-623](../../../backend/app/team/orchestrator.py#L620-L623)
   `_aggregate_node` 在 except 块里发射 `team_done{status:error}` 后 `raise`；
   [backend/app/scenarios/coding_team/agent.py:88-91](../../../backend/app/scenarios/coding_team/agent.py#L88-L91) 又在外层
   catch 重新发射一次。前端会收到两次 `team_done{status:error}`，造成状态机抖动。

这五个问题构成"配置失效 → 子任务无界 → 流被杀 → 恢复失败 → 卡死"的完整失败链。
Phase 1 不重构架构、不加 UX 特性，**只把这条链路断掉**：让超时/并发限制真正生效、
让终态事件可靠、让错误路径只有一个 owner。

## What Changes

### B1. 强制执行 `max_parallel` 与 `subtask_timeout`

#### B1.1 子任务执行包裹 `asyncio.wait_for`

- [backend/app/team/scheduler.py:186-229](../../../backend/app/team/scheduler.py#L186-L229) `_run_subtask_stream`：
  将 `runner(*runner_args, **runner_kwargs)` 的 `async for event in stream` 整体包裹进
  `asyncio.wait_for(..., timeout=subtask_timeout)`。
- [backend/app/team/scheduler.py:232-364](../../../backend/app/team/scheduler.py#L232-L364) `_run_team_role_subtask`：同样包裹。
- 超时返回 `TeamSubtaskResult(success=False, payload=f"子任务超时（{subtask_timeout}s）")`。
- Phase 1 不引入新的 `team_node_error` 事件（属 Phase 3），改为：
  - 在超时分支发射 `delegation` 事件，`source="team"`，携带超时错误消息，让前端 trace 可见；
  - **或**仅依赖失败 `TeamSubtaskResult` 流入 aggregator，由 `team_done{status:error}` 兜底。
  - 两种方式至少择一实现，本提案默认选择前者（trace 可见性更好）。

#### B1.2 并发 fan-out 用 `asyncio.Semaphore` 限流

- [backend/app/team/orchestrator.py:192-233](../../../backend/app/team/orchestrator.py#L192-L233) `_dispatch_node`
  保留 LangGraph `Send` fan-out（无法替换为顺序派发，会破坏并行语义）。
- 在 per-agent 节点（`_deep_node`/`_code_node`/`_team_role_node`/`_custom_node`/`_builtin_node`）
  内部，调用 runner 前 `async with _team_semaphore:` 获取信号量。
- `_team_semaphore = asyncio.Semaphore(max_parallel)`，在 `run_team_path` 入口按配置创建一次，
  通过 `contextvars.ContextVar` 或 `TeamState["team_semaphore"]` 传递到各节点。
- 默认 `max_parallel=3`，与 [agents.py:109](../../../backend/app/config/agents.py#L109) 默认值对齐。

### B2. `team_done` 成为团队生命周期的真终态

#### B2.1 后端：`team_done` 之后必须紧跟 `done`

- `run_team_path` 在 `team_done` 发射后，**由本函数自身**负责再发射一个 `done` 事件（携带 `team_done` 的
  `status` 与 `agents` 摘要）。前端无需再等"后续 done"。
- 这意味着 [coding_team/agent.py:76-87](../../../backend/app/scenarios/coding_team/agent.py#L76-L87) 的 `async for sse in run_team_path(...)`
  循环结束后不再单独发射 `done`，由 `run_team_path` 内部统一收尾。

#### B2.2 前端：`team_done` 触发 done watchdog

- [useChatStream.ts:459-481](../../../frontend/renderer/hooks/useChatStream.ts#L459-L481) 处理 `team_done` 时，
  保留现有 `upsertTeamNode({finalizeAgents:true, createIfMissing:false})` + `markReasoningDone` + `markRunningToolCallsComplete`。
- **新增** done watchdog：启动一个 2 秒定时器，若 2s 内 `done` 未到达，则强制调用
  `setSessionRunning(threadId, false)` 并把消息标记为完成。若 `done` 在 2s 内到达，取消定时器。
- 这保证即便 `done` 丢失，UI 也最多卡 2s 而非永远卡死。

#### B2.3 前端：`tryRecoverResult` 合成 `team_done`

- [chat.ts:336-418](../../../frontend/renderer/lib/api/chat.ts#L336-L418) `tryRecoverResult`：
  在合成 `token` + `done` 之前，先检查 `/api/observation/runs/{traceId}` 响应中的 `agent_mode` 字段。
- 若 `agent_mode === "team"`（或等价标识），在合成 `token` 之前先合成一个
  `team_done` 事件（`status: "done"`，`agents: []`），让 `TeamNodeCard` 走 finalize 逻辑。
- 合成的 `team_done` 必须遵守 `createIfMissing:false` 语义：若消息中不存在 `team` part（运行可能已在
  `team_init` 之前降级），则跳过合成，仅合成 `token` + `done`。

### B3. 拉长恢复窗口 + 指数退避

- [chat.ts:342-344](../../../frontend/renderer/lib/api/chat.ts#L342-L344) `MAX_WAIT_MS` 从 120s 提升到 300s
  （对齐 `subtask_timeout` 上限 + aggregator 运行时间）。
- 引入指数退避：
  - 前 60s：`/api/chat/result` 5s 间隔，`/api/observation/runs` 3s 间隔（保持现状）。
  - 60s 后：分别提升到 15s / 10s。
  - 上限 30s（避免无限退避）。
- 每次轮询两个端点；任一返回 `status === "completed"` 或 `status === "failed"` 即停止轮询并合成终态事件。

### B4. 不活跃超时拉长到 180s

- [chat.ts:192](../../../frontend/renderer/lib/api/chat.ts#L192) `INACTIVITY_TIMEOUT` 从 90000 提升到 180000。
- 设计文档需明确权衡：更长超时意味着真断流检测变慢，但避免合法长工具调用被误判为断流。
- 180s = `subtask_timeout` 的一半，作为"子任务还在合理执行"的合理上界。

### R8. 统一 `team_done` 错误路径 owner

- 移除 [orchestrator.py:620-623](../../../backend/app/team/orchestrator.py#L620-L623) `_aggregate_node` except 块中的
  `team_done{status:error}` 发射，仅保留 `logger.exception` + `raise`。
- 保留 [coding_team/agent.py:88-91](../../../backend/app/scenarios/coding_team/agent.py#L88-L91) `run_coding_team`
  作为错误路径 `team_done{status:error}` 的**唯一 owner**（外层 try/except 覆盖最广）。
- 在 `_aggregate_node` 的 except 块加注释明确"由 `run_coding_team` 负责 team_done 错误事件"。
- 由于 B2.1 让 `run_team_path` 自身负责 `team_done` 后的 `done`，错误路径同样适用：
  `run_coding_team` 的 except 块在发射 `team_done{status:error}` 后也必须发射 `done` 事件。

## Capabilities

### Modified Capabilities

- `agent-team-stability`：AgentTeam 子任务执行强制受 `max_parallel` / `subtask_timeout` 约束，
  `team_done` 成为团队生命周期真终态，SSE 断流恢复路径合成 `team_done`，
  不活跃超时与恢复窗口对齐团队运行特征，错误路径 `team_done` 仅由单一 owner 发射。

## Impact

- **后端**：
  - 修改 [backend/app/team/scheduler.py](../../../backend/app/team/scheduler.py)（`_run_subtask_stream` / `_run_team_role_subtask` 内部包裹 `wait_for` 超时）
  - 修改 [backend/app/team/orchestrator.py](../../../backend/app/team/orchestrator.py)（per-agent 节点获取信号量；`_aggregate_node` 移除错误路径 `team_done`；`run_team_path` 在 `team_done` 后发射 `done`）
  - 修改 [backend/app/scenarios/coding_team/agent.py](../../../backend/app/scenarios/coding_team/agent.py)（错误路径发射 `team_done` + `done`，作为唯一 owner）
  - **不修改** [backend/app/config/agents.py](../../../backend/app/config/agents.py) 与 [backend/app/config/settings.py](../../../backend/app/config/settings.py)（字段已存在，只是开始被读取）
  - **不引入** 新的 SSE 事件类型（`team_node_done` / `team_node_error` 属 Phase 3）
- **前端**：
  - 修改 [frontend/renderer/hooks/useChatStream.ts](../../../frontend/renderer/hooks/useChatStream.ts)（`team_done` 触发 done watchdog）
  - 修改 [frontend/renderer/lib/api/chat.ts](../../../frontend/renderer/lib/api/chat.ts)（`tryRecoverResult` 合成 `team_done`；恢复窗口 300s + 指数退避；`INACTIVITY_TIMEOUT=180s`）
- **API**：无新增端点。`/api/observation/runs/{traceId}` 响应需确认携带 `agent_mode` 字段（若缺则 Phase 1 顺带补齐）。
- **测试**：
  - 新增 `tests/python/unit/test_team_stability.py`：验证超时、信号量限流、错误路径单 owner。
  - 新增前端测试：`team_done` watchdog、`tryRecoverResult` 合成 `team_done`、指数退避。
- **依赖**：无新增依赖。
- **文档**：更新 [AGENTS.md](../../../AGENTS.md) §9 SSE 事件契约中 `team_done` 的终态语义说明。
- **Phase 边界**：本 Phase 1 **不做** Phase 2（架构清理：合并 5 个重复 node 函数、移除 `_in_progress_tasks`、
  修复 `approval_runner` 异常吞咽、`astream_events` v2→v3）与 Phase 3（UX：`team_node_done`/`team_node_error`
  事件、子代理输出实时流、团队进度条、单子代理取消）的任何工作。

## Rollback Plan

- 每个 B1/B2/B3/B4/R8 子项独立提交，可单独 revert。
- B1.2 信号量方案若引发死锁（如 runner 内部 fork 子任务反向等待信号量），回退为"仅 `wait_for` 超时、
  不限并发"，并在 design.md 记录未决问题转入 Phase 2。
- B2.2 done watchdog 若与既有 `done` 事件产生竞态（如 `done` 在 watchdog 触发瞬间到达），
  回退为仅延长 `INACTIVITY_TIMEOUT`（B4），不引入 watchdog。
- B3 恢复窗口拉长若加剧后端负载（轮询压垮 observation 表），回退为 120s 但保留指数退避。
- R8 单 owner 改动若引入"错误路径无 `team_done`"回归，立即 revert 恢复双发（双发是"抖动"问题，
  无 `team_done` 是"卡死"问题，后者更严重，回退到前者可接受）。
- 全部 Phase 1 改动若引入回归，可整体 revert 该 commit 范围；不破坏 Phase 0 已稳定的单 agent 路径
  （改动集中在 `app/team/` 与团队模式前端分支）。
