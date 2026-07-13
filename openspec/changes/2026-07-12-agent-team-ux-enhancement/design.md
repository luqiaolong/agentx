# Design: Agent Team 第三阶段 — 体验提升（UX Enhancement）

## Context

AgentTeam 优化分三阶段推进。Phase 1（稳定性加固）已用 `asyncio.wait_for` 落地
`subtask_timeout`、用 `asyncio.Semaphore` 落地 `max_parallel`、把 `team_done`
做成前端终止事件、扩展 `tryRecoverResult` 合成 `team_done`、把
`INACTIVITY_TIMEOUT` 提到 180s、去重 error 路径的 `team_done`。Phase 2（架构
清理）已把 5 个 node 函数统一成单一 `_run_subtask_node` + dispatch table、用增强
`_merge_todos` reducer 移除 `_in_progress_tasks` 全局、删除死状态字段
（`subtask_results`、`RouterState.team_blackboard`、`Blackboard.meta`）、在 /reset
做 child_thread_id 清理、收窄 `_inherit_workspace` 异常 scope、给
`_route_event_for_node` 加日志、恢复 `_quality_gate` 同结果检查、迁移
`astream_events` v2→v3、修 `_looks_like_dangerous_task` 词边界、让 `_default_node`
与降级路径一致（无 team_init 则无 team_done）。

**本阶段前提**：Phase 2 的统一 `_run_subtask_node` 是本阶段三个新 SSE 事件
（`team_agent_token` / `team_node_done` / `team_node_error`）的天然发射点；
没有 Phase 2 的统一节点，事件发射会再次分散到 5 个地方。

### 当前痛点与代码锚点

| 痛点 | 锚点 | 现象 |
|---|---|---|
| F1 折叠 | `TeamNodeCard.tsx:87` `useState(false)`；`AssistantMessageParts.tsx:255` `SubAgentGroup` 同样默认折叠 | 运行期只看到 "Agent Team · 执行中" + spinner |
| F2 无流式 | `useChatStream.ts:463-475` 仅在 `team_done` 用 `agentMessages` 回填；`index.ts:917-930` | 执行中 `AgentRow` 无 message/summary |
| F3 无单取消 | `lib/api/chat.ts:443-454` `abort(threadId)` 线程级 | 只能中止整条流 |
| F4 无单 agent 事件 | `api-types.ts:32-100` 无 `team_node_done`/`team_node_error` | 单 agent 失败不可见 |
| F5 无进度 | `TeamNodeCard` 仅逐 agent 状态 | 无聚合百分比 |
| F6 静默重规划 | `index.ts:894-895` `initialAgents` 直接替换 `agents` | 用户看不到 diff |
| F7 label 三重复 | `DelegationCard.tsx:6-20` / `TaskTimeline.tsx:47-59` / `TodoProgress.tsx:30-45` + `normalizeAgentRole`(`AssistantMessageParts.tsx:72-75`) 对齐后端 `_SOURCE_MAP`(`orchestrator.py:286`) | 改一处漏三处 |
| P1 Planner 阻塞 | `orchestrator.py:122` `streaming=False`，L141-146 `ainvoke` 全等 | 5-15s 静默 |
| P2 Aggregator 阻塞 team_done | `orchestrator.py:597-612` 先跑 aggregator 再发 team_done | LLM 慢→前端假死 |
| P3 O(n²) | `AssistantMessageParts.tsx:356` `useMemo([message.parts])` 每 token 重跑 | 长 Team 卡顿 |
| P4 引用不稳 | `index.ts:902` `newAgents.map(...)` 每 event 新引用；`areEqual`(`TeamNodeCard.tsx:141-149`) 比引用 | memo 失效 |
| P5 无粘性 | `useAutoScroll.ts` 24 行无条件滚底 | 用户上翻被拽回 |
| D1 文档陈旧 | `docs/agents/03-key-conventions.md:78` 仍提 `run_chat_path`；与 `agent-architecture-refactor` spec 矛盾 | 误导 |

## Goals / Non-Goals

**Goals:**

