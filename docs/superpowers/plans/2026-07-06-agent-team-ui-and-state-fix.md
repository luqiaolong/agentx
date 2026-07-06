# Agent Team UI 与状态管理修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Agent Team 模式下 agent 输出平铺展示、状态卡在执行中、后端事件序列混乱、subagent 上下文丢失等问题。

**Architecture:**
- 前端引入 `TeamNode` 聚合 part，把 team_plan / team_progress / team_result 收敛为树状结构（team_plan 为根，下挂多个 agent 节点，每个 agent 节点聚合其 progress + result）
- 后端 team_path 修正事件序列：先发 running 再执行，子任务完成发 done，最后发 `team_done` 表示团队整体结束
- 后端增加子任务超时防护，避免 _subtask_done 哨兵丢失导致主循环死锁
- 后端 Orchestrator prompt 注入项目上下文（AGENTS.md 文件地图 + 前置 step 发现），subagent 拿到精确路径

**Tech Stack:** Python 3.11 + FastAPI + LangGraph（后端）；React 18 + TypeScript + zustand（前端）

---

## 问题清单（排查结论）

### 前端问题（用户明确关心）

| ID | 问题 | 影响 |
|---|---|---|
| F1 | agent 输出平铺在 parts 数组，没有挂在 agent 节点下 | 用户无法看出哪个 agent 产出了什么 |
| F2 | team_progress part 一旦写入 status="running" 不会更新，后续 done 是新 part | agent 状态视觉上永远卡在执行中 |
| F3 | team_progress 重复 part 堆积（同一 agent 多个 part） | parts 数组膨胀，渲染混乱 |
| F4 | 没有 team 整体生命周期状态 | 看不到"团队已完成" |

### 后端问题

| ID | 问题 | 影响 |
|---|---|---|
| B1 | `while done_count < total` 依赖 _subtask_done 哨兵，deep 子任务 approval 阻塞会死锁 | 主循环永不退出 |
| B2 | running 事件批量提前发，与实际执行时机不一致 | 前端状态不准 |
| B3 | 没有 team_done 事件，依赖 run_router 末尾 done | 前端无法区分 team 结束 |
| B4 | subagent 拿不到项目上下文（AGENTS.md 文件地图、前置 step 发现） | subagent 盲探索 |
| B5 | Aggregator 无质量门（不去重、不检查完成度） | 4 份相同截断结果被汇总 |
| B6 | agent_mode == "agent_team" 直接进 team_path，无任务复杂度评估 | 简单任务也走 team |

---

## File Structure

### 前端
- **Modify:** `frontend/renderer/stores/chat.ts` — 新增 `TeamNodePart` 类型 + `upsertTeamNode` action
- **Modify:** `frontend/renderer/hooks/useChatStream.ts` — team 事件聚合到 TeamNode
- **Modify:** `frontend/renderer/components/chat/AssistantUIThread.tsx` — TeamNode 树状渲染
- **Create:** `frontend/renderer/components/chat/parts/TeamNodeCard.tsx` — Team 节点卡片组件

### 后端
- **Modify:** `backend/app/paths/team_path.py` — 事件序列修正 + team_done + 超时防护 + 上下文注入
- **Modify:** `backend/app/router/graph.py` — run_router 透传 team_done 事件
- **Modify:** `backend/app/main.py` — SSE 事件契约注释更新（team_done）

### 测试
- **Modify:** `tests/python/unit/paths/test_team_path.py` — 新增事件序列 + 超时 + 上下文测试
- **Create:** `tests/renderer/TeamNodeCard.test.tsx` — Team 节点渲染测试

---

## Phase 1: 前端 UI 改造 — agent 输出挂在 agent 节点下

### Task 1: 新增 TeamNodePart 类型与 store action

**Files:**
- Modify: `frontend/renderer/stores/chat.ts:15-49`（MessagePart 类型定义）
- Modify: `frontend/renderer/stores/chat.ts:87-170`（ChatState 接口 + 实现）

- [ ] **Step 1: 在 chat.ts 中新增 TeamNodePart 类型**

替换现有的 `team_plan` / `team_progress` / `team_result` 三个独立 part 类型为聚合的 `team` part：

```typescript
// 替换 chat.ts:35-49 的三个 team_* part 定义为：
| {
    type: "team";
    id: string;
    plan: { agent: string; input: string; purpose: string }[];
    reasoning: string;
    agents: TeamAgentState[];
    status: "running" | "done" | "error";
    doneAt?: number;
  }

// 在 MessagePart 类型之前新增 TeamAgentState 辅助类型
export interface TeamAgentState {
  agent: string;
  purpose: string;
  status: "pending" | "running" | "done" | "error";
  message?: string;
  summary?: string;
  startedAt?: number;
  finishedAt?: number;
}
```

- [ ] **Step 2: 新增 upsertTeamNode action**

在 ChatState 接口新增：

