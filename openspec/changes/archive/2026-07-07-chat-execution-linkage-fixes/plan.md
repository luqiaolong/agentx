# 聊天执行链路问题修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复用户从输入到点击发送后的完整执行链路中的 P0/P1 级缺陷，包括安全授权、上下文持久化、审批安全、可中断/可暂停、任务规划、子代理调用等。

**Architecture:** 在后端统一通过 LangGraph checkpointer 写回历史，使路径 A/B/D 具备跨轮记忆；把 workspace 从消息正文迁移到独立字段；强化 DeepAgent 审批流支持多危险工具全量审批；引入 `pause`/`resume` 端点替代单一 abort；前端按 thread_id 精确管理会话状态。

**Tech Stack:** FastAPI + LangGraph + LangChain + React + TypeScript + zustand

---

## 文件结构变更概览

- 修改：`backend/app/api/schemas.py` — 增加 `workspace_path` 字段，统一 `permission_mode` 枚举。
- 修改：`backend/app/api/chat.py` — 新增 `pause`/`resume` 端点，修正 done 事件与 abort 处理。
- 修改：`backend/app/router/graph.py` — 解析独立 `workspace_path`，统一在 `run_router` 末尾写回 checkpointer。
- 修改：`backend/app/chat/run.py` — 返回 assistant message 文本供写回 checkpointer。
- 修改：`backend/app/subagents/dispatch.py` — 路径 B 返回 collected assistant message，keyword fallback 默认 None。
- 修改：`backend/app/subagents/*_agent.py` — 子代理（code/rag/web/custom）写回 checkpoint 或返回完整 assistant message。
- 修改：`backend/app/deep/agent.py` — 危险工具全量审批、pause/resume 状态检查、取消响应。
- 修改：`backend/app/deep/approval.py` — 区分 abort_flow / cancel_approval，辅助函数扩展。
- 修改：`backend/app/deep/streaming.py` — 稳定 tool_call id 配对。
- 修改：`backend/app/team/scheduler.py` — 路径 D runner 响应 abort，token 透传。
- 修改：`backend/app/memory/context.py` — 截断时保持 tool_calls ↔ ToolMessage 配对。
- 修改：`frontend/renderer/lib/api/chat.ts` — 新增 pause/resume 调用，SSE 断开感知。
- 修改：`frontend/renderer/lib/shared/api-types.ts` — 同步新类型。
- 修改：`frontend/renderer/stores/chat/index.ts` — Session 增加 `permissionMode`、持久化任务状态。
- 修改：`frontend/renderer/components/chat/ChatComposer.tsx` — 从 Session 读取 permissionMode，移除 `<workspace>` 标签注入。
- 修改：`frontend/renderer/components/chat/ChatView.tsx` — 使用 thread_id 精确设置会话运行状态，支持 pause/resume UI。
- 修改：`frontend/renderer/hooks/useChatStream.ts` — done/error 按 thread_id 路由，error 清理 pending。
- 新增：`tests/python/unit/test_chat_persistence.py` — 验证 CHAT/SINGLE_TOOL/TEAM 路径写回 checkpoint。
- 新增：`tests/python/unit/test_deep_approval_multi_tools.py` — 验证多危险工具全量审批。
- 新增：`tests/python/unit/test_interrupt_resume.py` — 验证 pause/resume 状态机。
- 新增：`tests/renderer/chat-state.test.tsx` — 验证 done 事件按 thread_id 更新。
- 修改：`AGENTS.md` §13 — 同步文件路径与事件契约。

---

## Phase 1: P0 安全与核心稳定性

### Task 1: Workspace 字段化，移除消息正文解析（修复 P0-1.1）

**Files:**
- Modify: `backend/app/api/schemas.py:59-74`
- Modify: `backend/app/router/graph.py:239-265`
- Modify: `frontend/renderer/components/chat/ChatComposer.tsx:224-235`
- Modify: `frontend/renderer/lib/api/chat.ts:23-47`
- Test: `tests/python/unit/test_chat_endpoint.py`（新增用例）

- [ ] **Step 1: 修改 ChatRequest，增加 `workspace_path` 字段并统一 permission_mode**

