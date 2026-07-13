# Tasks: AgentTeam 稳定性硬化（Phase 1 — P0 直击痛点）

## 1. 后端：子任务超时与并发限流（B1）

### 1.1 读取 `max_parallel` / `subtask_timeout` 配置

- [ ] T1.1 在 [backend/app/team/orchestrator.py](../../../backend/app/team/orchestrator.py) `run_team_path` 入口添加
  `_resolve_team_settings(settings)` 辅助函数，从 `agents.teams.coding` 或 `settings.agent_team_*` 读取
  `max_parallel`（默认 3）与 `subtask_timeout`（默认 300）
- [ ] T1.2 在 `TeamState` TypedDict 中新增 `team_semaphore: asyncio.Semaphore` 与 `subtask_timeout: int` 字段
  （与现有 `subtask_runners` 运行时字段同序列化排除策略）
- [ ] T1.3 在 `_dispatch_node` 的 `Send` payload 中传递 `team_semaphore` 与 `subtask_timeout`
  （[orchestrator.py:211-231](../../../backend/app/team/orchestrator.py#L211-L231)）

### 1.2 per-agent 节点获取信号量 + scheduler 内部包裹 `asyncio.wait_for`

- [ ] T1.4 在 [backend/app/team/orchestrator.py](../../../backend/app/team/orchestrator.py) `_deep_node` /
  `_code_node` / `_team_role_node` / `_custom_node` / `_builtin_node` 中，调用 runner 前
  `async with semaphore:` 获取配额，再调用 `_run_subtask_stream(...)` / `_run_team_role_subtask(...)`
  （传入 `subtask_timeout`）。在 `_run_subtask_stream` / `_run_team_role_subtask` 内部包裹
  `async for event in stream` 于 `asyncio.wait_for(...)`（timeout 来自传入的 `subtask_timeout`），不在节点调用处包裹
- [ ] T1.5 超时 `asyncio.TimeoutError` 捕获后，发射 `delegation` 事件（`source="team"`, `event="timeout"`）
  并返回 `TeamSubtaskResult(success=False, payload=f"子任务超时（{subtask_timeout}s）")`
- [ ] T1.6 验证 `semaphore` 为 `None` 时（防御性降级）跳过限流，仅保留 `wait_for` 超时

## 2. 后端：终态事件可靠性与错误路径收敛（B2 后端 + R8）

### 2.1 `run_team_path` 在 `team_done` 后紧跟发射 `done`（D4）

- [ ] T2.1 在 [backend/app/team/orchestrator.py:710](../../../backend/app/team/orchestrator.py#L710) 附近，
  `team_done{status:done}` 发射后立即发射 `done` 事件（`data={"team_status":"done"}`）
- [ ] T2.2 在 [backend/app/scenarios/coding_team/agent.py:88-91](../../../backend/app/scenarios/coding_team/agent.py#L88-L91)
  `run_coding_team` 错误路径中，`team_done{status:error}` 发射后立即发射 `done` 事件
- [ ] T2.3 移除 `run_coding_team` 上层（Router 或更外层）对团队路径 `done` 事件的重复发射
  （若有），保证 `done` 仅由 `run_team_path` / `run_coding_team` 发射

### 2.2 统一 `team_done` 错误路径 owner（R8 / D7）

- [ ] T2.4 移除 [backend/app/team/orchestrator.py:620-623](../../../backend/app/team/orchestrator.py#L620-L623)
  `_aggregate_node` except 块中的 `team_done{status:error}` 发射，仅保留 `logger.exception` + `raise`
- [ ] T2.5 在 `_aggregate_node` except 块加注释：`# team_done{status:error} 由 run_coding_team 统一发射`
- [ ] T2.6 验证 `_plan_node` / `_dispatch_node` / per-agent node 失败时，异常能传播到 `run_coding_team`
  的 except 块并被正确发射 `team_done{status:error}` + `done`

## 3. 前端：`team_done` 终态保证与恢复路径（B2 前端 + B3 + B4）

### 3.1 `team_done` 触发 done watchdog（D4）

- [ ] T3.1 在 [frontend/renderer/hooks/useChatStream.ts:459-481](../../../frontend/renderer/hooks/useChatStream.ts#L459-L481)
  `team_done` case 中新增 `doneWatchdogRef`，启动 2s 定时器，到期强制
  `setSessionRunning(threadId, false)` + `setStreaming(false)`
- [ ] T3.2 在 `done` case 中 `clearTimeout(doneWatchdogRef.current)` 取消 watchdog
- [ ] T3.3 验证 watchdog 回调内检查 `isStreaming` 状态，已停止则跳过（防竞态）

### 3.2 `tryRecoverResult` 合成 `team_done`（D5）

- [ ] T3.4 在 [frontend/renderer/lib/api/chat.ts:336-418](../../../frontend/renderer/lib/api/chat.ts#L336-L418)
  `tryRecoverResult` 中，检查 `/api/observation/runs/{traceId}` 与 `/api/chat/result/{traceId}` 响应的
  `agent_mode` 字段，若为 `"team"` 则在合成 `token`+`done` 之前先合成 `team_done`
- [ ] T3.5 新增 `synthesizeTeamDone(conn)` 辅助函数：先检查消息中是否已存在 `team` part
  （`createIfMissing:false` 语义），不存在则跳过合成
- [ ] T3.6 验证后端 `/api/observation/runs/{traceId}` 与 `/api/chat/result/{traceId}` 响应携带
  `agent_mode` 字段；若缺失，在 [backend/app/observation/](../../../backend/app/observation/) 顺带补齐序列化

### 3.3 恢复窗口拉长 + 指数退避（B3 / D6）

- [ ] T3.7 将 [chat.ts:342](../../../frontend/renderer/lib/api/chat.ts#L342) `MAX_WAIT_MS` 从 120_000 提升到 300_000
- [ ] T3.8 重构轮询间隔：前 60s 用 5s/3s，60s 后切到 15s/10s（基于 `Date.now() - start` 计算间隔）
- [ ] T3.9 验证任一端点返回 `status === "completed"` 或 `status === "failed"` 即停止轮询并合成终态

### 3.4 不活跃超时拉长（B4）

- [ ] T3.10 将 [chat.ts:192](../../../frontend/renderer/lib/api/chat.ts#L192) `INACTIVITY_TIMEOUT` 从 90000 提升到 180000
- [ ] T3.11 在代码注释中说明权衡：180s = `subtask_timeout` 一半，避免合法长工具调用误判断流；
  真断流检测变慢是可接受代价

## 4. 测试与验证

### 4.1 后端单元测试

- [ ] T4.1 新增 `tests/python/unit/test_team_stability.py`：
  测试 `asyncio.wait_for` 超时返回失败 `TeamSubtaskResult` + 发射 `delegation` 事件
- [ ] T4.2 测试 `asyncio.Semaphore` 限流：5 个子任务 + `max_parallel=2`，验证同时执行数 ≤2
- [ ] T4.4 测试 `_aggregate_node` 异常时**不**发射 `team_done`，异常传播到 `run_coding_team`，
  后者发射 `team_done{status:error}` + `done`
- [ ] T4.5 测试 `run_team_path` 正常路径发射 `team_done{status:done}` 后紧跟 `done`

### 4.2 前端单元测试

- [ ] T4.6 新增 `team_done` watchdog 测试：`team_done` 到达后 2s 内 `done` 未到，
  验证 `setSessionRunning(false)` 被调用
- [ ] T4.7 新增 watchdog 取消测试：`team_done` 后 `done` 在 2s 内到达，
  验证 `setSessionRunning` 不被 watchdog 重复调用
- [ ] T4.8 新增 `tryRecoverResult` 合成 `team_done` 测试：`agent_mode === "team"` 时合成 `team_done`，
  非 team 模式不合成
- [ ] T4.9 新增 `createIfMissing:false` 测试：消息中无 `team` part 时，即便 `agent_mode === "team"` 也不合成
- [ ] T4.10 新增指数退避测试：验证 60s 前用 5s/3s，60s 后用 15s/10s

### 4.3 端到端冒烟测试

- [ ] T4.11 复现 trace `ee346b48ce5a41a7` 场景：5 子任务 + 1 个卡死子任务（模拟 LLM 长 generation），
  验证 300s 后超时返回失败，`team_done{status:error}` + `done` 正常发射，前端不卡死
- [ ] T4.12 验证 SSE 物理断流（kill 后端进程）后，`tryRecoverResult` 在 300s 内合成
  `team_done` + `token` + `done`，`TeamNodeCard` 正常 finalize
- [ ] T4.13 验证正常团队运行（无超时）端到端流程：`team_init` → 子任务并行 → `team_done{status:done}` → `done`，
  前端 2s watchdog 被取消

## 5. 文档与收尾

- [ ] T5.1 更新 [AGENTS.md](../../../AGENTS.md) §9 SSE 事件契约：`team_done` 后必跟 `done` 的终态语义
- [ ] T5.2 在 [backend/app/team/orchestrator.py](../../../backend/app/team/orchestrator.py) 顶部模块 docstring 中
  说明 `max_parallel` / `subtask_timeout` 的强制执行机制与信号量传递路径
- [ ] T5.3 在 [backend/app/team/scheduler.py](../../../backend/app/team/scheduler.py) `_run_subtask_stream` docstring 中
  说明 `wait_for` 超时行为（abort 响应优化推迟到 Phase 2，不在 Phase 1 范围）
- [ ] T5.4 验证 Phase 1 改动**未触及** Phase 2（架构清理）与 Phase 3（UX 增强）范围：
  无新增 SSE 事件类型、无合并重复 node 函数、无移除 `_in_progress_tasks`、无 `team_node_done`/`team_node_error`
- [ ] T5.5 运行 `gitnexus_detect_changes()` 验证改动范围仅限 `app/team/` + `app/scenarios/coding_team/` +
  `frontend/renderer/hooks/useChatStream.ts` + `frontend/renderer/lib/api/chat.ts` + 测试文件