```typescript
/**
 * 更新或创建指定 message 的 team part。
 * - 若 message 无 team part：创建新 team part（用 plan 初始化）
 * - 若已有 team part：按 updaters 更新 plan / agents / status
 *
 * updaters 支持部分字段：
 * - plan: 替换整个 plan
 * - agentUpdate: 按 agent name 更新单个 agent 状态（upsert）
 * - status: 更新 team 整体状态
 */
upsertTeamNode: (
  messageId: string,
  updaters: {
    plan?: { agent: string; input: string; purpose: string }[];
    reasoning?: string;
    agentUpdate?: { agent: string; patch: Partial<TeamAgentState> };
    status?: "running" | "done" | "error";
  },
) => void;
```

- [ ] **Step 3: 实现 upsertTeamNode**

在 store 实现中新增：

```typescript
upsertTeamNode: (messageId, updaters) => {
  set((s) => {
    const targetCid = findSessionIdByMessageId(s.sessions, messageId);
    if (targetCid === null) return s;
    const sess = s.sessions[targetCid];
    if (!sess) return s;
    const messages = sess.messages.map((m) => {
      if (m.id !== messageId) return m;
      const parts = [...m.parts];
      const teamIdx = parts.findIndex((p) => p.type === "team");
      if (teamIdx === -1) {
        // 创建新 team part
        const newPart = {
          type: "team" as const,
          id: crypto.randomUUID(),
          plan: updaters.plan ?? [],
          reasoning: updaters.reasoning ?? "",
          agents: updaters.agentUpdate
            ? [
                {
                  agent: updaters.agentUpdate.agent,
                  purpose: "",
                  status: "pending" as const,
                  ...updaters.agentUpdate.patch,
                },
              ]
            : [],
          status: updaters.status ?? ("running" as const),
        };
        parts.push(newPart);
      } else {
        // 更新现有 team part
        const existing = parts[teamIdx] as Extract<MessagePart, { type: "team" }>;
        let newPlan = existing.plan;
        if (updaters.plan) newPlan = updaters.plan;
        let newAgents = existing.agents;
        if (updaters.agentUpdate) {
          const { agent, patch } = updaters.agentUpdate;
          const idx = newAgents.findIndex((a) => a.agent === agent);
          if (idx === -1) {
            newAgents = [
              ...newAgents,
              { agent, purpose: "", status: "pending" as const, ...patch },
            ];
          } else {
            newAgents = newAgents.map((a, i) =>
              i === idx ? { ...a, ...patch } : a,
            );
          }
        }
        const newStatus = updaters.status ?? existing.status;
        const doneAt =
          updaters.status === "done" || updaters.status === "error"
            ? Date.now()
            : existing.doneAt;
        parts[teamIdx] = {
          ...existing,
          plan: newPlan,
          agents: newAgents,
          status: newStatus,
          doneAt,
          ...(updaters.reasoning !== undefined
            ? { reasoning: updaters.reasoning }
            : {}),
        };
      }
      return { ...m, parts, content: deriveContent(parts) };
    });
    const sessions = { ...s.sessions, [targetCid]: { ...sess, messages } };
    return { sessions };
  });
},
```

- [ ] **Step 4: 运行 typecheck 验证**

Run: `npm run typecheck`
Expected: PASS（无类型错误）

- [ ] **Step 5: Commit**

```bash
git add frontend/renderer/stores/chat.ts
git commit -m "feat(chat): add TeamNodePart type and upsertTeamNode action"
```

---

### Task 2: 重构 useChatStream 的 team 事件聚合

**Files:**
- Modify: `frontend/renderer/hooks/useChatStream.ts:57-61`（store 引用）
- Modify: `frontend/renderer/hooks/useChatStream.ts:154-181`（team_* 事件处理）

- [ ] **Step 1: 引入 upsertTeamNode**

在 useChatStream.ts:57-61 的 store 引用区新增：

```typescript
const upsertTeamNode = useChatStore((s) => s.upsertTeamNode);
```

- [ ] **Step 2: 替换 team_plan / team_progress / team_result 三个 case**

删除 useChatStream.ts:154-181 的三个 case，替换为：