```python
class ChatRequest(BaseModel):
    message: str = Field(..., description="用户消息（/reset 触发会话重置）")
    thread_id: str = Field(..., description="会话 ID")
    permission_mode: Literal["standard", "full_trust"] = Field(
        default="standard",
        description='权限模式：standard（审批流）或 full_trust（会话内全量放行）',
    )
    system_prompt: str | None = Field(default=None, description="可选场景 prompt")
    agent_mode: Literal["agent", "agent_team"] = Field(default="agent")
    workspace_path: str | None = Field(default=None, description="当前会话绑定的 workspace 绝对路径")
```

- [ ] **Step 2: 修改 run_router，从参数读取 workspace_path，停止解析 `<workspace>` 标签**

删除 `cleaned_message, workspace_path = _parse_workspace_tag(cleaned_message)` 及后续 `sandbox.authorize` 调用。

在函数签名增加 `workspace_path: str | None = None`，然后：

```python
if workspace_path:
    sandbox = get_sandbox()
    try:
        sandbox.authorize(thread_id, workspace_path, writable=True, source="chip")
    except ValueError as exc:
        logger.warning("workspace authorize failed", ...)
```

- [ ] **Step 3: 修改 ChatComposer，不再拼接 `<workspace>` 标签**

```typescript
const handleSubmit = () => {
  const content = input.trim();
  if (!content || isStreaming) return;
  onSend(content); // 不再拼接 workspace 标签
  setInput("");
  handleClosePicker();
};
```

- [ ] **Step 4: 修改 chat.send，透传 workspace_path**

```typescript
async function send(
  msg: { role: string; content: string },
  opts?: SendMessageOpts & { workspacePath?: string | null }
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: msg.content,
      thread_id: opts?.threadId ?? "",
      permission_mode: opts?.permissionMode ?? "standard",
      system_prompt: opts?.systemPrompt ?? null,
      agent_mode: opts?.agentMode ?? "agent",
      workspace_path: opts?.workspacePath ?? null,
    }),
  });
  // ...
}
```

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/python/unit/test_chat_endpoint.py -v`
Expected: PASS（包含新用例：验证 `<workspace>` 标签不再被解析）

Run: `npm test -- tests/renderer/chat-composer.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/schemas.py backend/app/router/graph.py frontend/renderer/components/chat/ChatComposer.tsx frontend/renderer/lib/api/chat.ts tests/python/unit/test_chat_endpoint.py tests/renderer/chat-composer.test.tsx
git commit -m "fix: move workspace_path to ChatRequest field and stop parsing message tag"
```

---

### Task 2: 路径 A/B/D 写回 checkpointer（修复 P0-3.1 / P0-6.1）

**Files:**
- Modify: `backend/app/router/graph.py:210-362`
- Modify: `backend/app/chat/run.py:21-81`
- Modify: `backend/app/subagents/dispatch.py:219-323`
- Modify: `backend/app/team/orchestrator.py:82-296`
- Modify: `backend/app/team/scheduler.py:51-194`
- Modify: `backend/app/memory/context.py`
- Create: `tests/python/unit/test_chat_persistence.py`

- [ ] **Step 1: 让 run_router 在路径结束后写回 checkpoint**

在 `run_router` 末尾增加：

```python
# 收集本次对话的 user + assistant 消息并写回 checkpointer
assistant_content_parts: list[str] = []
async for sse in path_generator:
    yield sse
    if sse.get("event") == "token":
        assistant_content_parts.append(str(sse.get("data", "")))
    # ...

assistant_content = "".join(assistant_content_parts).strip()
if assistant_content and checkpointer is not None:
    from langchain_core.messages import AIMessage, HumanMessage
    new_messages = [
        HumanMessage(content=cleaned_message),
        AIMessage(content=assistant_content),
    ]
    await _append_messages_to_checkpointer(checkpointer, thread_id, new_messages)
```

实现 `_append_messages_to_checkpointer`：

```python
async def _append_messages_to_checkpointer(checkpointer, thread_id: str, new_messages: list) -> None:
    config = {"configurable": {"thread_id": thread_id}}
    if hasattr(checkpointer, "aget"):
        checkpoint = await checkpointer.aget(config)
    else:
        checkpoint = checkpointer.get(config)
    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    messages = list(channel_values.get("messages", []))
    messages.extend(new_messages)
    new_channel_values = {**channel_values, "messages": messages}
    new_checkpoint = {**checkpoint, "channel_values": new_channel_values}
    if hasattr(checkpointer, "aput"):
        await checkpointer.aput(config, new_checkpoint, {"messages": "any"}, [])
    elif hasattr(checkpointer, "put"):
        checkpointer.put(config, new_checkpoint, {"messages": "any"}, [])