- 运行期 `TeamNodeCard` 自动展开，子代理 message 实时流式，单 agent 完成/失败即时可见。
- 用户能单独取消一个子代理而不影响其他。
- 团队进度条与百分比可见。
- 重规划时旧 plan 保留可见并打「已重新规划」标记，不再静默替换。
- Planner 规划期有可见反馈；Aggregator LLM 延迟不再阻塞 Team 生命周期收尾。
- 长 Team 运行（>20 子代理 / >10k token）前端渲染接近 O(n)。
- `TeamNodeCard.memo` 不被无意义引用变化击穿；自动滚动可被用户上滑暂停。
- 角色 label 单一来源；死代码清理；文档与现状一致。

**Non-Goals:**

- 不改 AgentTeam 的 Orchestrator/Aggregator 提示词与拆任务质量（那是后续优化）。
- 不改 `subtask_timeout` / `max_parallel` / `INACTIVITY_TIMEOUT`（Phase 1 已定）。
- 不重构 `_run_subtask_node` 内部结构（Phase 2 已统一），仅在其内部加事件发射与 abort 检查。
- 不做跨会话的 Team 运行历史回放（observation DB 已记录，回放另立提案）。
- 不做后端角色 label 生成器（前端单一来源即可，后端 `_SOURCE_MAP` 保留不动）。
- 不引入新的状态管理库或虚拟滚动库（沿用 zustand + @tanstack/react-virtual）。

## Decisions

### D1: TeamNodeCard 智能展开/折叠策略 + 全部展开开关

**选择**：`TeamNodeCard` 不再用固定 `useState(false)`，改为派生展开状态：

- `status === "running"` 时自动展开；
- `status === "done" || "error"` 时自动折叠；
- 保留用户手动 toggle 能力，但手动状态在 status 切换时被覆盖（运行中展开优先）；
- 卡片头部新增「全部展开 / 全部折叠」开关，作用于当前 plan 的所有 `AgentRow`。

`SubAgentGroup`（`AssistantMessageParts.tsx:255`）同理：当它是"最近一次委派且
status === running"时自动展开；被更新的委派取代或 `team_done` 到达后自动折叠。

**理由**：运行期正是最需要可见性的时候；折叠是"事后复盘"视角。让卡片状态跟随
生命周期，免去用户手动展开的摩擦。全部展开开关覆盖"我想看全部 agent"的场景。

**替代方案**：

- 始终展开 → 拒绝，短 Team 时噪声过大，且 done 后展开占据屏幕。
- 加设置项让用户选默认行为 → 拒绝，违反"不阻塞启动降级"与"少配置"取向，状态
  跟随生命周期已覆盖 90% 场景。

### D2: 新增三个 SSE 事件的契约与发射点

**选择**：在 Phase 2 统一的 `_run_subtask_node` 与 `_route_event_for_node` 处发射：

| 事件 | payload | 发射点 | 前端动作 |
|---|---|---|---|
| `team_agent_token` | `{agent, chunk}` | `scheduler.py::_route_event_for_node` 路由子代理 runner `token` 事件时附带 agent 名增发 | `upsertTeamNode({agentUpdate:{agent, patch:{message: (existing\|\|"") + chunk}}})` |
| `team_node_done` | `{agent, success, summary}` | `_run_subtask_node` runner 成功后、return state 前 | `agentUpdate:{agent, patch:{status:"done", finishedAt:Date.now(), summary}}` |
| `team_node_error` | `{agent, error}` | `_run_subtask_node` runner 失败后、return state 前 | `agentUpdate:{agent, patch:{status:"error", finishedAt:Date.now(), message:error}}` |

向后兼容：`team_done` 的 `agentMessages` 仍回填最终 summary（覆盖流式 raw token，
因 summary 是压缩版）。开发阶段无需保留旧路径，但 `team_done` 本身仍是前端
终止事件（Phase 1 已定），不删。

**理由**：Phase 2 已把 5 个 node 收敛到 1 个 `_run_subtask_node`，事件发射点天然
集中；不再需要在多处加事件。`team_agent_token` 让用户看见子代理"正在想什么"，
`team_node_done/error` 让单 agent 生命周期即时收口，不必等批量。

**替代方案**：

- 复用 `delegation` 事件加 status 字段 → 拒绝，`delegation` 只发 running，语义
  被撑大，且前端 `delegation` handler 已耦合 SubAgentGroup 渲染，改它影响面大。