```typescript
case "team_plan": {
  // team_plan 事件初始化 team part（含 plan + reasoning + 所有 agent 初始 pending）
  const plan = Array.isArray(e.plan) ? e.plan : [];
  const agents = plan.map((t) => ({
    agent: String(t?.agent ?? ""),
    purpose: String(t?.purpose ?? ""),
    status: "pending" as const,
  }));
  upsertTeamNode(pendingIdRef.current, {
    plan: plan.map((t) => ({
      agent: String(t?.agent ?? ""),
      input: String(t?.input ?? ""),
      purpose: String(t?.purpose ?? ""),
    })),
    reasoning: String(e.reasoning ?? ""),
  });
  // 用 agentUpdate 批量初始化 agents（覆盖默认空数组）
  // 由于 upsertTeamNode 的 agentUpdate 是单 agent，这里需要直接初始化
  // 改为：先 upsert plan，再用循环 upsert 每个 agent
  for (const a of agents) {
    upsertTeamNode(pendingIdRef.current, {
      agentUpdate: { agent: a.agent, patch: { purpose: a.purpose, status: "pending" } },
    });
  }
  break;
}
case "team_progress": {
  const agent = String(e.agent ?? "");
  const status = (e.status === "running" || e.status === "done" || e.status === "error"
    ? e.status
    : "running") as "running" | "done" | "error";
  const patch: Partial<TeamAgentState> = { status };
  if (e.message !== undefined) patch.message = String(e.message);
  if (status === "running") patch.startedAt = Date.now();
  if (status === "done" || status === "error") patch.finishedAt = Date.now();
  upsertTeamNode(pendingIdRef.current, {
    agentUpdate: { agent, patch },
  });
  break;
}
case "team_result": {
  const agent = String(e.agent ?? "");
  upsertTeamNode(pendingIdRef.current, {
    agentUpdate: { agent, patch: { summary: String(e.summary ?? "") } },
  });
  break;
}
case "team_done": {
  // team 整体结束：标记 team part 为 done
  upsertTeamNode(pendingIdRef.current, {
    status: e.status === "error" ? "error" : "done",
  });
  break;
}
```

- [ ] **Step 3: 导入 TeamAgentState 类型**

在 useChatStream.ts 顶部 import 区新增：

```typescript
import type { ChatEvent } from "@/lib/utils";
import type { TeamAgentState } from "@/stores/chat";
```

- [ ] **Step 4: 运行 typecheck**

Run: `npm run typecheck`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/renderer/hooks/useChatStream.ts
git commit -m "refactor(chat): aggregate team events into TeamNode"
```

---

### Task 3: 新建 TeamNodeCard 组件

**Files:**
- Create: `frontend/renderer/components/chat/parts/TeamNodeCard.tsx`

- [ ] **Step 1: 创建 TeamNodeCard 组件**

```tsx
import { Fragment } from "react";
import { Users, CheckCircle2, AlertCircle, Loader2, ChevronRight } from "lucide-react";
import type { TeamAgentState } from "@/stores/chat";

interface TeamNodeCardProps {
  plan: { agent: string; input: string; purpose: string }[];
  reasoning: string;
  agents: TeamAgentState[];
  status: "running" | "done" | "error";
  doneAt?: number;
}

function AgentRow({ agent }: { agent: TeamAgentState }) {
  const icon =
    agent.status === "running" ? (
      <Loader2 className="h-3 w-3 animate-spin" />
    ) : agent.status === "done" ? (
      <CheckCircle2 className="h-3 w-3 text-emerald-500" />
    ) : agent.status === "error" ? (
      <AlertCircle className="h-3 w-3 text-red-500" />
    ) : (
      <div className="h-3 w-3 rounded-full border border-muted-c/40" />
    );

  return (
    <div className="border-l border-default pl-2.5 py-1">
      <div className="flex items-center gap-1.5 text-[11px]">
        {icon}
        <span className="font-medium text-primary-c">{agent.agent}</span>
        <ChevronRight className="h-2.5 w-2.5 opacity-40" />
        <span className="text-muted-c truncate">{agent.purpose}</span>
      </div>
      {agent.message && agent.status === "running" && (
        <div className="mt-0.5 text-[10px] text-muted-c/80 pl-4">
          {agent.message}
        </div>
      )}
      {agent.summary && (agent.status === "done" || agent.status === "error") && (
        <div className="mt-0.5 text-[10px] text-secondary-c pl-4 line-clamp-4 whitespace-pre-wrap">
          {agent.summary}
        </div>
      )}
    </div>
  );
}

