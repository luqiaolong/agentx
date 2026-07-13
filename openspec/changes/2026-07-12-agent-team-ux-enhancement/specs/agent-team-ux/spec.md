# Spec: agent-team-ux

> 本 spec 定义 AgentTeam 第三阶段（体验提升）的能力契约。所有 Requirement 均在
> Phase 2 统一的 `_run_subtask_node` 之上叠加。新 SSE 事件遵循 AGENTS.md §13：
> 修改事件必须同步 `api/chat.py` + `lib/api/chat.ts` + `useChatStream.ts` +
> `shared/api-types.ts` 四处。

## ADDED Requirements

### Requirement: 子代理输出实时流式（team_agent_token）

系统 SHALL 在子代理执行期间，通过新 SSE 事件 `team_agent_token` 实时推送每个子代理
的增量文本，使前端 `AgentRow` 在 `team_done` 之前即可显示子代理输出。事件 payload
为 `{agent: string, chunk: string, trace_id?: string}`，发射点为
`backend/app/team/scheduler.py::_route_event_for_node` —— 当路由子代理 runner 的
`token` 事件时，附带 agent 名称额外发射该事件（原 `token` 事件仍转发，保证主消息流
不受影响）。

#### Scenario: 子代理执行中实时显示输出

- **WHEN** 一个 Team 子代理（如 `rag`）正在执行并产出 token
- **THEN** 后端通过 `team_agent_token` 事件推送 `{agent: "rag", chunk: "<text>"}`
- **AND** 前端 `useChatStream` 收到后调用 `upsertTeamNode({agentUpdate: {agent: "rag", patch: {message: (existing||"") + chunk}}})`
- **AND** `TeamNodeCard.AgentRow` 实时显示 `agent.message`，无需等 `team_done`

#### Scenario: team_done 回填覆盖流式 raw token

- **WHEN** `team_done` 到达，携带 `agentMessages` 含某 agent 的最终 summary
- **THEN** 前端用 summary 覆盖该 agent 的 `message` 字段（流式 raw token 是未压缩原文，summary 是浓缩版）
- **AND** 已通过 `team_agent_token` 流式显示的 raw token 被替换为最终 summary，不残留重复

#### Scenario: 子代理无 token 输出时不发空事件

- **WHEN** 子代理 runner 未产出任何 `token` 事件（如纯工具调用型 agent）
- **THEN** 后端不发射任何 `team_agent_token` 事件
- **AND** 前端 `AgentRow.message` 保持空，直到 `team_node_done` 携带 summary 到达

---

### Requirement: 单 agent 完成事件（team_node_done）

系统 SHALL 在单个子代理成功完成时，发射 `team_node_done` 事件，payload 为
`{agent: string, success: true, summary: string, trace_id?: string}`。发射点为
`_run_subtask_node`：runner 成功返回后、state update 返回之前。前端据此即时将该
agent 置为 `done`，无需等待 `team_done` 批量收尾。

#### Scenario: 单 agent 完成即时标记

- **WHEN** Team 中 `backend_dev` 子代理 runner 成功完成
- **THEN** 后端在返回 state update 前发射 `team_node_done{agent:"backend_dev", success:true, summary:"<findings>"}`
- **AND** 前端 `useChatStream` 调用 `upsertTeamNode({agentUpdate:{agent:"backend_dev", patch:{status:"done", finishedAt:Date.now(), summary}}})`
- **AND** `TeamNodeCard` 中该 `AgentRow` 立即变为完成态（绿色对勾），其他 agent 不受影响

#### Scenario: team_done 仍正常收尾

- **WHEN** 所有子代理已分别通过 `team_node_done`/`team_node_error` 标记完成
- **THEN** `team_done` 仍照常发射（Phase 1 已定为前端终止事件）
- **AND** 前端 `team_done` handler 的 `finalizeAgents` 不再改变已 done 的 agent（仅兜底未收尾的）
- **AND** `agentMessages` 仍回填最终 summary（覆盖流式 raw token）

---

### Requirement: 单 agent 失败事件（team_node_error）

系统 SHALL 在单个子代理失败时，发射 `team_node_error` 事件，payload 为
`{agent: string, error: string, trace_id?: string}`。发射点为 `_run_subtask_node`：
runner 失败后、state update 返回之前。单个 agent 失败不再被静默吞掉。