- 仅在前端轮询 blackboard → 拒绝，无轮询基础设施，且违背 SSE 推送模型。

### D3: 子代理单独取消 — per-agent abort flag + REST 端点

**选择**：

- 后端 `api/chat.py` 新增 `POST /api/chat/team/agent/abort`，body
  `{thread_id, agent_name}`，写入模块级 `_agent_abort_flags: dict[str, set[str]]`
  （thread_id → 待取消 agent 名集合）。
- `_run_subtask_node` 在每轮迭代（含工具调用前后）检查
  `agent_name in _agent_abort_flags.get(thread_id, set())`，命中则清条目并返回
  `TeamSubtaskResult(success=False, payload="用户取消")`，与全局 `abort_event`
  检查并列。
- 前端 `AgentRow` 在 `status === "running"` 时显示「×」按钮，点击调新端点；
  成功后该 agent 立即置 `error`（前端乐观更新 + 等 `team_node_error`/`team_done` 确认）。

**理由**：线程级 `abort_event` 粒度太粗；per-agent flag 复用同一检查点模式，
最小侵入。Team 继续跑其他 agent，被取消的按失败处理，Aggregator 仍能汇总。

**替代方案**：

- 给每个子代理一个独立 `asyncio.Task` 然后 `task.cancel()` → 拒绝，DeepAgents
  runner 内部状态机复杂，外部 cancel 会留下半成品状态；flag + 优雅返回更安全。
- 前端直接关 SSE 连接 → 拒绝，那是整流取消，回到 F3 原问题。

### D4: 团队进度条与百分比

**选择**：`TeamNodeCard` 头部在 `status === "running"` 时显示进度条：

- `doneCount = agents.filter(a => a.status === "done" || a.status === "error").length`
- 显示 `${doneCount}/${agents.length}` + `Math.round(doneCount/agents.length*100)+"%"`
- 视觉为细横条，done 段填充，error 段用红色尾巴区分。

**理由**：F5 痛点直击；`TeamNodeCard` 已有 agents 数组，零额外数据获取成本。

**替代方案**：复用 `TodoProgress` 组件 → 拒绝，`TodoProgress` 是主任务 todo
维度（`completedTodos/todos.length`），与 agent 维度语义不同，混用会误导。

### D5: 重规划可视化 — supersededPlans 数组

**选择**：`upsertTeamNode` 收到 `initialAgents` 且 team part 已存在时：

- 不再 `newAgents = updaters.initialAgents`（`index.ts:894-895` 旧行为）；
- 改为 `supersededPlans = [...existing.supersededPlans, existing.agents]`，
  `agents = updaters.initialAgents`，旧 plan 进历史栈。
- `TeamAgentState` 不变；team part 新增 `supersededPlans: TeamAgentState[][]`。
- `TeamNodeCard` 在当前 plan 上方渲染折叠的「Previous Plan (N)」区，每个旧 plan
  打「已重新规划」灰色 badge，默认折叠。

**理由**：静默替换让用户失去对"计划为何变了"的判断依据；保留旧 plan 仅做只读
展示，不参与进度计算（进度只算当前 plan），既透明又不污染当前状态。

**替代方案**：

- 直接展示 diff（高亮增删 agent）→ 拒绝，diff 计算复杂且重规划通常整体换思路，
  agent 名对齐意义有限；折叠旧 plan 已足够。
- 把旧 plan 写入 observation DB 供事后查 → 拒绝，本阶段不做历史回放（Non-Goal）。

### D6: 角色 label 单一来源 — agentRoles.ts

**选择**：新建 `frontend/renderer/lib/agentRoles.ts`，导出

```ts
export const AGENT_ROLE_META: Record<string, { label: string; icon: IconType; description: string }>;
export function getRoleMeta(role: string): { label: string; icon: IconType; description: string };
```

`DelegationCard`（`DelegationCard.tsx:6-20` `SUBAGENT_META`）、`TaskTimeline`
（`TaskTimeline.tsx:47-59` `AGENT_ROLE_LABELS`）、`TodoProgress`
（`TodoProgress.tsx:30-45` `formatTaskLabel`）、`AssistantMessageParts`
（`normalizeAgentRole` L72-75）全部改 import。删除 `formatTaskLabel`
（`TodoProgress.tsx:22-23` 自述未用）与整个 `messageOps.ts`（已被
`messageIndex.ts` 取代）。

