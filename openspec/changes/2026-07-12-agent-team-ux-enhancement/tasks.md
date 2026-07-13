# Tasks: Agent Team 第三阶段 — 体验提升（UX Enhancement）

> 依赖：Phase 1（稳定性加固）与 Phase 2（架构清理）已落地。本阶段在 Phase 2
> 统一的 `_run_subtask_node` 之上叠加。任务按依赖排序分 6 阶段，逐阶段验收。

## 预期修改文件

- [ ] `backend/app/team/orchestrator.py`（Planner streaming + team_done 提前 + per-agent abort 检查 + team_node_done/error 发射）
- [ ] `backend/app/team/scheduler.py`（`_route_event_for_node` 增发 team_agent_token）
- [ ] `backend/app/api/chat.py`（新增 `/api/chat/team/agent/abort` + `_agent_abort_flags`）
- [ ] `frontend/shared/api-types.ts`（补 3 个事件 union 分支）
- [ ] `frontend/renderer/hooks/useChatStream.ts`（3 个新事件 handler）
- [ ] `frontend/renderer/stores/chat/index.ts`（upsertTeamNode 引用稳定 + supersededPlans）
- [ ] `frontend/renderer/components/chat/parts/TeamNodeCard.tsx`（自动展开/折叠 + 进度条 + 全部展开开关 + supersededPlans 渲染 + AgentRow 取消按钮）
- [ ] `frontend/renderer/components/chat/AssistantMessageParts.tsx`（SubAgentGroup 自动展开 + 分层 memo）
- [ ] `frontend/renderer/hooks/useAutoScroll.ts`（粘性 + 回到底部指示）
- [ ] `frontend/renderer/lib/agentRoles.ts`（新建单一来源）
- [ ] `frontend/renderer/lib/api/chat.ts`（新增 abortTeamAgent 调用）
- [ ] `frontend/renderer/stores/chat/messageOps.ts`（删除）
- [ ] `docs/agents/02-sse-event-contract.md`、`docs/agents/03-key-conventions.md`（文档同步）
- [ ] 后端单测 + 前端组件测试

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T1 | 后端：scheduler 增发 team_agent_token | scheduler.py | `_route_event_for_node` 路由子代理 token 时额外发 team_agent_token{agent,chunk} | ⬜ |
| T2 | 后端：_run_subtask_node 发 team_node_done/error | orchestrator.py | runner 完成/失败后、return state 前发对应事件 | ⬜ |
| T3 | 后端：per-agent abort 端点 + 检查 | chat.py, orchestrator.py | POST /api/chat/team/agent/abort 写 flag；_run_subtask_node 检查命中返回"用户取消" | ⬜ |
| T4 | 后端：Planner astream 流式 | orchestrator.py | streaming=True，规划期发 reasoning{source=team_planner}，流后解析 [agent:xxx] | ⬜ |
| T5 | 后端：team_done 提前到 aggregator 前 | orchestrator.py | team_done 在 _run_aggregator 之前发；done 由 chat.py 在 aggregator 后发 | ⬜ |
| T6 | 前端：api-types + useChatStream 三事件 | api-types.ts, useChatStream.ts | union 补 3 分支；handler 增量追加 message / 标记 done|error | ⬜ |
| T7 | 前端：upsertTeamNode 引用稳定 + supersededPlans | stores/chat/index.ts | patch 未变返回同引用；initialAgents 入 supersededPlans | ⬜ |
| T8 | 前端：TeamNodeCard 自动展开 + 进度 + 取消按钮 | TeamNodeCard.tsx | running 展开/done|error 折叠；头部进度条；AgentRow ×按钮 | ⬜ |
| T9 | 前端：SubAgentGroup 自动展开 + 分层 memo | AssistantMessageParts.tsx | 最新委派 running 自动展开；useToolCallItems/useTextStream 拆分 | ⬜ |
| T10 | 前端：useAutoScroll 粘性 | useAutoScroll.ts | 距底 50px 内才自动滚；上滑暂停 + 新消息指示 | ⬜ |
| T11 | 前端：agentRoles.ts 单一来源 + 死代码清理 | agentRoles.ts(新), 4 文件改 import, 删 messageOps.ts | 四文件 import getRoleMeta；删 formatTaskLabel 与 messageOps.ts | ⬜ |
| T12 | 文档：02-sse-event-contract + 03-key-conventions §14.5 | docs/agents/*.md | 补 3 事件；删 run_chat_path fallback 描述 | ⬜ |
| T13 | 测试：后端 abort + 事件发射单测 | tests/python/unit/ | abort flag 命中、3 事件发射、planner astream 解析 | ⬜ |
| T14 | 测试：前端组件测试 | frontend 测试 | TeamNodeCard 展开/折叠/进度/supersededPlans、useChatStream handler | ⬜ |

## 1. 后端：子代理可见性事件（D2）

- [ ] `scheduler.py::_route_event_for_node`：路由子代理 runner 的 `token` 事件时，额外 `writer(make_sse_event("team_agent_token", {"agent": node.agent, "chunk": token_chunk}))`；原 `token` 事件保留转发
- [ ] `orchestrator.py::_run_subtask_node`：runner 成功后 `team_node_done{agent, success:true, summary}`，失败后 `team_node_error{agent, error}`，均在 return state update 之前发射
- [ ] `api-types.ts`：ChatEvent union 补 `team_agent_token` / `team_node_done` / `team_node_error` 三个分支（含 `trace_id?`）
- [ ] `useChatStream.ts`：新增三 handler —— `team_agent_token` 增量追加 `agent.message`；`team_node_done` 置 `status:"done"`+`finishedAt`+`summary`；`team_node_error` 置 `status:"error"`+`finishedAt`+`message`
- [ ] T2.1 移除 Phase 1 的 timeout `delegation` 事件发射，改为 `team_node_error`：`_run_subtask_node` 超时分支（`asyncio.TimeoutError`）发 `team_node_error{agent, error:"子任务超时（Ns）"}`，不再发 `delegation{source:"team", event:"timeout"}`；前端 `delegation` handler 移除 timeout 处理
- [ ] T2.2 实现 `team_agent_token` 16ms chunk 合并节流：连续 `team_agent_token` 事件在 16ms 窗口内合并为单个事件，payload 为合并后的 chunk 串；验收时观测一次 >20 子代理 / >10k token 运行事件速率，若持续 >200/s 则将窗口扩大至 32ms
- [ ] 验证：发起一次 Team 运行，观察 `AgentRow` 在执行中实时显示 message、单个完成/失败即时变色，无需等 `team_done`

## 2. 后端：子代理单独取消（D3）

- [ ] `api/chat.py`：新增模块级 `_agent_abort_flags: dict[str, set[str]]` 与 `POST /api/chat/team/agent/abort` 端点（body `{thread_id, agent_name}`），写入 flag 后返回 `{ok:true}`
- [ ] `orchestrator.py::_run_subtask_node`：在现有 `abort_event` 检查点旁，加 `agent_name in _agent_abort_flags.get(thread_id, set())` 检查；命中则 `discard` 该条目并返回 `TeamSubtaskResult(success=False, payload="用户取消")`
- [ ] `team_done` 与 `/reset` 路径清理 `_agent_abort_flags[thread_id]`，避免 thread 复用残留
- [ ] `lib/api/chat.ts`：新增 `abortTeamAgent(threadId, agentName)` 调用新端点
- [ ] `TeamNodeCard.AgentRow`：`status === "running"` 时渲染「×」按钮，点击调 `abortTeamAgent`；乐观置 `error`，等 `team_node_error`/`team_done` 确认
- [ ] 验证：3 agent 并行时取消其中一个，另两个继续并完成，被取消 agent 显示"用户取消"

## 3. 后端：Planner 流式 + Aggregator 解耦（D7）

- [ ] `orchestrator.py::_plan_node`：`get_chat_model(streaming=True)`；`ainvoke` 换 `async for chunk in llm.astream([...])`，每 chunk 发 `reasoning{content, source:"team_planner"}`；流结束拼接全文后走原 `[agent:xxx]` 解析
- [ ] `orchestrator.py::_aggregate_node`：把 `team_done` 发射（L607-612）移到 `_run_aggregator` 调用（L597）之前；aggregator 流式 `token` 照常发；`done` 终止事件由 chat.py 在 aggregator 流结束后发
- [ ] 前端 `reasoning` handler 已支持 `source` 字段，`source==="team_planner"` 时渲染"Planning..."指示（如无指示组件则新增轻量 inline 标记）
- [ ] T4.1 验证 aggregator 流式期间 watchdog 不误触发：Phase 1 D4 的 2s done-watchdog 不在 `team_done` 上 arm，而在 aggregator 第一个 `token` 到达时 arm，每个后续 `token` 重置 2s 计时器；`team_done` 后 30s 内无 `token` 则 watchdog 兜底触发 `setSessionRunning(false)`；aggregator 流结束 `done` 收到则正常关流
- [ ] 验证：Team 规划期前端可见 reasoning 流式输出；所有子任务完成后立即见 `team_done`+各 summary，再连续见 aggregator 最终答案 token

## 4. 前端：卡片可见性 + 进度 + 重规划可视化（D1, D4, D5）

- [ ] `TeamNodeCard.tsx`：`useEffect([status])` 驱动 `setExpanded` —— `running` 展开、`done|error` 折叠，覆盖手动；保留 header toggle（当前 status 下生效）
- [ ] `TeamNodeCard.tsx`：头部新增「全部展开 / 全部折叠」开关，作用当前 plan 的所有 `AgentRow`
- [ ] `TeamNodeCard.tsx`：头部新增 `<TeamProgress>` —— `doneCount = agents.filter(done|error).length`，显示 `${doneCount}/${agents.length}` + 百分比 + 细横条（error 段红色），仅 `running` 时渲染
- [ ] `stores/chat/index.ts`：team part 类型加 `supersededPlans: TeamAgentState[][]`；`upsertTeamNode` 收到 `initialAgents` 且 existing 时，把旧 `agents` 推入 `supersededPlans`，`agents` 换新
- [ ] `TeamNodeCard.tsx`：当前 plan 上方渲染折叠「Previous Plan (N)」区，每个旧 plan 打「已重新规划」badge，dimmed；进度只算当前 plan
- [ ] `AssistantMessageParts.tsx::SubAgentGroup`：派生 `autoExpand = isLatestDelegation && status==="running"`；被新委派取代或 `team_done` 后自动折叠
- [ ] 验证：运行期卡片自动展开可见 agent 行与进度；触发重规划（MEDIUM-5 场景）后旧 plan 保留可见并打标

## 5. 前端：性能优化 — 分层 memo + 引用稳定 + 自动滚动粘性（D8）

- [ ] `AssistantMessageParts.tsx`：拆 `useToolCallItems(parts)`（memo 依赖 tool-call/result part ID 稳定签名，非 `parts` 引用）与 `useTextStream(parts)`（仅处理尾部 text/reasoning，每 token 重跑但廉价）
- [ ] `stores/chat/index.ts::upsertTeamNode`：`agentUpdate` 分支浅比较 —— patch 字段值与现有值 `Object.is` 全等时返回原 `agents` 引用；仅当至少一字段实际变化才产新引用；文档化不变式
- [ ] `useAutoScroll.ts`：加 `stickyRef`（距底 ≤50px 为 true）；`messages` 变化时仅 sticky 为 true 才 `scrollToBottom`；sticky 为 false 时显示「↓ 新消息」按钮，点击回底并恢复 sticky
- [ ] 验证：长 Team 运行（>20 子代理 / >10k token）渲染流畅；`TeamNodeCard.memo` 在无可见变化时不重渲；上滑查看不被拽回

## 6. 工程债清理 + 文档同步（D6, D1 文档）

- [ ] 新建 `frontend/renderer/lib/agentRoles.ts`：导出 `AGENT_ROLE_META` 与 `getRoleMeta(role)`
- [ ] `DelegationCard.tsx`、`TaskTimeline.tsx`、`TodoProgress.tsx`、`AssistantMessageParts.tsx` 删本地 map，改 `import { getRoleMeta } from "@/lib/agentRoles"`
- [ ] 删除 `TodoProgress.formatTaskLabel`（`TodoProgress.tsx:22-23` 自述未用）
- [ ] 删除 `frontend/renderer/stores/chat/messageOps.ts`（已被 `messageIndex.ts` 取代）；删前 grep 确认无 import
- [ ] `docs/agents/03-key-conventions.md §14.5`：删除 `run_chat_path` fallback 描述（与 `agent-architecture-refactor` spec 矛盾）
- [ ] `docs/agents/02-sse-event-contract.md`：补录 `team_agent_token` / `team_node_done` / `team_node_error` 三事件 payload 与发射时机；注明 `team_done` 提前到 aggregator 前
- [ ] 后端单测：`test_team_agent_abort.py`（abort flag 命中 / thread 复用清理）、`test_team_node_events.py`（三事件发射顺序）、`test_planner_astream.py`（流式后解析正确）
- [ ] 前端测试：`TeamNodeCard`（自动展开/折叠 / 进度计算 / supersededPlans 渲染 / 取消按钮）、`useChatStream`（三 handler）
- [ ] 全量验收：`pnpm typecheck` + `uv run pytest tests/python/unit -m "not integration"` + 手动一次完整 Team 运行