#### Scenario: 单 agent 失败可见

- **WHEN** Team 中 `tester` 子代理 runner 抛异常或返回失败
- **THEN** 后端发射 `team_node_error{agent:"tester", error:"<原因>"}`
- **AND** 前端 `useChatStream` 调用 `upsertTeamNode({agentUpdate:{agent:"tester", patch:{status:"error", finishedAt:Date.now(), message:error}}})`
- **AND** `TeamNodeCard` 中该 `AgentRow` 立即变为失败态（红色），显示错误原因
- **AND** 其他 agent 继续执行，不被该失败中断

#### Scenario: 失败 agent 不阻塞 team_done

- **WHEN** 一个 agent 已通过 `team_node_error` 标记失败，其余 agent 完成
- **THEN** `team_done` 以 `status:"error"` 收尾（因 `errors` 非空）
- **AND** 失败 agent 的最终状态保持 `error`，不被 `finalizeAgents` 改写

---

### Requirement: 超时仅发 team_node_error 不发 delegation

Phase 1 在子任务超时分支发射过渡性 `delegation{source:"team", event:"timeout"}` 事件。
Phase 3 引入 `team_node_error` 后，系统 SHALL 移除该 `delegation` 超时事件发射，超时
统一由 `team_node_error` 覆盖，避免超时同时发射两个事件造成前端重复处理。

#### Scenario: 子任务超时仅发 team_node_error

- **WHEN** Team 中某子代理因 `subtask_timeout` 超时（`asyncio.wait_for` 抛 `TimeoutError`）
- **THEN** 后端发射 `team_node_error{agent, error:"子任务超时（Ns）"}`（N 为 `subtask_timeout` 值）
- **AND** 后端**不**发射 `delegation{source:"team", event:"timeout"}` 事件
- **AND** 前端 `useChatStream` 的 `team_node_error` handler 将该 agent 置为 `error`，显示"子任务超时（Ns）"
- **AND** 前端 `delegation` handler 不再收到 timeout 事件（迁移完成）

---

### Requirement: 子代理单独取消

系统 SHALL 提供端点 `POST /api/chat/team/agent/abort`，接收 `{thread_id, agent_name}`，
仅取消指定 thread 下的指定子代理，不影响同 thread 其他子代理。后端通过模块级
`_agent_abort_flags: dict[str, set[str]]` 记录待取消 agent，`_run_subtask_node` 在
检查全局 `abort_event` 之外额外检查该标志。

#### Scenario: 取消单个 agent 不影响其他

- **WHEN** 用户在 `TeamNodeCard` 中点击 `rag` agent 行的「×」按钮（该 agent 状态为 running）
- **THEN** 前端调用 `POST /api/chat/team/agent/abort` body `{thread_id, agent_name:"rag"}`
- **AND** 后端把 `"rag"` 加入 `_agent_abort_flags[thread_id]`
- **AND** `_run_subtask_node` 在下一轮迭代检查命中，返回 `TeamSubtaskResult(success=False, payload="用户取消")`
- **AND** 该 agent 通过 `team_node_error` 标记为 `error`（message="用户取消"）
- **AND** 同 thread 的 `backend_dev`、`tester` agent 继续执行并完成

#### Scenario: abort flag 命中后清理

- **WHEN** 某 agent 的 abort flag 被 `_run_subtask_node` 检查命中并返回"用户取消"
- **THEN** 后端从 `_agent_abort_flags[thread_id]` 中 `discard` 该 agent 名
- **AND** 同 thread 后续若再次启动同名 agent，不会被误取消

#### Scenario: thread 复用时清理 abort flag

- **WHEN** `team_done` 到达或用户 `/reset` 当前 thread
- **THEN** 后端清空 `_agent_abort_flags[thread_id]` 整个集合
- **AND** 同 thread_id 下一轮 Team 运行不会被上一轮残留 flag 误取消

---

### Requirement: 团队进度条与百分比

`TeamNodeCard` SHALL 在 `status === "running"` 时于卡片头部显示团队进度条，聚合
展示已完成 agent 数量与百分比。