**理由**：四份重复映射是改一处漏三处的根因；前端单一来源即可，后端
`_SOURCE_MAP`（`orchestrator.py:286`）是后端自用，不强求统一（Non-Goal）。

**替代方案**：后端生成 JSON 供前端消费 → 拒绝，引入构建期/运行期依赖，且后端
`_SOURCE_MAP` 含后端专属字段，非纯展示数据；前端单一来源已消除重复。

### D7: Planner 流式 + Aggregator 与 team_done 解耦

**选择**：

- **Planner**（`orchestrator.py:122`）：`streaming=True`，把 `ainvoke`
  （L141-146）换成 `async for chunk in llm.astream([...])`：每 chunk 发
  `reasoning` 事件（`source="team_planner"`，前端渲染"Planning..."指示），流结束
  拼接全文后解析 `[agent:xxx]` 行，后续 `_plan_node` 逻辑不变。
- **Aggregator**（`orchestrator.py:597-612`）：把 `team_done` 发射移到
  `_run_aggregator` 之前 —— 所有子任务 summary 已在 `findings` 中，先发
  `team_done{status:"done", agents: agent_summaries}` 收口 Team 生命周期，再跑
  aggregator 流式 `token` 事件输出最终答案；`done` 终止事件由 chat.py 在
  aggregator 流结束后发。

**理由**：P1 给规划期反馈；P2 把 Team 收尾与 Aggregator LLM 延迟解耦 —— 用户先
看到"所有 agent 完成 + 各自 summary"，再看到 aggregator 综合答案，体验连贯。
开发阶段无需兼容，直接改发射顺序。

**替代方案**：

- Planner 改用结构化输出（`with_structured_output`）→ 拒绝，现有 `[agent:xxx]`
  文本解析已稳定，且结构化输出会丢规划期 reasoning 流式反馈。
- 把 aggregator 拆成独立 SSE 流 → 拒绝，破坏单 SSE 连接模型，且 `done` 由 chat.py
  统一管理更简单。

### D8: 前端渲染性能 — 分层 memo + 引用稳定 + 自动滚动粘性

**选择**（三项独立优化）：

1. **分层 memo**（P3）：`AssistantMessageParts` 拆两 hook：
   - `useToolCallItems(message.parts)`：memo 依赖派生自 tool-call/result part ID
     的稳定签名（如 `parts.filter(p => p.type==="tool_call"||"tool_result").map(p => p.id).join("|")`），只在工具调用实际变化时重跑昂贵分组逻辑。
   - `useTextStream(message.parts)`：只取尾部 text/reasoning part，每 token 重跑
     但仅渲染最新文本，廉价。
2. **引用稳定**（P4）：`upsertTeamNode` 的 `agentUpdate` 分支（`index.ts:902`）改为
   浅结构比较：构造候选 `newAgent = {...a, ...patch}`，若 `patch` 各字段值与 `a` 现有
   值 `Object.is` 相等，则 `newAgents = existing.agents`（同引用，不触发重渲）；
   仅当至少一个字段实际变化才 `newAgents.map(...)` 产新引用。文档化不变式：
   `agents` 引用变化当且仅当某 agent 可见状态实际变化。
3. **自动滚动粘性**（P5）：`useAutoScroll` 加 `stickyRef`：监听容器 scroll，距底
   ≤50px 时 `stickyRef.current = true`，否则 false；`messages` 引用变化时仅当
   sticky 为 true 才 `scrollToBottom`；sticky 为 false 时显示「↓ 新消息」按钮，
   点击回到底部并恢复 sticky。

**理由**：三项分别治 O(n²) 分组、memo 失效、被动滚屏；互不依赖，可独立上线与回滚。

**替代方案**：

- 用 `@tanstack/react-virtual` 虚拟化整个消息流 → 拒绝，消息流本已用虚拟滚动，
  痛点是 parts 内部分组每 token 重跑，虚拟化解决不到。
- 用 immer 不可变更新自动保引用 → 拒绝，引入新依赖且 immer 的结构性共享仍会在
  patch 时产新引用，不如显式浅比较直接。