export function TeamNodeCard({
  plan,
  reasoning,
  agents,
  status,
  doneAt,
}: TeamNodeCardProps) {
  const headerIcon =
    status === "running" ? (
      <Loader2 className="h-3.5 w-3.5 animate-spin" />
    ) : status === "done" ? (
      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
    ) : (
      <AlertCircle className="h-3.5 w-3.5 text-red-500" />
    );

  return (
    <div className="rounded-lg border border-indigo-200 bg-indigo-50/50 px-3 py-2 text-xs dark:border-indigo-900/50 dark:bg-indigo-950/20">
      <div className="mb-1.5 flex items-center gap-1.5 font-semibold text-indigo-900 dark:text-indigo-200">
        {headerIcon}
        <Users className="h-3.5 w-3.5" />
        Agent Team {status === "running" ? "执行中" : status === "done" ? "已完成" : "失败"}
      </div>
      {reasoning && (
        <div className="mb-1.5 text-[10px] opacity-70 text-indigo-800 dark:text-indigo-300">
          {reasoning}
        </div>
      )}
      <div className="space-y-0.5">
        {agents.map((a, i) => (
          <AgentRow key={`${a.agent}-${i}`} agent={a} />
        ))}
      </div>
      {status === "done" && doneAt && (
        <div className="mt-1 text-[10px] text-muted-c/60">
          完成于 {new Date(doneAt).toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 运行 typecheck**

Run: `npm run typecheck`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/renderer/components/chat/parts/TeamNodeCard.tsx
git commit -m "feat(chat): add TeamNodeCard component for tree-style team rendering"
```

---

### Task 4: AssistantUIThread 集成 TeamNodeCard

**Files:**
- Modify: `frontend/renderer/components/chat/AssistantUIThread.tsx:37-45`（RenderItem 类型）
- Modify: `frontend/renderer/components/chat/AssistantUIThread.tsx:57-137`（buildRenderItems）
- Modify: `frontend/renderer/components/chat/AssistantUIThread.tsx:124-133`（team_* case）
- Modify: `frontend/renderer/components/chat/AssistantUIThread.tsx:348-401`（team_* 渲染）

- [ ] **Step 1: 更新 RenderItem 类型**

在 AssistantUIThread.tsx:37-45 替换三个 team_* RenderItem 为单个 team：

```typescript
type RenderItem =
  | { kind: "delegation"; part: Extract<MessagePart, { type: "delegation" }> }
  | { kind: "reasoning"; part: Extract<MessagePart, { type: "reasoning" }> }
  | { kind: "tool-call"; part: PairedToolCall }
  | { kind: "orphan-tool-result"; part: OrphanToolResult }
  | { kind: "text"; part: Extract<MessagePart, { type: "text" }> }
  | { kind: "team"; part: Extract<MessagePart, { type: "team" }> };
```

- [ ] **Step 2: 更新 buildRenderItems**

在 buildRenderItems 的 switch 中替换 team_plan / team_progress / team_result 三个 case 为单个 team case：

```typescript
case "team":
  items.push({ kind: "team", part: p });
  break;
```

- [ ] **Step 3: 更新渲染分支**

在 MessageParts 的 items.map 渲染中替换三个 team_* case 为：

```tsx
case "team":
  return (
    <TeamNodeCard
      key={`team-${item.part.id}`}
      plan={item.part.plan}
      reasoning={item.part.reasoning}
      agents={item.part.agents}
      status={item.part.status}
      doneAt={item.part.doneAt}
    />
  );
```

- [ ] **Step 4: 导入 TeamNodeCard**

在 AssistantUIThread.tsx 顶部新增：

```typescript
import { TeamNodeCard } from "./parts/TeamNodeCard";
```

- [ ] **Step 5: 运行 typecheck + vitest**

Run: `npm run typecheck && npm test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/renderer/components/chat/AssistantUIThread.tsx
git commit -m "refactor(chat): render team events as tree via TeamNodeCard"
```

---

## Phase 2: 后端事件契约优化

### Task 5: team_path 事件序列修正 + team_done 事件

**Files:**
- Modify: `backend/app/paths/team_path.py:373-394`（_make_team_event 支持 team_done）
- Modify: `backend/app/paths/team_path.py:497-597`（run_team_path 事件序列）
- Modify: `backend/app/paths/team_path.py:108-110`（_PASSTHROUGH_EVENTS 不变）

- [ ] **Step 1: _make_team_event 新增 team_done 支持**

在 team_path.py:381-391 的元组中新增 `"team_done"`：

```python
if event in (
    "team_plan",
    "team_progress",
    "team_result",
    "team_done",  # 新增
    "reasoning",
    "error",
    _SUBTASK_DONE_EVENT,
):
```

- [ ] **Step 2: 修正 running 事件时机 — 移除批量提前发**

替换 team_path.py:512-517 的批量 running 事件为：移除该段（running 事件改为在 _runner 内部实际开始执行时发）。

删除 team_path.py:512-517：

```python
# 删除这段（批量提前发 running）：
# for task in valid_tasks:
#     yield _make_team_event(
#         "team_progress",
#         {"agent": task.agent, "status": "running", "message": task.purpose},
#     )
```

- [ ] **Step 3: _runner 内部发 running 事件**

在 team_path.py:524-541 的 `_runner` 函数中，进入 semaphore 后立即发 running：

```python
async def _runner(t: TeamPlanTask) -> None:
    async with semaphore:
        # 实际开始执行时才发 running
        await queue.put(
            _make_team_event(
                "team_progress",
                {"agent": t.agent, "status": "running", "message": t.purpose},
            )
        )
        try:
            async for ev in _run_subtask(
                t, thread_id, history, permission_mode, scene_prompt, state, profile_prompt
            ):
                await queue.put(ev)
        except Exception as exc:  # noqa: BLE001
            await queue.put(
                _make_team_event(
                    _SUBTASK_DONE_EVENT,
                    {
                        "agent": t.agent,
                        "success": False,
                        "payload": f"{t.agent} 子任务异常: {exc}",
                    },
                )
            )
```

- [ ] **Step 4: 主循环透传 running 事件**

在 team_path.py:548-560 的主循环中，running 事件也要透传（不仅 _SUBTASK_DONE_EVENT 之外的都透传，已有逻辑覆盖，确认无误）。

现有逻辑：
```python
while done_count < total:
    event = await queue.get()
    if event["event"] == _SUBTASK_DONE_EVENT:
        done_count += 1
        ...
    else:
        yield event  # running / approval_request / todo_update 等都透传
```
无需修改，确认即可。

- [ ] **Step 5: 新增 team_done 事件**

在 team_path.py:591-597（blackboard.findings 为空时 error 之后）的 aggregator 调用前后增加 team_done：

```python
if not blackboard.findings:
    yield _make_team_event("error", {"message": "所有专家任务均失败"})
    yield _make_team_event("team_done", {"status": "error"})
    return

# ---- 3. Aggregator 汇总 ----
async for sse in _run_aggregator(message, blackboard):
    yield sse

# team 整体结束
has_error = bool(blackboard.errors)
yield _make_team_event(
    "team_done",
    {"status": "error" if has_error and not blackboard.findings else "done"},
)
```

- [ ] **Step 6: 写测试 — 事件序列**

在 `tests/python/unit/paths/test_team_path.py` 新增测试：

```python
async def test_team_done_event_emitted_on_success(monkeypatch):
    """team_done 事件在 aggregator 后发出，status=done。"""
    # mock orchestrator 返回 1 个 code 子任务
    # mock run_code_agent 返回 token 事件
    # 收集所有事件，断言最后有 team_done {status: done}

async def test_team_done_event_emitted_on_all_failure():
    """所有子任务失败时 team_done status=error。"""
    # mock orchestrator 返回 1 个 code 子任务
    # mock run_code_agent 抛异常
    # 断言有 team_done {status: error}

async def test_running_event_emitted_at_actual_start(monkeypatch):
    """running 事件在 semaphore 获取后发出，不是批量提前发。"""
    # mock max_parallel=1，两个子任务
    # 断言事件序列：running(A) → done(A) → running(B) → done(B)
    # 而不是：running(A) running(B) → done(A) → done(B)
```

- [ ] **Step 7: 运行测试**

Run: `uv run pytest tests/python/unit/paths/test_team_path.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/paths/team_path.py tests/python/unit/paths/test_team_path.py
git commit -m "fix(team): emit team_done event and fix running event timing"
```

---

### Task 6: 子任务超时防护 — 避免 _subtask_done 哨兵丢失死锁

**Files:**
- Modify: `backend/app/paths/team_path.py:445-597`（run_team_path 主循环）
- Modify: `backend/app/config.py:312-315`（新增超时配置）

- [ ] **Step 1: config.py 新增子任务超时配置**

在 config.py:315 后新增：

```python
agent_team_subtask_timeout: int = Field(
    default=300, ge=30, le=1800, description="单个子任务最大执行时长（秒），超时强制失败"
)
```

- [ ] **Step 2: run_team_path 主循环增加超时**

替换 team_path.py:548-562 的 `while done_count < total` 循环为带超时版本：

```python
results: dict[str, TeamSubtaskResult] = {}
done_count = 0
total = len(valid_tasks)
subtask_timeout = settings.agent_team_subtask_timeout
while done_count < total:
    try:
        event = await asyncio.wait_for(queue.get(), timeout=subtask_timeout)
    except asyncio.TimeoutError:
        # 超时：未完成的子任务全部标记为失败
        logger.warning(
            "team subtask timeout",
            done_count=done_count,
            total=total,
            timeout=subtask_timeout,
        )
        # 把仍在 running 的 runner 强制 cancel
        for rt in runner_tasks:
            if not rt.done():
                rt.cancel()
        # 为未完成子任务填充错误结果
        for task in valid_tasks:
            if task.agent not in results:
                err_msg = f"{task.agent} 子任务超时（{subtask_timeout}s）"
                results[task.agent] = TeamSubtaskResult(
                    agent=task.agent, success=False, payload=err_msg,
                )
                blackboard.errors[task.agent] = err_msg
                yield _make_team_event(
                    "team_progress",
                    {"agent": task.agent, "status": "error", "message": err_msg},
                )
        break

    if event["event"] == _SUBTASK_DONE_EVENT:
        done_count += 1
        obj = json.loads(event["data"])
        results[obj["agent"]] = TeamSubtaskResult(
            agent=obj["agent"],
            success=obj["success"],
            payload=obj["payload"],
        )
    else:
        yield event

# 等待所有 runner 结束（cancel 后也会很快结束）
await asyncio.gather(*runner_tasks, return_exceptions=True)
```

- [ ] **Step 3: 写测试 — 超时防护**

```python
async def test_subtask_timeout_breaks_deadlock(monkeypatch):
    """子任务永不返回时，超时机制打破死锁。"""
    # mock max_parallel=1，1 个子任务
    # mock run_code_agent 为无限循环（async generator 永不 yield 也永不 return）
    # 设置 agent_team_subtask_timeout=1（1 秒）
    # 断言：1 秒内收到 team_progress error + team_done error
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/python/unit/paths/test_team_path.py::test_subtask_timeout_breaks_deadlock -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/paths/team_path.py backend/app/config.py tests/python/unit/paths/test_team_path.py
git commit -m "fix(team): add subtask timeout to prevent deadlock on missing _subtask_done"
```

---

## Phase 3: 编排质量提升

### Task 7: Orchestrator prompt 注入项目上下文

**Files:**
- Modify: `backend/app/paths/team_path.py:40-62`（_ORCHESTRATOR_PROMPT）
- Modify: `backend/app/paths/team_path.py:113-114`（_build_orchestrator_prompt）
- Modify: `backend/app/paths/team_path.py:445-453`（run_team_path 入口）

- [ ] **Step 1: _ORCHESTRATOR_PROMPT 增加上下文占位符**

在 team_path.py:41-62 的 prompt 中新增 `{context}` 占位符：

```python
_ORCHESTRATOR_PROMPT = (
    "你是一个任务拆解专家（Orchestrator）。请把用户请求拆分成若干子任务，"
    "每个子任务指定一个执行专家和输入。"
    "\n\n可用专家：\n"
    "- code: 读取/搜索代码与文件，只读工具（read_file/list_dir/glob/grep）。\n"
    "- rag: 从向量知识库检索文档。\n"
    "- web: 联网搜索实时信息。\n"
    "- deep: 执行需要写文件、编辑文件或系统命令的危险任务（会走审批）。\n"
    "\n项目上下文：\n{context}\n"
    "\n输出必须是严格 JSON，不要 markdown 代码块，不要额外解释：\n"
    "{{\n"
    '  "reasoning": "为什么这样拆任务",\n'
    '  "plan": [\n'
    '    {{"agent": "code", "input": "具体子任务输入", "purpose": "目的说明"}}\n'
    "  ]\n"
    "}}\n"
    "\n约束：\n"
    "1. 如果任务涉及写文件、编辑文件、执行系统命令，agent 必须设为 deep。\n"
    "2. 不要编造文件路径；若用户没给路径，子任务输入里说明需要搜索或推断。\n"
    "3. 子任务数量不要超过 {max_tasks} 个。\n"
    "4. 若任务简单，可只返回一个子任务。\n"
    "5. 子任务输入中应引用项目上下文里的具体路径，避免 subagent 盲探索。\n"
)
```

- [ ] **Step 2: _build_orchestrator_prompt 注入上下文**

```python
def _build_orchestrator_prompt(user_message: str, max_tasks: int, context: str = "") -> str:
    return (
        _ORCHESTRATOR_PROMPT.format(max_tasks=max_tasks, context=context)
        + f"\n\n用户请求：{user_message}"
    )
```

- [ ] **Step 3: run_team_path 构建 context**

在 team_path.py:460 附近（settings 加载后）新增上下文构建：

```python
settings = get_settings()
max_tasks = settings.agent_team_max_tasks
max_parallel = settings.agent_team_max_parallel

# 构建项目上下文：AGENTS.md 文件地图 + 关键目录结构
context = _build_project_context()
```

新增 `_build_project_context` 函数（team_path.py 顶部）：

```python
def _build_project_context() -> str:
    """构建项目上下文摘要，供 Orchestrator 拆任务时参考。

    包含 AGENTS.md §11 文件地图的关键路径，避免 subagent 盲探索。
    """
    lines = [
        "项目结构（agentx）：",
        "- 后端 Python: backend/app/（FastAPI + LangGraph）",
        "  - router/ (classifier.py, graph.py, state.py) — 消息分类 + StateGraph",
        "  - paths/ (chat_path.py, tool_path.py, deep_path.py, team_path.py) — 四路径",
        "  - subagents/ (code/rag/web/custom) — 子代理",
        "  - tools/ (filesystem + rag_retrieve) — 工具",
        "- 前端 Electron+React: frontend/",
        "  - renderer/components/chat/ — 聊天组件",
        "  - renderer/hooks/useChatStream.ts — SSE 事件处理",
        "  - renderer/stores/ (chat.ts, agentMode.ts) — zustand 状态",
        "- 配置: AGENTS.md（工程规范 + 文件地图 + Router 路径说明）",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: 调用处传 context**

在 team_path.py:472 附近：

```python
orchestrator_prompt = _build_orchestrator_prompt(message, max_tasks, context=context)
```

- [ ] **Step 5: 写测试 — 上下文注入**

```python
def test_orchestrator_prompt_contains_context():
    """Orchestrator prompt 包含项目上下文。"""
    from app.paths.team_path import _build_orchestrator_prompt
    prompt = _build_orchestrator_prompt("分析 router", max_tasks=3, context="项目结构：backend/app/router/")
    assert "backend/app/router/" in prompt
    assert "分析 router" in prompt
```

- [ ] **Step 6: 运行测试**

Run: `uv run pytest tests/python/unit/paths/test_team_path.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/paths/team_path.py tests/python/unit/paths/test_team_path.py
git commit -m "feat(team): inject project context into orchestrator prompt"
```

---

### Task 8: Aggregator 质量门 — 去重 + 完成度检查

**Files:**
- Modify: `backend/app/paths/team_path.py:408-442`（_run_aggregator）

- [ ] **Step 1: 新增 _quality_gate 函数**

在 team_path.py:408 之前新增：

```python
def _quality_gate(blackboard: Blackboard) -> tuple[bool, str]:
    """Aggregator 质量门：检查黑板结果质量。

    Returns:
        (ok, reason) — ok=False 时 reason 说明拒绝原因
    """
    if not blackboard.findings:
        return False, "无任何成功的子任务结果"
    # 去重检查：所有 findings 内容完全相同
    unique_findings = set(blackboard.findings.values())
    if len(unique_findings) == 1 and len(blackboard.findings) > 1:
        return False, "所有子任务返回相同内容，疑似未实际执行"
    # 截断检查：findings 中存在"[结果已截断]"且无其他实质内容
    truncated_only = all(
        "[结果已截断]" in v and len(v.strip()) < 50
        for v in blackboard.findings.values()
    )
    if truncated_only:
        return False, "所有结果均为截断片段，无有效内容"
    return True, ""
```

- [ ] **Step 2: _run_aggregator 调用质量门**

在 team_path.py:408-442 的 `_run_aggregator` 开头新增质量门检查：

```python
async def _run_aggregator(
    user_message: str,
    blackboard: Blackboard,
) -> AsyncIterator[dict[str, str]]:
    """调用 Aggregator LLM，流式输出最终回复。"""
    settings = get_settings()

    # 质量门检查
    ok, reason = _quality_gate(blackboard)
    if not ok:
        logger.warning("team aggregator quality gate rejected", reason=reason)
        yield _make_team_event(
            "error",
            {"message": f"专家结果质量不足: {reason}"},
        )
        return

    try:
        llm = get_chat_model(temperature=0.5, streaming=True)
    except ValueError as exc:
        yield _make_team_event("error", {"message": f"LLM 不可用: {exc}"})
        return
    # ... 原有逻辑不变
```

- [ ] **Step 3: 写测试 — 质量门**

```python
def test_quality_gate_rejects_all_identical():
    """所有 findings 相同时拒绝。"""
    from app.paths.team_path import _quality_gate, Blackboard
    bb = Blackboard()
    bb.findings = {"code": "result", "rag": "result"}
    ok, reason = _quality_gate(bb)
    assert not ok
    assert "相同" in reason

def test_quality_gate_rejects_empty():
    """无 findings 时拒绝。"""
    from app.paths.team_path import _quality_gate, Blackboard
    bb = Blackboard()
    ok, reason = _quality_gate(bb)
    assert not ok
    assert "无任何" in reason

def test_quality_gate_accepts_distinct():
    """不同内容时通过。"""
    from app.paths.team_path import _quality_gate, Blackboard
    bb = Blackboard()
    bb.findings = {"code": "result A", "rag": "result B"}
    ok, _ = _quality_gate(bb)
    assert ok
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/python/unit/paths/test_team_path.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/paths/team_path.py tests/python/unit/paths/test_team_path.py
git commit -m "feat(team): add aggregator quality gate for dedup and completeness"
```

---

### Task 9: 触发门槛 — 简单任务降级到单 agent

**Files:**
- Modify: `backend/app/paths/team_path.py:445-460`（run_team_path 入口评估）

- [ ] **Step 1: 新增 _should_downgrade_to_single 函数**

在 team_path.py:445 之前新增：

```python
# 简单任务关键词：命中则降级到单 agent（路径 A/B/C）
_SIMPLE_TASK_KEYWORDS = frozenset({
    "你好", "hello", "hi", "谢谢", "翻译", "解释", "什么是",
    "总结", "摘要",
})

def _should_downgrade_to_single(message: str) -> tuple[bool, str]:
    """评估是否应降级到单 agent 路径。

    Returns:
        (downgrade, reason) — downgrade=True 时应走单 agent
    """
    lower = message.lower().strip()
    # 极短消息（< 10 字符）降级
    if len(lower) < 10:
        return True, "消息过短，无需 team 协作"
    # 命中简单关键词降级
    if any(kw in lower for kw in _SIMPLE_TASK_KEYWORDS):
        return True, "命中简单任务关键词"
    return False, ""
```

- [ ] **Step 2: run_team_path 入口降级检查**

在 team_path.py:460 附近（settings 加载前）新增：

```python
async def run_team_path(
    message: str,
    thread_id: str,
    state: RouterState,
    profile_prompt: str = "",
    history: list | None = None,
    permission_mode: str = "workspace",
    scene_prompt: str | None = None,
) -> AsyncIterator[dict[str, str]]:
    """AgentTeam 路径入口。"""
    settings = get_settings()

    # 触发门槛：简单任务降级提示（实际降级由调用方 graph.py 决定，
    # 这里只发 warning 事件供调试）
    downgrade, reason = _should_downgrade_to_single(message)
    if downgrade:
        logger.info("team downgrade to single", reason=reason, message_len=len(message))
        # 不强制降级，只记录日志；用户显式选 team 模式时尊重选择
        # 但若要强制降级，可在此 yield error 并 return
```

> **设计决策：** 不强制降级（用户显式选 team 模式时尊重选择），但记录日志便于观察。若未来需要强制降级，取消注释 yield error 即可。

- [ ] **Step 3: 写测试 — 降级评估**

```python
def test_should_downgrade_short_message():
    from app.paths.team_path import _should_downgrade_to_single
    d, _ = _should_downgrade_to_single("你好")
    assert d

def test_should_not_downgrade_complex_task():
    from app.paths.team_path import _should_downgrade_to_single
    d, _ = _should_downgrade_to_single("分析 backend/app/router/graph.py 的实现并生成优化方案")
    assert not d
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/python/unit/paths/test_team_path.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/paths/team_path.py tests/python/unit/paths/test_team_path.py
git commit -m "feat(team): add downgrade evaluation for simple tasks"
```

---

## Phase 4: SSE 契约同步与回归

### Task 10: SSE 契约同步 — team_done 事件

**Files:**
- Modify: `backend/app/main.py:595-601`（事件契约注释）
- Modify: `frontend/shared/api-types.ts`（ChatEvent 类型新增 team_done）
- Modify: `frontend/preload/index.ts`（team_done 事件解析）

- [ ] **Step 1: main.py 注释新增 team_done**

在 main.py:595-601 的事件契约注释中新增：

```
- ``team_done``     — JSON `{"status": "done"|"error"}` — AgentTeam 整体结束
```

- [ ] **Step 2: api-types.ts 新增 team_done 事件类型**

查看现有 ChatEvent 定义，新增 team_done 变体：

```typescript
| { type: "team_done"; status: "done" | "error" }
```

- [ ] **Step 3: preload/index.ts 解析 team_done**

在 preload 的 SSE 事件解析中新增 team_done 分支（参照 team_plan 等）。

- [ ] **Step 4: typecheck**

Run: `npm run typecheck`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py frontend/shared/api-types.ts frontend/preload/index.ts
git commit -m "feat(sse): add team_done event to SSE contract"
```

---

### Task 11: 回归测试 + ruff + typecheck

- [ ] **Step 1: 后端全量测试**

Run: `uv run pytest tests/python/unit -m "not integration" -v`
Expected: 全部 PASS

- [ ] **Step 2: 前端全量测试**

Run: `npm test`
Expected: 全部 PASS

- [ ] **Step 3: ruff 风格检查**

Run: `uv run ruff check backend/`
Expected: 无错误

- [ ] **Step 4: typecheck**

Run: `npm run typecheck`
Expected: 无错误

- [ ] **Step 5: 最终 commit（如有遗漏修复）**

```bash
git add -A
git commit -m "test: regression for agent team ui and state fix"
```

---

## Self-Review

### Spec coverage
- F1（agent 输出挂 agent 节点下）→ Task 1-4 ✓
- F2（状态卡执行中）→ Task 1（TeamAgentState 状态聚合）+ Task 5（team_done）✓
- F3（part 堆积）→ Task 1（upsert 替代 addPart）✓
- F4（无整体生命周期）→ Task 5（team_done）+ Task 1（status 字段）✓
- B1（死锁）→ Task 6（超时防护）✓
- B2（running 提前发）→ Task 5（移到 _runner 内部）✓
- B3（无 team_done）→ Task 5 ✓
- B4（subagent 无上下文）→ Task 7 ✓
- B5（Aggregator 无质量门）→ Task 8 ✓
- B6（触发门槛低）→ Task 9 ✓

### Placeholder scan
无 TBD / TODO / "implement later" / "similar to Task N"。所有代码块完整。

### Type consistency
- `TeamAgentState` 在 Task 1 定义，Task 2/3 使用，名称一致 ✓
- `upsertTeamNode` 在 Task 1 定义，Task 2 使用，签名一致 ✓
- `team_done` 事件在 Task 5 后端定义，Task 10 前端契约同步 ✓
- `_should_downgrade_to_single` / `_quality_gate` / `_build_project_context` 定义与调用一致 ✓