#### Scenario: 运行中显示进度

- **WHEN** Team 处于 running 状态，agents 数组为 5，其中 2 个 done、1 个 error
- **THEN** 头部显示 `${3}/${5}` 与 `60%`
- **AND** 进度条以 60% 填充，其中 error 段以红色尾巴区分
- **AND** 进度仅统计当前 plan 的 agents，不计入 `supersededPlans`

#### Scenario: 完成后隐藏进度条

- **WHEN** Team 状态切换为 `done` 或 `error`
- **THEN** 进度条隐藏（避免噪声）
- **AND** `TeamNodeCard` 自动折叠

---

### Requirement: 重规划可视化（supersededPlans）

当 `upsertTeamNode` 收到 `initialAgents` 且 team part 已存在时，系统 SHALL NOT
静默替换 `agents` 数组，而是把旧 `agents` 推入新增的 `supersededPlans` 字段，并在
`TeamNodeCard` 中以折叠的「Previous Plan」区渲染。

#### Scenario: 重规划保留旧 plan

- **WHEN** Team 运行中触发重新规划，后端发新 `team_init` 携带新 `initialAgents`
- **THEN** 前端把旧 `agents` 推入 `supersededPlans`（追加，不覆盖）
- **AND** 新 `agents` 成为当前 plan
- **AND** `TeamNodeCard` 在当前 plan 上方渲染「Previous Plan (N)」折叠区，旧 plan 每个 agent 打「已重新规划」灰色 badge，dimmed 展示
- **AND** 进度条仅计算当前 plan，不计入 supersededPlans

#### Scenario: 多次重规划累积

- **WHEN** Team 连续触发 2 次重规划
- **THEN** `supersededPlans` 累积 2 个旧 plan
- **AND** 「Previous Plan」区显示编号 1、2，均可独立展开
- **AND** 超过最近 3 个时丢弃最旧的（上限保护）

#### Scenario: 用户查看旧 plan 不影响当前

- **WHEN** 用户展开「Previous Plan」区查看旧 plan 的 agent 列表
- **THEN** 旧 plan agent 仅只读展示，不显示取消按钮（已不可取消）
- **AND** 当前 plan 的运行、进度、取消按钮不受影响

---

### Requirement: Planner 规划期流式反馈

系统 SHALL 把 Team Planner 的 LLM 调用从 `streaming=False` + `ainvoke` 改为
`streaming=True` + `astream`，规划过程中以 `reasoning` 事件（`source="team_planner"`）
增量输出，消除 5-15s 规划静默期。

#### Scenario: 规划期可见 reasoning

- **WHEN** 用户发送触发 Team 的消息，Planner 开始规划
- **THEN** 后端用 `llm.astream(...)` 流式调用，每 chunk 发 `reasoning{content, source:"team_planner"}`
- **AND** 前端在 `team_init` 之前即可见"Planning..."流式文本
- **AND** 规划期不再是无反馈的静默

#### Scenario: 流式后解析仍正确

- **WHEN** Planner astream 流结束
- **THEN** 后端拼接全文，按现有逻辑解析 `[agent:xxx]` 行生成 plan
- **AND** `team_init` 事件携带的 plan 与旧 `ainvoke` 路径输出一致
- **AND** 后续 `_run_subtask_node` 调度不受影响

---

### Requirement: team_done 与 Aggregator 解耦

系统 SHALL 把 `team_done` 发射移到 `_run_aggregator` 调用之前 —— 所有子任务 summary
已就绪即发 `team_done`，Aggregator 的 LLM 延迟不再阻塞 Team 生命周期收尾。`done`
终止事件仍由 chat.py 在 Aggregator 流结束后发射。

#### Scenario: team_done 先于 aggregator 最终答案

- **WHEN** 所有子任务完成，进入 `_aggregate_node`
- **THEN** 后端先发 `team_done{status:"done", agents: agent_summaries}` 收口 Team 生命周期
- **AND** 前端 `TeamNodeCard` 立即变为完成态，显示各 agent summary
- **AND** 随后 Aggregator 流式 `token` 事件输出最终综合答案
- **AND** Aggregator 完成后 chat.py 发 `done` 终止事件