```

- [ ] **Step 2: 路径 B 需要聚合子代理输出为 assistant message**

在 `run_tool_path` 中，收集子代理产出的所有 `token` 事件，最后返回 assistant_content 给 `run_router`。可以在函数签名后增加一个内部生成器包装：

```python
async def _run_tool_path_collected(...):
    collected: list[str] = []
    async for sse in _inner_run_tool_path(...):
        if sse.get("event") == "token":
            collected.append(str(sse.get("data", "")))
        yield sse
    return "".join(collected)
```

或者让 `run_router` 在 `elif classification == "SINGLE_TOOL":` 分支自己收集 token。

- [ ] **Step 3: 路径 D 结束时把 Aggregator summary 写回 checkpoint**

在 `run_team_path` 末尾，生成 assistant_content 字符串：

```python
aggregator_text_parts: list[str] = []
async for sse in _run_aggregator(message, blackboard):
    yield sse
    if sse.get("event") == "token":
        aggregator_text_parts.append(str(sse.get("data", "")))

assistant_content = "".join(aggregator_text_parts).strip()
# run_router 负责写回，所以这里无需写回；但为了兼容直接在别处调用 run_team_path 的场景，可以提供可选参数。
```

- [ ] **Step 4: 运行 persistence 测试**

Run: `uv run pytest tests/python/unit/test_chat_persistence.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/router/graph.py backend/app/chat/run.py backend/app/subagents/dispatch.py backend/app/team/orchestrator.py backend/app/team/scheduler.py backend/app/memory/context.py tests/python/unit/test_chat_persistence.py
git commit -m "fix: persist CHAT/SINGLE_TOOL/TEAM messages into checkpointer"
```

---

### Task 3: 多危险工具全量审批（修复 P0-2.1）

**Files:**
- Modify: `backend/app/deep/agent.py:342-390`
- Modify: `backend/app/deep/approval.py:71-123`
- Create: `tests/python/unit/test_deep_approval_multi_tools.py`

- [ ] **Step 1: 修改 DeepAgent 危险工具处理，审批所有危险调用**

```python
if dangerous_calls:
    # 一次性 yield 所有危险工具的审批请求
    for tc in dangerous_calls:
        yield _make_approval_event(tc, thread_id, kind="dangerous_tool")

    decision = await _await_approval(...)
    if decision is None or not decision.approved:
        # 拒绝时，对每一个待审批的危险 tool_call 注入 ToolMessage 错误
        for tc in dangerous_calls:
            await _inject_tool_error_for_call(agent, config, tc, "用户拒绝执行危险操作")
        yield make_sse_event("error", "用户拒绝执行危险操作")
        sandbox.set_full_trust(thread_id, False)
        return
```

新增 `_inject_tool_error_for_call`：

```python
async def _inject_tool_error_for_call(agent, config, tool_call, error_text):
    from langchain_core.messages import ToolMessage
    tc_id = tool_call.get("id") or str(uuid4())
    tool_msg = ToolMessage(content=error_text, tool_call_id=tc_id)
    await agent.aupdate_state(config, {"messages": [tool_msg]})
```

- [ ] **Step 2: 修改 _make_approval_event 支持多个 tool 预览聚合（可选）**

如果一次审批多个工具，当前仍单个事件 yield 多次。前端 `ApprovalDialog` 需要能处理队列，因此保持多个事件。或新增 `multi_approval_request` 事件（需改前端）。这里保守方案：保持多个 approval_request 事件串行等待同一个 decision。

- [ ] **Step 3: 运行测试**

Run: `uv run pytest tests/python/unit/test_deep_approval_multi_tools.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/deep/agent.py backend/app/deep/approval.py tests/python/unit/test_deep_approval_multi_tools.py
git commit -m "fix: require approval for all pending dangerous tool calls"
```

---

### Task 4: 可中断 LLM 流与路径 D 中止（修复 P0-2.2 / P0-2.3 / P1-2.6）

**Files:**
- Modify: `backend/app/chat/run.py:64-80`
- Modify: `backend/app/deep/agent.py:296-306`
- Modify: `backend/app/team/scheduler.py:180-205`
- Modify: `backend/app/approval/state.py`
- Modify: `backend/app/utils/sse_events.py`（如需要定义 abort 事件）
- Create: `tests/python/unit/test_interrupt_stream.py`

- [ ] **Step 1: 在 approval.state 中增加 `is_aborted` 的轮询友好检查 + 事件通知**

保持现有 `set/is/clear_abort`，增加一个 `asyncio.Event` 机制：

```python
_abort_events: dict[str, asyncio.Event] = {}

