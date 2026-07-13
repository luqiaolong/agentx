# Proposal: Agent Team 第三阶段 — 体验提升（UX Enhancement）

> 本变更是 AgentTeam 优化系列的第三阶段（Phase 3）。Phase 1（稳定性加固）与
> Phase 2（架构清理）已完成，本阶段在 Phase 2 清理后的统一架构之上，集中解决
> 深度分析中识别出的前端体验、子代理可见性、性能与文档陈旧问题。

## Why

AgentTeam 当前能跑通「Orchestrator 拆任务 → 并行子代理 → Aggregator 汇总」主链路，
但用户在长时间 Team 运行过程中体验割裂，主要体现在五类痛点：

1. **子代理执行过程不可见**：`TeamNodeCard` 默认折叠（`TeamNodeCard.tsx:87`
   `useState(false)`），运行期间用户只看到 "Agent Team · 执行中" + spinner，看不到
   任何 agent 行；子代理输出仅在 `team_done` 时通过 `agentMessages` 一次性回填
   （`useChatStream.ts:463-475`），执行中 `AgentRow` 是空的。用户无法判断"卡住了"
   还是"在跑"。

2. **缺乏细粒度生命周期事件**：`api-types.ts:32-100` 没有 `team_node_done` /
   `team_node_error` 事件，单个子代理完成/失败对用户不可见，只能等 `team_done`
   批量收尾。单个 agent 失败被静默吞掉。

3. **无法单独取消子代理**：`lib/api/chat.ts:443-454` 的 `abort(threadId)` 是
   线程级，用户只能中止整条流，无法"停掉某个跑偏的 agent，让其他继续"。

4. **LLM 阻塞导致前端假死**：Planner `streaming=False`
   （`orchestrator.py:122`）规划期 5-15s 无任何输出；Aggregator 在 `team_done`
   之前才跑（`orchestrator.py:597-612`），LLM 慢/挂时 `TeamNodeCard` 卡在
   "执行中"。两者都把 LLM 延迟耦合进了 Team 生命周期。

5. **前端 O(n²) 渲染 + 引用不稳定**：`AssistantMessageParts.tsx:356` 的
   `useMemo([message.parts])` 每个 token 都重跑分组逻辑；`upsertTeamNode`
   （`index.ts:902`）每次 `agentUpdate` 都 `newAgents.map(...)` 产生新数组引用，
   击穿 `TeamNodeCard.areEqual`（`TeamNodeCard.tsx:141-149`）；`useAutoScroll`
   无"用户上滑暂停"守卫，长 Team 运行时用户无法上翻查看。

此外还有三类工程债：重规划静默替换 `agents` 数组（`index.ts:894-895`）、
角色 label 映射三处重复（`DelegationCard` / `TaskTimeline` / `TodoProgress`）、
文档 `03-key-conventions.md §14.5` 仍提及已删除的 `run_chat_path` fallback。

## What Changes

### 后端：新增 3 个 SSE 事件 + 子代理取消端点 + Planner/Aggregator 解耦

- 新增 SSE 事件 `team_agent_token`（携带 `{agent, chunk}`）：在
  `backend/app/team/scheduler.py::_route_event_for_node` 路由子代理 runner 的
  `token` 事件时，附带 agent 名称额外发射，实现子代理输出实时流式。
- 新增 SSE 事件 `team_node_done`（`{agent, success, summary}`）与
  `team_node_error`（`{agent, error}`）：在 Phase 2 统一出的
  `_run_subtask_node` 中，runner 完成后、返回 state update 之前发射，标记
  单个 agent 生命周期终点。
- 新增端点 `POST /api/chat/team/agent/abort`，接收 `{thread_id, agent_name}`，
  写入 per-agent abort 标志 `_agent_abort_flags: dict[str, set[str]]`；
  `_run_subtask_node` 在检查全局 `abort_event` 之外额外检查该标志，命中则返回
  `TeamSubtaskResult(success=False, payload="用户取消")`，其他 agent 继续。
- Planner 切换 `streaming=True` + `llm.astream(...)`，规划过程中以
  `reasoning` 事件（`source="team_planner"`）增量输出，流结束后再解析
  `[agent:xxx]` 行；消除规划期 5-15s 静默。
- `team_done` 提前到 Aggregator 之前发射（所有子任务 summary 已就绪即可发，
  `done` 终止事件由 chat.py 在 Aggregator 流结束后再发），解耦 Team 生命周期
  与 Aggregator LLM 延迟。

### 前端：卡片可见性、进度、取消按钮、性能、重规划可视化

- `TeamNodeCard`：`status === "running"` 自动展开、`done`/`error` 自动折叠；
  卡片头部新增「全部展开 / 全部折叠」开关；头部新增进度条
  `${doneCount}/${agents.length}` + 百分比。
- `SubAgentGroup`（`AssistantMessageParts.tsx:255`）：最新委派且 status 为
  running 时自动展开；被更新的委派取代或 `team_done` 到达后自动折叠。
- `useChatStream.ts`：新增 `team_agent_token` / `team_node_done` /
  `team_node_error` 三个 handler，分别增量追加子代理 message、标记单个
  agent done/error。
- `AgentRow`：`status === "running"` 时显示「×」取消按钮，点击调新端点。
- `upsertTeamNode`（`index.ts:902`）：`agentUpdate` 改为浅结构比较，patch 字段
  值未变时返回同一 `agents` 数组引用，恢复 `TeamNodeCard.memo` 有效性；
  `initialAgents` 在已有 team part 时不静默替换，而是把旧 `agents` 推入新增的
  `supersededPlans: TeamAgentState[][]` 字段，`TeamNodeCard` 以「Previous Plan」
  折叠区渲染并打「已重新规划」标记。