#### Scenario: Aggregator 失败不影响 Team 收尾

- **WHEN** `team_done` 已发射，但 Aggregator LLM 调用失败
- **THEN** 前端 `TeamNodeCard` 已显示完成态（子任务 summary 完整）
- **AND** Aggregator 失败发 `error` 事件，前端显示综合答案生成失败
- **AND** Team 子任务结果不丢失（已通过 `team_done` 回传）

---

### Requirement: Phase 1 watchdog 在 aggregator 流式期间不误触发

由于 Phase 3 D7 把 `team_done` 提前到 aggregator LLM 流之前，Phase 1 D4 的 2s
done-watchdog（`team_done` 后 2s 未收到 `done` 即 `setSessionRunning(false)`）会
在 aggregator 流式期间误触发。系统 SHALL 调整 watchdog arm 时机：不在 `team_done`
上 arm，而是在 aggregator 第一个 `token` 到达时 arm，每个后续 `token` 重置 2s 计时器；
若 `team_done` 后 30s 内无任何 `token`，watchdog 兜底触发。

#### Scenario: team_done 已发且 aggregator 持续流式 token，watchdog 不触发

- **WHEN** `team_done` 已发射，aggregator 随后持续产出 `token` 事件（token 间隔 <2s）
- **THEN** 2s watchdog 在每个 `token` 到达时被重置，不触发 `setSessionRunning(false)`
- **AND** 前端持续接收 aggregator 综合答案 token，会话保持 running
- **AND** watchdog 不在 `team_done` 上 arm（aggregator 流未开始前不计时）

#### Scenario: team_done 后 30s 内无 token，watchdog 触发

- **WHEN** `team_done` 已发射，但 30s 内未收到任何 aggregator `token` 事件（aggregator LLM hung）
- **THEN** 前端 watchdog 触发 `setSessionRunning(false)`，标记 aggregator 超时
- **AND** 用户可见"综合答案生成超时"提示
- **AND** Team 子任务结果不丢失（已通过 `team_done` 回传）

#### Scenario: aggregator 流结束且 done 收到，正常关流

- **WHEN** aggregator 流正常结束，chat.py 发射 `done` 终止事件
- **THEN** 前端正常关闭 SSE 流，清除 watchdog 计时器
- **AND** `setSessionRunning(false)` 由 `done` 事件触发（非 watchdog）
- **AND** 会话状态正常置为完成

---

### Requirement: TeamNodeCard 智能展开/折叠

`TeamNodeCard` SHALL 根据团队状态自动管理展开状态：`running` 自动展开、`done|error`
自动折叠，并保留「全部展开 / 全部折叠」开关。

#### Scenario: 运行期自动展开

- **WHEN** Team 进入 running 状态
- **THEN** `TeamNodeCard` 自动展开，显示所有 agent 行与进度条
- **AND** 用户无需手动点击即可观察执行过程

#### Scenario: 完成后自动折叠

- **WHEN** Team 状态切换为 `done` 或 `error`
- **THEN** `TeamNodeCard` 自动折叠，仅保留头部摘要
- **AND** 用户可手动展开查看详情

#### Scenario: 全部展开开关

- **WHEN** 用户点击卡片头部「全部展开」开关
- **THEN** 当前 plan 的所有 `AgentRow` 展开（含子代理输出详情）
- **AND** 再次点击「全部折叠」收起所有 agent 行详情

---

### Requirement: 前端渲染性能优化

系统 SHALL 通过三项优化消除长 Team 运行的 O(n²) 渲染：分层 memo、`agents` 引用稳定、
自动滚动粘性。

#### Scenario: 分层 memo 避免每 token 重跑分组

- **WHEN** 一个 Team 运行产出大量 token，消息 parts 数组每个 token 都变引用
- **THEN** `useToolCallItems` 仅在 tool-call/result part ID 签名变化时重跑昂贵分组逻辑
- **AND** `useTextStream` 只处理尾部 text/reasoning，每 token 重跑但廉价
- **AND** 长运行（>10k token）渲染耗时接近 O(n) 而非 O(n²)

#### Scenario: agents 引用稳定保 memo 有效