def set_abort(thread_id: str) -> None:
    _abort_flags[thread_id] = True
    event = _abort_events.get(thread_id)
    if event:
        event.set()

def get_abort_event(thread_id: str) -> asyncio.Event:
    if thread_id not in _abort_events:
        _abort_events[thread_id] = asyncio.Event()
    return _abort_events[thread_id]
```

- [ ] **Step 2: 在路径 A 的 LLM astream 中响应 abort**

```python
abort_event = get_abort_event(thread_id)
async for chunk in llm.astream(messages):
    if abort_event.is_set():
        raise asyncio.CancelledError("aborted")
    raw = extract_chunk_text(chunk, strip=False)
    cleaned = think_filter.feed(raw)
    # ...
```

- [ ] **Step 3: 在路径 D 的 `_runner` 中检查 abort**

```python
async def _runner(t, idx):
    async with semaphore:
        abort_event = get_abort_event(thread_id)
        if abort_event.is_set():
            await queue.put(make_team_event(_SUBTASK_DONE_EVENT, {"agent": t.agent, "success": False, "payload": "用户中止"}))
            return
        # ...
        async for ev in _run_subtask(...):
            if abort_event.is_set():
                break
            await queue.put(ev)
```

- [ ] **Step 4: 路径 D 子任务 token 透传**

在 `_PASSTHROUGH_EVENTS` 中加入 `"token"`：

```python
_PASSTHROUGH_EVENTS: frozenset[str] = frozenset(
    {"approval_request", "todo_update", "delegation", "tool_call", "tool_result", "reasoning", "token"}
)
```

注意 deep 子任务的 token 会被 `_run_subtask` 收集到 `collected_text`，透传不影响汇总。

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/python/unit/test_interrupt_stream.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/chat/run.py backend/app/deep/agent.py backend/app/team/scheduler.py backend/app/approval/state.py tests/python/unit/test_interrupt_stream.py
git commit -m "fix: make LLM streams and team subtasks responsive to abort"
```

---

### Task 5: 暂停/恢复端点（修复 P0-4.1 / P0-4.2）

**Files:**
- Create: `backend/app/api/chat_pause.py`（或扩展 `backend/app/api/chat.py`）
- Modify: `backend/app/api/chat.py`
- Modify: `backend/app/approval/state.py`
- Modify: `backend/app/deep/agent.py:296-422`
- Modify: `frontend/renderer/lib/api/chat.ts`
- Modify: `frontend/renderer/components/chat/ChatView.tsx`
- Modify: `frontend/renderer/hooks/useChatStream.ts`
- Create: `tests/python/unit/test_interrupt_resume.py`

- [ ] **Step 1: 增加 pause/resume 状态管理**

在 `app/approval/state.py` 中增加：

```python
_pause_flags: dict[str, bool] = {}
_pause_events: dict[str, asyncio.Event] = {}

def set_pause(thread_id: str) -> None:
    _pause_flags[thread_id] = True
    event = _pause_events.get(thread_id)
    if event:
        event.set()

def clear_pause(thread_id: str) -> None:
    _pause_flags.pop(thread_id, None)
    event = _pause_events.pop(thread_id, None)
    if event:
        event.set()

def is_paused(thread_id: str) -> bool:
    return _pause_flags.get(thread_id, False)

def get_pause_event(thread_id: str) -> asyncio.Event:
    if thread_id not in _pause_events:
        _pause_events[thread_id] = asyncio.Event()
    return _pause_events[thread_id]
```

- [ ] **Step 2: 在 chat.py 注册 pause/resume 端点**