- `AssistantMessageParts`：拆成 `useToolCallItems`（按 tool-call/result ID 稳定
  签名 memo，只在工具调用变化时重跑）+ `useTextStream`（只处理尾部文本，token
  级增量但廉价）两层，消除长 Team 运行的 O(n²)。
- `useAutoScroll`：新增 stickiness ref（距底 50px 内为 sticky），用户上滑置
  false 暂停自动滚动，并显示「↓ 新消息」回到底部指示。

### 工程债清理

- 新建 `frontend/renderer/lib/agentRoles.ts` 作为角色→`{label, icon, description}`
  单一来源，`DelegationCard` / `TaskTimeline` / `TodoProgress` /
  `AssistantMessageParts` 全部改为 import；删除 `TodoProgress.formatTaskLabel`
  （`TodoProgress.tsx:22-23` 自述未用）与整个 `messageOps.ts`（已被
  `messageIndex.ts` 取代）。
- 更新 `docs/agents/03-key-conventions.md §14.5` 删除 `run_chat_path` fallback
  描述；更新 `docs/agents/02-sse-event-contract.md` 补录三个新事件。

## Capabilities (New/Modified)

### New Capabilities

- `agent-team-ux`：AgentTeam 运行期细粒度可见性 —— 子代理输出实时流式、单 agent
  完成/失败事件、团队进度条、单 agent 取消、重规划对比、规划期流式反馈。

### Modified Capabilities

- `sse-event-contract`：新增 `team_agent_token` / `team_node_done` /
  `team_node_error` 三个事件类型；`team_done` 语义调整为"所有子任务完成即发，
  不再等待 Aggregator"。
- `agent-team`（archive）：`TeamNodeCard` 行为由默认折叠改为运行期自动展开；
  `SubAgentGroup` 同步调整。
- 前端渲染管线：`AssistantMessageParts` 分层 memo、`upsertTeamNode` 引用稳定、
  `useAutoScroll` 粘性。

### Removed Capabilities

- 无（本阶段不删除既有能力，仅删除死代码 `TodoProgress.formatTaskLabel` 与
  `messageOps.ts`）。

## Impact

- **后端**: 修改 `team/orchestrator.py`（Planner streaming + team_done 提前 +
  per-agent abort 检查 + `team_node_done`/`team_node_error` 发射）、
  `team/scheduler.py`（`_route_event_for_node` 增发 `team_agent_token`）、
  `api/chat.py`（新增 `/api/chat/team/agent/abort` 端点 + per-agent abort 标志
  模块级变量）。
- **前端**: 修改 `TeamNodeCard.tsx`（自动展开/折叠 + 进度条 + 全部展开开关 +
  supersededPlans 渲染）、`AssistantMessageParts.tsx`（SubAgentGroup 自动展开 +
  分层 memo）、`useChatStream.ts`（3 个新事件 handler）、
  `stores/chat/index.ts`（upsertTeamNode 引用稳定 + supersededPlans）、
  `useAutoScroll.ts`（粘性 + 回到底部指示）、`lib/api/chat.ts`（新增
  abortAgent 调用）；新建 `lib/agentRoles.ts`；删除 `stores/chat/messageOps.ts`。
- **API**: 新增 1 个 REST 端点、3 个 SSE 事件类型。
- **共享类型**: `frontend/shared/api-types.ts` 补 3 个事件 union 分支。
- **文档**: 更新 `02-sse-event-contract.md`、`03-key-conventions.md §14.5`。
- **测试**: 后端 per-agent abort 单测、新 SSE 事件发射单测；前端
  `TeamNodeCard` 自动展开/折叠 + 进度计算 + supersededPlans 渲染测试。
- **性能**: 长 Team 运行（>20 子代理 / >10k token）渲染从 O(n²) 降到接近 O(n)。

## Rollback Plan

本阶段均为叠加式增量，回滚按依赖逆序逐层关闭即可：

1. **前端开关层**：`TeamNodeCard` 自动展开回退为 `useState(false)`；`AgentRow`
  隐藏取消按钮；`useAutoScroll` 移除 stickiness。这三项独立，可单独回滚。
2. **前端事件层**：`useChatStream` 移除三个新 handler —— 后端不发就不触发，
  无破坏性。`upsertTeamNode` 引用稳定优化若出问题，回退为无条件 `map`。
3. **后端事件层**：停止发射 `team_agent_token` / `team_node_done` /
  `team_node_error`；`team_done` 恢复到 Aggregator 之后发射。前端无对应
  handler 时这些事件被 `default` 分支忽略，无破坏性。
4. **后端取消端点**：删除 `/api/chat/team/agent/abort` 与
  `_agent_abort_flags`；前端按钮隐藏即可。
5. **Planner 流式**：切回 `streaming=False` + `ainvoke`，独立可回滚。
6. **死代码清理**：`agentRoles.ts` 回退为各文件内联映射；恢复 `messageOps.ts`
  （若已删）；恢复 `formatTaskLabel`。
7. **文档**：还原 `02-sse-event-contract.md` / `03-key-conventions.md` 旧文。

> 项目硬约束：开发阶段无需灰度与版本兼容，因此回滚即"删除新代码、还原旧代码"，
> 不写灰度开关或 `@Deprecated` 兼容层。每一步回滚后跑 `pnpm typecheck` +
> `uv run pytest tests/python/unit -m "not integration"` 验证。