- **WHEN** `upsertTeamNode` 收到一个 `agentUpdate`，其 patch 字段值与该 agent 现有值全等
- **THEN** 返回的 `agents` 数组与原引用相同（不产新引用）
- **AND** `TeamNodeCard.memo` 的 `areEqual` 判定相等，跳过重渲
- **AND** 仅当至少一个 agent 的可见状态（status/message/summary）实际变化时，`agents` 引用才变

#### Scenario: 用户上滑暂停自动滚动

- **WHEN** Team 运行中用户向上滚动查看历史（距底 >50px）
- **THEN** 自动滚动暂停（stickyRef=false），新内容到达不把视图拽回底部
- **AND** 显示「↓ 新消息」回到底部指示按钮
- **AND** 用户点击该按钮或滚回底部后，自动滚动恢复

---

## MODIFIED Requirements

### Requirement: SSE 事件契约（agent-team 扩展）

原 `sse-event-contract`（`api-types.ts:32-100`）的 ChatEvent union SHALL 新增三个分支
并调整 `team_done` 语义。

#### Scenario: 新增三个事件类型

- **WHEN** 后端发射 `team_agent_token` / `team_node_done` / `team_node_error`
- **THEN** `shared/api-types.ts` 的 ChatEvent union 包含对应分支（含 `trace_id?`）
- **AND** `useChatStream.ts` 有对应 handler 处理
- **AND** `lib/api/chat.ts` 的 SSE 解析不报未知事件

#### Scenario: team_done 语义调整

- **WHEN** `_aggregate_node` 执行
- **THEN** `team_done` 在 `_run_aggregator` 之前发射（所有子任务 summary 已就绪）
- **AND** `team_done` 不再因 Aggregator LLM 延迟而推迟
- **AND** `docs/agents/02-sse-event-contract.md` 注明此调整

#### Scenario: 未知事件仍被忽略

- **WHEN** 前端收到未识别的事件类型（向前兼容兜底）
- **THEN** `useChatStream` 的 `default` 分支忽略，不破坏流式
- **AND** 回滚阶段移除新 handler 后，三事件落入 default 被忽略，无破坏性

---

### Requirement: 角色 label 单一来源

原 `DelegationCard.SUBAGENT_META` / `TaskTimeline.AGENT_ROLE_LABELS` /
`TodoProgress.formatTaskLabel` / `AssistantMessageParts.normalizeAgentRole` 四处重复
映射 SHALL 统一到 `frontend/renderer/lib/agentRoles.ts`。

#### Scenario: 四处改用单一来源

- **WHEN** 开发者修改某角色的 label/icon/description
- **THEN** 仅需修改 `agentRoles.ts::AGENT_ROLE_META` 一处
- **AND** `DelegationCard` / `TaskTimeline` / `TodoProgress` / `AssistantMessageParts` 全部通过 `getRoleMeta(role)` 获取
- **AND** 不存在重复的角色映射常量

#### Scenario: 死代码清理

- **WHEN** `agentRoles.ts` 单一来源就位
- **THEN** `TodoProgress.formatTaskLabel`（`TodoProgress.tsx:22-23` 自述未用）被删除
- **AND** `frontend/renderer/stores/chat/messageOps.ts`（已被 `messageIndex.ts` 取代）被删除
- **AND** 删除前全局 grep 确认无 import 引用，`pnpm typecheck` 通过

---

### Requirement: 文档与现状一致

`docs/agents/03-key-conventions.md §14.5` SHALL 删除已移除的 `run_chat_path` fallback
描述；`docs/agents/02-sse-event-contract.md` SHALL 补录三个新事件与 `team_done` 语义调整。

#### Scenario: 删除陈旧 run_chat_path 描述

- **WHEN** 开发者查阅 `03-key-conventions.md §14.5`
- **THEN** 不再出现 `run_chat_path` fallback 描述
- **AND** 文档与 `agent-architecture-refactor` spec（team 不回退 legacy CHAT 路径）一致

#### Scenario: SSE 契约文档补全

- **WHEN** 开发者查阅 `02-sse-event-contract.md`
- **THEN** 文档列出 `team_agent_token` / `team_node_done` / `team_node_error` 三事件的 payload 字段与发射时机
- **AND** 注明 `team_done` 提前到 aggregator 之前的语义调整
