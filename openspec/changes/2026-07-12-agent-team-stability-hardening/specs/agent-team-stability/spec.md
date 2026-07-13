# Spec: agent-team-stability

## 概述

`agent-team-stability` 规格定义 AgentTeam 模式（`coding_team` 场景）在 Phase 1 稳定性硬化后的
行为契约：子任务执行受 `max_parallel` / `subtask_timeout` 强制约束、`team_done` 成为团队生命周期
真终态事件（后紧跟 `done`）、SSE 断流恢复路径合成 `team_done`、不活跃超时与恢复窗口对齐团队运行特征、
错误路径 `team_done` 仅由单一 owner（`run_coding_team`）发射。

本规格不覆盖 Phase 2（架构清理）与 Phase 3（UX 增强：`team_node_done` / `team_node_error` 事件、
子代理输出实时流、团队进度条、单子代理取消）。

## MODIFIED Requirements

### Requirement: 子任务执行超时强制

子任务 runner（`_run_subtask_stream` / `_run_team_role_subtask`）必须在配置的 `subtask_timeout`
（默认 300s，来自 [agents.py:111](../../../backend/app/config/agents.py#L111) / [settings.py:189](../../../backend/app/config/settings.py#L189)）
内完成或失败。超时后返回失败 `TeamSubtaskResult` 并发射 `delegation` 事件（Phase 1 复用现有事件，
Phase 3 迁移到 `team_node_error`）。

#### Scenario: 子任务在超时前完成

- **WHEN** 子任务 runner 在 `subtask_timeout`（默认 300s）内正常完成
- **THEN** 返回 `TeamSubtaskResult(success=True, payload=<summary>)`
- **AND** 不发射 `delegation{source:"team",event:"timeout"}` 事件
- **AND** 子任务结果正常流入 `_aggregate_node`

#### Scenario: 子任务超时

- **WHEN** 子任务 runner 在 `subtask_timeout`（默认 300s）内未完成（如 LLM 长生成卡死）
- **THEN** `_run_subtask_stream` / `_run_team_role_subtask` 内部的 `asyncio.wait_for` 抛出 `TimeoutError` 被 scheduler 函数捕获
- **AND** scheduler 函数发射 `delegation` 事件，`source="team"`，`event="timeout"`，`agent=<子任务 agent 名>`，`timeout=<配置值>`
- **AND** scheduler 函数返回 `TeamSubtaskResult(success=False, payload="子任务超时（300s）")`
- **AND** 失败结果流入 `_aggregate_node`，由 aggregator 决定 `team_done` 的 `status`

#### Scenario: 配置覆盖默认超时

- **WHEN** `agents.teams.coding.subtask_timeout` 配置为 600（覆盖默认 300）
- **THEN** `run_team_path` 入口创建的 `subtask_timeout` 为 600
- **AND** 该值通过 `TeamState["subtask_timeout"]` 传递到所有 per-agent 节点
- **AND** 超时分支的 `delegation` 事件 `timeout` 字段为 600

### Requirement: 子任务并发限流

per-agent 节点（`_deep_node` / `_code_node` / `_team_role_node` / `_custom_node` / `_builtin_node`）
在调用 runner 前必须获取 `asyncio.Semaphore(max_parallel)`（默认 3，来自 [agents.py:109](../../../backend/app/config/agents.py#L109)）。
信号量在 `run_team_path` 入口创建一次，通过 `TeamState["team_semaphore"]` 传递。

#### Scenario: 并发数受信号量限制

- **WHEN** 团队计划包含 5 个子任务，`max_parallel=3`
- **THEN** 任意时刻同时执行的子任务数 ≤ 3
- **AND** 其余子任务在 `async with semaphore` 处等待配额
- **AND** 某子任务完成释放信号量后，等待中的子任务立即获取配额开始执行

#### Scenario: 信号量降级（防御性）

- **WHEN** `TeamState["team_semaphore"]` 为 `None`（不应发生，`run_team_path` 总会设置；防御性检查）
- **THEN** per-agent 节点跳过 `async with semaphore`，直接执行 runner
- **AND** 仍保留 `asyncio.wait_for` 超时保护（超时保护不依赖信号量）

#### Scenario: 子任务取消后信号量释放

- **WHEN** 子任务在 `async with semaphore` 期间被取消（如 abort 触发）
- **THEN** `async with` 的 `__aexit__` 释放信号量配额
- **AND** 后续等待的子任务可获取配额继续执行

### Requirement: team_done 后必跟 done

`run_team_path` 在发射 `team_done` 后（无论 `status` 是 `done` 还是 `error`），必须立即发射
`done` 事件。`run_coding_team` 在错误路径发射 `team_done{status:error}` 后也必须立即发射 `done`。
前端 `team_done` 处理时启动 2s done watchdog 兜底。

#### Scenario: 正常路径 team_done 后跟 done

- **WHEN** 团队所有子任务完成（无超时、无异常）
- **THEN** `_aggregate_node` 发射 `team_done{status:"done", agents:[...]}`
- **AND** `run_team_path` 紧跟发射 `done{team_status:"done"}`
- **AND** 前端 `useChatStream` 收到 `team_done` 后启动 2s watchdog
- **AND** `done` 在 2s 内到达，watchdog 被取消
- **AND** `setSessionRunning(threadId, false)` 由 `done` 事件触发（非 watchdog）

#### Scenario: 错误路径 team_done 后跟 done

- **WHEN** 团队执行抛出异常（如 `_aggregate_node` 失败）
- **THEN** 异常传播到 `run_coding_team` 的 except 块
- **AND** `run_coding_team` 发射 `error` 事件 + `team_done{status:"error"}` + `done{team_status:"error"}`
- **AND** `_aggregate_node` except 块**不**发射 `team_done`（仅 `logger.exception` + `raise`）
- **AND** 前端仅收到一次 `team_done{status:"error"}`（无重复）

#### Scenario: done 丢失时 watchdog 兜底

- **WHEN** `team_done` 已到达，但 `done` 因 SSE 物理断流丢失
- **THEN** 前端 2s watchdog 触发
- **AND** `setSessionRunning(threadId, false)` + `setStreaming(false)` 被调用
- **AND** `TeamNodeCard` 在 `team_done` 时已通过 `upsertTeamNode({finalizeAgents:true})` 收尾
- **AND** UI 最多卡 2s 而非永远卡死

### Requirement: tryRecoverResult 合成 team_done

SSE 断流恢复时，`tryRecoverResult` 必须在合成 `token` + `done` 之前，先检查运行是否为团队模式
（通过 `/api/observation/runs/{traceId}` 或 `/api/chat/result/{traceId}` 响应的 `agent_mode` 字段），
若是则合成 `team_done` 事件（遵守 `createIfMissing:false` 语义）。

#### Scenario: 团队模式运行恢复时合成 team_done

- **WHEN** SSE 断流，`tryRecoverResult` 轮询 `/api/observation/runs/{traceId}` 返回 `agent_mode="team"` 且 `ended_at` 非空
- **THEN** 在合成 `token` + `done` 之前，先合成 `team_done{status:"done", agents:[]}`
- **AND** 合成的 `team_done` 触发前端 `TeamNodeCard` finalize 逻辑（`upsertTeamNode({finalizeAgents:true})`）
- **AND** 之后合成 `token`（剩余文本）+ `done`，消息正常收尾

#### Scenario: 非团队模式运行恢复时不合成 team_done

- **WHEN** SSE 断流，`tryRecoverResult` 轮询返回 `agent_mode="deep"`（单 agent 模式）且 `ended_at` 非空
- **THEN** **不**合成 `team_done`
- **AND** 仅合成 `token` + `done`（保持现有行为）

#### Scenario: 团队模式但 team part 不存在时不合成

- **WHEN** `agent_mode="team"`，但前端消息中不存在 `team` part（运行可能在 `team_init` 之前降级，或用户手动删除）
- **THEN** `synthesizeTeamDone` 检查消息中无 `team` part，跳过合成 `team_done`
- **AND** 仅合成 `token` + `done`，消息以非团队形态收尾
- **AND** 不创建空 `team` part（`createIfMissing:false` 语义）

### Requirement: 恢复窗口与指数退避

`tryRecoverResult` 的恢复窗口从 120s 提升到 300s，并在 60s 后切换到更长轮询间隔以减少对过载后端的压力。

#### Scenario: 恢复窗口 300s

- **WHEN** SSE 断流，`tryRecoverResult` 开始轮询
- **THEN** 总轮询窗口为 300s（`MAX_WAIT_MS=300_000`）
- **AND** 300s 内任一端点返回终态（`status="completed"` 或 `"failed"`）即停止轮询
- **AND** 300s 后仍无终态，返回 `false`（恢复失败）

#### Scenario: 前 60s 快速轮询

- **WHEN** 恢复开始的前 60s
- **THEN** `/api/chat/result/{traceId}` 间隔 5s
- **AND** `/api/observation/runs/{traceId}` 间隔 3s
- **AND** 两个端点并行轮询（每次循环都检查）

#### Scenario: 60s 后指数退避

- **WHEN** 恢复已持续超过 60s
- **THEN** `/api/chat/result/{traceId}` 间隔提升到 15s
- **AND** `/api/observation/runs/{traceId}` 间隔提升到 10s
- **AND** 间隔切换基于 `Date.now() - start` 计算，无需额外状态

### Requirement: 不活跃超时 180s

`INACTIVITY_TIMEOUT` 从 90s 提升到 180s（= `subtask_timeout` 的一半），避免合法长工具调用被误判为断流。

#### Scenario: 合法长工具调用不触发误判

- **WHEN** 子代理执行大型 RAG 检索或多分钟 `execute` 命令，>90s 内无 SSE 事件
- **THEN** `INACTIVITY_TIMEOUT=180s` 不触发误判断流
- **AND** 子代理工具调用完成后继续发送 SSE 事件，`lastActivityTime` 被重置
- **AND** 恢复路径不被错误触发

#### Scenario: 真断流仍被检测

- **WHEN** SSE 真断流（后端进程崩溃或网络断开），>180s 无任何数据
- **THEN** `readWithTimeout` 抛出超时
- **AND** `tryRecoverResult` 被触发进入恢复路径
- **AND** 真断流检测比 90s 慢 90s，但避免误判的收益大于检测延迟

### Requirement: team_done 错误路径单一 owner

`_aggregate_node` except 块不再发射 `team_done{status:error}`，仅 `logger.exception` + `raise`。
`run_coding_team` 作为错误路径 `team_done{status:error}` 的唯一 owner。

#### Scenario: _aggregate_node 异常不发射 team_done

- **WHEN** `_aggregate_node` 执行抛出异常
- **THEN** except 块仅执行 `logger.exception("team aggregate_node failed")` + `raise`
- **AND** **不**调用 `writer(make_sse_event("team_done", ...))`
- **AND** 异常传播到 `run_coding_team` 的 except 块

#### Scenario: run_coding_team 作为唯一 owner 发射 team_done

- **WHEN** `run_team_path` 内部任何阶段（`_plan_node` / `_dispatch_node` / per-agent node / `_aggregate_node`）抛出异常
- **THEN** 异常传播到 `run_coding_team` 的 except 块
- **AND** `run_coding_team` 发射 `error` 事件 + `team_done{status:"error"}` + `done{team_status:"error"}`
- **AND** 前端仅收到一次 `team_done{status:"error"}`（无重复，无状态机抖动）

#### Scenario: plan 阶段失败也有 team_done

- **WHEN** `_plan_node` 抛出异常（如 LLM 规划失败）
- **THEN** 异常传播到 `run_coding_team` 的 except 块
- **AND** `run_coding_team` 发射 `team_done{status:"error"}` + `done`（即便 `_aggregate_node` 从未被调用）
- **AND** 前端 `TeamNodeCard`（若 `team_init` 已到达）能正确 finalize