```python
@app.post("/api/chat/pause")
async def chat_pause(req: AbortRequest) -> dict[str, Any]:
    from app.approval import set_pause
    set_pause(req.thread_id)
    return {"ok": True}

@app.post("/api/chat/resume")
async def chat_resume(req: AbortRequest) -> dict[str, Any]:
    from app.approval import clear_pause
    clear_pause(req.thread_id)
    return {"ok": True}
```

- [ ] **Step 3: 修改 DeepAgent 在 LLM 流中响应 pause**

在 `_stream_agent_events` 调用点（`agent.astream`）和 resume 循环中，检查 `is_paused`。若 paused，yield `paused` 事件后退出循环，保留 interrupt 状态。resume 时重新进入循环。

简化实现：在 `run_deep_path` 的 while 循环开头检查 `is_paused`：

```python
while iteration < max_iterations:
    if is_paused(thread_id):
        yield make_sse_event("paused", "{}")
        # 等待 resume_event
        await get_pause_event(thread_id).wait()
        if not is_paused(thread_id):
            get_pause_event(thread_id).clear()
    # ...
```

注意：pause 时不能清理 `_pending_approvals` 或 `_abort_flags`，只 cancel LLM 流。

- [ ] **Step 4: 前端支持 pause/resume**

`chat.ts` 增加：

```typescript
async function pause(threadId: string): Promise<void> {
  await fetch(`${API_BASE}/api/chat/pause`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ thread_id: threadId }) });
}
async function resume(threadId: string): Promise<void> {
  await fetch(`${API_BASE}/api/chat/resume`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ thread_id: threadId }) });
}
```

`ChatView` 中发送按钮改为显示暂停/继续图标（若 `isStreaming` 且 `isPaused` 则显示继续）。点击暂停调用 `chat.pause(threadId)`，点击继续调用 `chat.resume(threadId)`。

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/python/unit/test_interrupt_resume.py -v`
Expected: PASS

Run: `npm test -- tests/renderer/pause-resume.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/chat.py backend/app/approval/state.py backend/app/deep/agent.py frontend/renderer/lib/api/chat.ts frontend/renderer/components/chat/ChatView.tsx frontend/renderer/hooks/useChatStream.ts tests/python/unit/test_interrupt_resume.py tests/renderer/pause-resume.test.tsx
git commit -m "feat: add chat pause/resume endpoints and UI"
```

---

### Task 6: 前端会话状态按 thread_id 精确管理（修复 P0-7.1 / P1-7.2）

**Files:**
- Modify: `frontend/renderer/hooks/useChatStream.ts:69-238`
- Modify: `frontend/renderer/components/chat/ChatView.tsx:357-371`
- Create: `tests/renderer/chat-state.test.tsx`

- [ ] **Step 1: useChatStream 内部缓存 latest callbacks 与 thread_id**

```typescript
const threadIdRef = useRef<string | undefined>(undefined);
useEffect(() => { threadIdRef.current = threadId; }, [threadId]);