## Detailed Design

### D1 详细：TeamNodeCard 展开状态机

```
status 切换:
  -> running: setExpanded(true)（覆盖手动）
  -> done|error: setExpanded(false)（覆盖手动）
用户点 header toggle:
  setExpanded(v => !v)（仅当前 status 下生效，下次 status 切换仍覆盖）
「全部展开」开关:
  本地 state allExpanded，作用域当前 plan 的 AgentRow 显隐
SubAgentGroup:
  derived = isLatestDelegation && status === "running" ? true
          : (delegationReplaced || teamDone) ? false
          : 保持手动
```

实现上 `TeamNodeCard` 用 `useEffect([status])` 驱动 `setExpanded`；`SubAgentGroup`
由父层传入 `autoExpand` 布尔派生，避免内部 effect。

### D2 详细：事件发射时序

`_run_subtask_node`（Phase 2 统一节点）改造：

```
# In _run_subtask_node (Phase 2 unified node), after _run_subtask_stream returns:
try:
    result = await _run_subtask_stream(
        runner=config.runner_factory(state),
        runner_args=config.runner_args(state),
        agent_name=task.agent,
        abort_event=state["abort_event"],
        writer=writer,
    )
    if result.success:
        writer(make_sse_event("team_node_done", {"agent": task.agent, "success": True, "summary": result.payload[:200]}))
    else:
        writer(make_sse_event("team_node_error", {"agent": task.agent, "error": result.payload}))
except asyncio.TimeoutError:
    # Phase 1 timeout (wrapped inside _run_subtask_stream) propagates as TimeoutError
    writer(make_sse_event("team_node_error", {"agent": task.agent, "error": f"子任务超时（{state['subtask_timeout']}s）"}))
    result = TeamSubtaskResult(agent=task.agent, success=False, payload=f"子任务超时（{state['subtask_timeout']}s）")
except asyncio.CancelledError:
    writer(make_sse_event("team_node_error", {"agent": task.agent, "error": "子任务已取消"}))
    result = TeamSubtaskResult(agent=task.agent, success=False, payload="子任务已取消")
    raise  # H4: propagate cancellation for partial-state aggregation
```

**移除 Phase 1 在超时分支发射的 `delegation{source:"team", event:"timeout"}` 事件**；超时统一由
`team_node_error` 覆盖。超时作为 failure 的一种，`team_node_error` 的 `error` payload 设为
`'子任务超时（{subtask_timeout}s）'`。这避免 Phase 3 之后超时同时发射 `delegation{event:"timeout"}`
与 `team_node_error` 两个事件造成前端重复处理（上方 `asyncio.TimeoutError` 分支即落实此迁移）。

`_route_event_for_node`（scheduler）改造：当收到子代理 runner 的 `token` 事件，
除原样转发外，额外 `writer(make_sse_event("team_agent_token",
{"agent": node.agent, "chunk": token_chunk}))`。注意去重：原 `token` 事件仍发
（主消息流需要），`team_agent_token` 是额外副本，仅前端 team handler 消费。

### D3 详细：per-agent abort

`api/chat.py`：

```python
_agent_abort_flags: dict[str, set[str]] = {}  # thread_id -> {agent_name}

@router.post("/api/chat/team/agent/abort")
async def abort_team_agent(body: TeamAgentAbortRequest):
    _agent_abort_flags.setdefault(body.thread_id, set()).add(body.agent_name)
    return {"ok": True}
```

`_run_subtask_node` 检查点（与现有 `abort_event` 检查并列）：

```python
if task.agent in _agent_abort_flags.get(thread_id, set()):
    _agent_abort_flags[thread_id].discard(task.agent)
    return TeamSubtaskResult(success=False, payload="用户取消")
```

### D4 详细：进度计算

`TeamNodeCard` 头部新增 `<TeamProgress agents={agents} status={status} />`：

```
doneCount = agents.filter(a => a.status==="done"||a.status==="error").length
pct = agents.length ? Math.round(doneCount/agents.length*100) : 0
```

仅 `status==="running"` 时渲染；done/error 时隐藏（避免噪声）。

### D5 详细：supersededPlans

`MessagePart` 的 team 变体新增 `supersededPlans: TeamAgentState[][]`（默认 `[]`）。
`upsertTeamNode`（`index.ts:894`）：

```ts
if (updaters.initialAgents) {
  if (existing) {
    newSuperseded = [...existing.supersededPlans, existing.agents];
  }
  newAgents = updaters.initialAgents;
}
```

`TeamNodeCard` 在 reasoning 之上、当前 agents 之上渲染：

```
{supersededPlans.map((plan, i) => (
  <details key={i}>
    <summary>Previous Plan {i+1} <Badge>已重新规划</Badge></summary>
    {plan.map(a => <AgentRow agent={a} dimmed />)}
  </details>
))}
```

进度只算 `agents`（当前 plan），不算 `supersededPlans`。

### D6 详细：agentRoles.ts

```ts
export const AGENT_ROLE_META: Record<string, { label: string; icon: string; description: string }> = {
  // Built-in subagents
  rag:       { label: "RAG 子代理",    icon: "📚", description: "知识库检索" },
  web:       { label: "Web 子代理",    icon: "🌐", description: "网络搜索" },
  deep:      { label: "DeepAgent",     icon: "🤖", description: "深度任务执行（含写操作+审批）" },
  coding:    { label: "Coding Expert", icon: "💻", description: "代码专家" },
  work:      { label: "Work Agent",    icon: "🛠️", description: "全能工作代理" },
  // Team roles (7 builtin)
  frontend_dev:      { label: "前端开发",   icon: "🎨", description: "前端代码实现" },
  backend_dev:       { label: "后端开发",   icon: "⚙️", description: "后端代码实现" },
  tester:            { label: "测试工程师", icon: "🧪", description: "测试用例编写与验证" },
  architect:         { label: "架构师",     icon: "🏗️", description: "系统架构设计" },
  devops:            { label: "DevOps",    icon: "🚀", description: "部署与运维" },
  ui_designer:       { label: "UI 设计师",  icon: "✨", description: "界面设计" },
  product_manager:   { label: "产品经理",   icon: "📋", description: "需求分析与产品规划" },
  // Custom agents (dynamic prefix)
  // custom-<key>: { label: key, icon: "🔧", description: "自定义子代理" }
};
export function getRoleMeta(role: string) {
  return AGENT_ROLE_META[role] ?? { label: role, icon: "🔧", description: "" };
}
```

> 注：`label`/`icon`/`description` 字段名与后端 `_SOURCE_MAP`（`orchestrator.py:286`）对齐；
> 新增 agent 必须在此处同步注册，否则前端显示原始 agent 名。

四个文件删本地 map，改 `import { getRoleMeta } from "@/lib/agentRoles"`。

### D7 详细：Planner astream

```python
llm = get_chat_model(temperature=..., streaming=True)
full_text = []
async for chunk in llm.astream([{"role":"system",...},{"role":"user",...}]):
    full_text.append(chunk.content)
    writer(make_sse_event("reasoning", {"content": chunk.content, "source": "team_planner"}))
response_text = "".join(full_text)
# 解析 [agent:xxx] 行（现有逻辑不变）
```

Aggregator 顺序（`orchestrator.py:597-612`）：

```python
# 先发 team_done（所有子任务 summary 已就绪）
writer(make_sse_event("team_done", {"status": "done"|"error", "agents": agent_summaries}))
# 再跑 aggregator（流式 token）
async for sse in _run_aggregator(...):
    writer(sse)
# done 由 chat.py 在 aggregator 流结束后发
```

#### 与 Phase 1 watchdog 的协调

Phase 1 D4 设有 2s done-watchdog：`team_done` 后若 2s 内未收到 `done`，前端调用
`setSessionRunning(false)`。由于 D7 把 `team_done` 提前到 aggregator 之前，aggregator LLM
调用通常 >2s，若直接在 `team_done` 上 arm watchdog，watchdog 会在 aggregator 流式期间误触发，
提前结束会话。协调方案：

- (a) `team_done` 在 aggregator 之前发射（已由 D7 保证），仅收口 Team 子任务生命周期，
  **不** arm 2s watchdog。