const callbacksRef = useRef({ setMessages, setTodos, setTasks, ... });
useEffect(() => { callbacksRef.current = { setMessages, setTodos, setTasks, ... }; }, [setMessages, setTodos, setTasks, ...]);
```

- [ ] **Step 2: done 事件按 thread_id 更新会话状态**

```typescript
if (event === "done") {
  const cid = threadIdRef.current ?? currentIdRef.current;
  if (cid) {
    callbacksRef.current.setSessionRunning(cid, false);
    callbacksRef.current.markReasoningDone(pendingIdRef.current);
  }
  pendingIdRef.current = null;
  return;
}
```

- [ ] **Step 3: error 事件清理 pending message 并停止会话运行**

```typescript
if (event === "error") {
  const cid = threadIdRef.current ?? currentIdRef.current;
  if (cid) {
    callbacksRef.current.setSessionRunning(cid, false);
  }
  if (pendingIdRef.current) {
    callbacksRef.current.markReasoningDone(pendingIdRef.current);
    // 可选：删除空 pending message 或标记为错误
    callbacksRef.current.deleteMessage(pendingIdRef.current);
    pendingIdRef.current = null;
  }
  callbacksRef.current.setErrorMsg?.(String(data));
}
```

- [ ] **Step 4: 运行测试**

Run: `npm test -- tests/renderer/chat-state.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/renderer/hooks/useChatStream.ts frontend/renderer/components/chat/ChatView.tsx tests/renderer/chat-state.test.tsx
git commit -m "fix: route SSE done/error by thread_id and clean up pending messages"
```

---

### Task 7: permissionMode 持久化到 Session（修复 P1-1.2 / P1-1.3）

**Files:**
- Modify: `frontend/renderer/stores/chat/index.ts`
- Modify: `frontend/renderer/components/chat/ChatComposer.tsx`
- Modify: `frontend/renderer/components/chat/ChatView.tsx`

- [ ] **Step 1: Session 类型增加 permissionMode 字段**

```typescript
export interface Session {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
  workspacePath?: string | null;
  permissionMode?: "standard" | "full_trust";
}
```

- [ ] **Step 2: 迁移 store 旧数据，默认 permissionMode="standard"**

在 zustand store 初始化/加载时，对没有 `permissionMode` 的 session 补默认值。

- [ ] **Step 3: ChatComposer 从当前 Session 读取 permissionMode**

```typescript
const currentSession = useChatStore((s) => s.sessions.find((x) => x.id === s.currentId));
const permissionMode = currentSession?.permissionMode ?? "standard";
// 不再使用 usePermissionStore().mode
```

- [ ] **Step 4: 修改 `moveSessionToWorkspace` 失败时回滚**

```typescript
moveSessionToWorkspace: async (sid, workspacePath) => {
  const oldPath = get().sessions.find((s) => s.id === sid)?.workspacePath;
  set((state) => ({ ... })); // 先更新路径
  try {
    await sandbox.authorize(sid, workspacePath);
  } catch {
    // 回滚
    set((state) => ({ ...oldPath }));
    throw new Error("workspace authorize failed");
  }
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/renderer/stores/chat/index.ts frontend/renderer/components/chat/ChatComposer.tsx frontend/renderer/components/chat/ChatView.tsx
git commit -m "fix: store permissionMode per session and roll back failed workspace auth"
```

---

## Phase 2: P1 体验与健壮性

### Task 8: SSE 断开感知与异常分类（修复 P1-4.5 / P1-7.4）

**Files:**
- Modify: `frontend/renderer/lib/api/chat.ts:53-110`
- Modify: `backend/app/api/chat.py:102-110`

- [ ] **Step 1: 前端 read 循环跟踪是否收到 done**

```typescript
let receivedDone = false;
// ...
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  // ...
  if (event === "done") receivedDone = true;
}
if (!receivedDone) {
  onError?.(new Error("连接中断，未收到完成事件"));
}
```

- [ ] **Step 2: 后端异常分类**

```python
except asyncio.TimeoutError as exc:
    logger.warning("chat timeout", ...)
    yield make_sse_event("error_retryable", "请求超时，请稍后重试")
except openai.RateLimitError as exc:
    yield make_sse_event("error_retryable", "模型限流，请稍后重试")
except asyncio.CancelledError:
    yield make_sse_event("error_fatal", "用户取消")
except Exception as exc:
    logger.exception("chat error")
    yield make_sse_event("error", f"发生错误: {exc}")