- (b) 2s watchdog **不**在 `team_done` 上 arm，而是在 aggregator 流的**第一个 `token` 事件**
  到达时 arm（表示流已开始）；此后每个后续 `token` 重置 2s 计时器（token 间间隔 >2s 才视为卡死）。
- (c) 若 `team_done` 后 **30s 内无任何 `token`** 到达（aggregator LLM hung），watchdog 触发，
  前端 `setSessionRunning(false)` 并标记 aggregator 超时。
- (d) aggregator 流正常结束时，chat.py 照常发 `done`，前端正常关流（watchdog 清除）。

时序：`team_done`（提前，解耦 aggregator）→ [aggregator LLM token 流，每 token 重置 2s 计时器]
→ `done`（chat.py，aggregator 流结束）→ 关流。30s 无 token 则 watchdog 兜底。

### D8 详细：分层 memo 签名

```ts
function useToolCallItems(parts: MessagePart[]) {
  const sig = parts
    .filter(p => p.type === "tool_call" || p.type === "tool_result")
    .map(p => `${p.type}:${p.id}`).join("|");
  return useMemo(() => buildToolCallGroups(parts), [sig]);  // 非 [parts]
}
function useTextStream(parts: MessagePart[]) {
  const tail = parts.filter(p => p.type === "text" || p.type === "reasoning");
  return useMemo(() => renderTail(tail), [tail]);  // 每 token 重跑但廉价
}
```

引用稳定（`index.ts:902`）：

```ts
newAgents = newAgents.map((a, i) => {
  if (i !== idx) return a;
  const candidate = { ...a, ...patch };
  const changed = Object.keys(patch).some(k => !Object.is((a as any)[k], (candidate as any)[k]));
  return changed ? candidate : a;  // 未变返回原引用，保持数组同引用
});
// 进一步：若所有项都返回原引用，则 newAgents === existing.agents
```

## Risk & Mitigation

| 风险 | 影响 | 缓解 |
|---|---|---|
| `team_agent_token` 在高频 token 下淹没 SSE 通道 | 前端卡顿 / 后端 SSE 缓冲膨胀 | 子代理 token 走原 `token` 事件，`team_agent_token` 仅 team handler 消费；本阶段默认启用 16ms chunk 合并节流（~60 events/s 上限）：连续 `team_agent_token` 事件在 16ms 窗口内合并为单个事件，payload 为合并后的 chunk 串。验收时观测一次 >20 子代理 / >10k token 运行的事件速率并记录，若事件速率持续 >200/s 则将窗口扩大至 32ms |
| per-agent abort flag 在 thread 复用时残留 | 误取消下一轮同 thread 的 agent | abort 命中后立即 `discard` 清条目；`/reset` 与 `team_done` 时清空整个 `_agent_abort_flags[thread_id]` |
| Planner `astream` 改动破坏 `[agent:xxx]` 解析 | team_init 不发，Team 跑不起来 | 流结束拼接全文后再解析，解析逻辑与 `ainvoke` 路径完全一致；单测覆盖解析 |
| `team_done` 提前导致 Aggregator 失败时前端已显示"完成" | 误导用户 | `team_done` 标 Team 子任务完成，Aggregator 失败仍发 `error` 事件；前端 `done` 由 chat.py 控制，Aggregator 失败时 `error` 覆盖状态 |
| `supersededPlans` 在多次重规划下累积膨胀 | 内存 / DOM 膨胀 | 旧 plan 仅存 agent 元数据（不含 message 全文），单 plan <1KB；上限保留最近 3 个 |
| 引用稳定优化误判"未变"导致 UI 不更新 | agent 状态卡住 | 浅比较仅对 patch 列出的字段；`status`/`message`/`summary` 任一变即触发新引用；单测覆盖各字段变化场景 |
| `useAutoScroll` 粘性误判（窗口 resize） | 用户被卡在中间 | 粘性仅看距底绝对像素（50px），resize 不影响；回到底部按钮始终可见 |
| 删 `messageOps.ts` 误伤隐性引用 | 编译失败 | 删前全局 grep `messageOps` 确认无 import；`pnpm typecheck` 验证 |
| 文档更新遗漏同步 `02-sse-event-contract.md` | 契约不一致 | tasks.md 把文档更新列为独立任务，验收对照三事件名 |