```

- [ ] **Step 3: Commit**

```bash
git add frontend/renderer/lib/api/chat.ts backend/app/api/chat.py
git commit -m "fix: detect SSE disconnect and classify backend errors"
```

---

### Task 9: DeepAgent 任务规划（修复 P1-5.1 / P1-5.2）

**Files:**
- Modify: `backend/app/deep/agent.py`
- Modify: `backend/app/deep/prompts/system.md`（如存在）或 `backend/app/deep/agent.py` 内嵌 prompt
- Modify: `backend/app/deep/streaming.py:103-113`
- Modify: `frontend/renderer/hooks/useChatStream.ts:147-173`
- Modify: `frontend/renderer/components/chat/TodoProgress.tsx`

- [ ] **Step 1: System prompt 要求输出结构化任务计划**

在 system prompt 末尾加入：

```markdown
对于需要多步执行的复杂任务，请先输出 JSON 计划，格式：
{"plan": [{"id": "1", "title": "读取文件", "status": "pending"}, ...]}
执行过程中每次完成一步输出：
{"plan_update": {"id": "1", "status": "done"}}
```

- [ ] **Step 2: 解析 plan 并生成 `plan` / `plan_update` SSE 事件**

在 `_stream_agent_events` 中检测 token 前缀 `{"plan":`，提取后 yield `plan` 事件；后续检测 `{"plan_update":` yield `plan_update` 事件。

- [ ] **Step 3: todo_update 增加 task_id**

```python
yield make_sse_event("todo_update", json.dumps({"task_id": deep_task_id, "title": f"调用工具: {tc_name}", "done": False}, ensure_ascii=False))
```

- [ ] **Step 4: 前端按 task_id 分组展示 todo**

TodoProgress 接收 `todos: Record<string, TodoItem[]>`，按 task_id 渲染分组列表。

- [ ] **Step 5: Commit**

```bash
git add backend/app/deep/agent.py backend/app/deep/streaming.py frontend/renderer/hooks/useChatStream.ts frontend/renderer/components/chat/TodoProgress.tsx
git commit -m "feat: structured task plan with task-scoped todo updates"
```

---

### Task 10: 历史截断保持 tool_calls 配对（修复 P1-3.3）

**Files:**
- Modify: `backend/app/memory/context.py:66-88`

- [ ] **Step 1: 截断后检查 AIMessage(tool_calls) 与 ToolMessage 配对**

```python
def _ensure_tool_call_pairing(messages: list) -> list:
    tool_call_ids = set()
    tool_msg_ids = set()
    for m in messages:
        if isinstance(m, AIMessage) and m.tool_calls:
            tool_call_ids.update(tc.get("id") for tc in m.tool_calls if tc.get("id"))
        elif isinstance(m, ToolMessage):
            tool_msg_ids.add(m.tool_call_id)
    missing = tool_call_ids - tool_msg_ids
    if missing:
        # 为每个缺失 id 追加错误占位 ToolMessage
        for tc_id in missing:
            messages.append(ToolMessage(content="上下文被截断，原工具结果不可用", tool_call_id=tc_id))
    return messages
```

- [ ] **Step 2: 在 trim_messages_with_budget 返回前调用 `_ensure_tool_call_pairing`**

- [ ] **Step 3: Commit**

```bash
git add backend/app/memory/context.py
git commit -m "fix: keep tool_calls/ToolMessage pairing after context trimming"
```

---

### Task 11: 自定义子代理工具校验统一（修复 P1-6.3）

**Files:**
- Modify: `backend/app/team/planner.py:260-262`

- [ ] **Step 1: 统一校验 custom agent 的工具启用状态**

```python
def _validate_task(cfg, t: TaskItem, tools_enabled: dict[str, bool]) -> None:
    # ...
    if t.agent == "custom":
        if not cfg.tools:
            raise ValueError(...)
        if not any(tools_enabled.get(tool, True) for tool in cfg.tools):
            raise ValueError(f"自定义子代理所有工具已被禁用: {cfg.tools}")
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/team/planner.py
git commit -m "fix: validate enabled tools for custom subagent"
```

---

### Task 12: 文档同步（修复 P2-7.6）

**Files:**
- Modify: `AGENTS.md` §13

- [ ] **Step 1: 修正 _event_generator 文件路径**

把 "`backend/app/main.py::_event_generator`" 改为 "`backend/app/api/chat.py::_event_generator`"，并补充新增路径 `/api/chat/pause`、`/api/chat/resume`。

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: update AGENTS.md chat execution paths"
```

---

## 自评

**1. Spec coverage:**
- P0-1.1 → Task 1
- P0-2.1 → Task 3
- P0-2.2/P0-2.3/P1-2.6 → Task 4
- P0-3.1/P0-6.1 → Task 2
- P0-4.1/P0-4.2 → Task 5
- P0-7.1/P1-7.2 → Task 6
- P1-1.2/P1-1.3 → Task 7
- P1-4.5/P1-7.4 → Task 8
- P1-5.1/P1-5.2 → Task 9
- P1-3.3 → Task 10
- P1-6.3 → Task 11
- P2-7.6 → Task 12

**2. Placeholder scan:** 无 TBD/TODO；每个 step 含具体文件与代码片段。

**3. Type consistency:** `permission_mode` 统一为 `Literal["standard", "full_trust"]`；`workspace_path` 统一为可选字符串；thread_id 透传保持一致。

---

## 执行方式

**Plan complete and saved to `docs/superpowers/plans/2026-07-07-chat-execution-linkage-fixes.md`.**

Two execution options:

1. **Subagent-Driven (recommended)** - dispatch fresh subagents per independent task group, review between tasks.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.