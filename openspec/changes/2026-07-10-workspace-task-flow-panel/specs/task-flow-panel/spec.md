# Spec: 任务面板任务流 + Todo 列表展示(task-flow-panel)

## 概述

右侧任务面板从"平铺单行摘要"升级为"任务流分组 + todo 列表 + 进度条"展示。激活 TaskTimeline 替换
CompactTaskList；Task 模型引入 parentTaskId / taskSource / agentRole 支持父子任务嵌套；后端 todo_update
SSE 事件扩展 source + parent_task_id 字段；聊天区 TodoProgress 数据源统一到 useTasksStore，消除双写。

## 包结构

```
backend/app/
├── sse/
│   └── events.py            ← 修改:make_todo_update_event 新增 source + parent_task_id 参数
├── team/
│   └── orchestrator.py      ← 修改:_emit_todo_in_progress 注入 source + parent_task_id
└── deepagent/
    └── streaming.py         ← 修改:主路径 todo_update 注入 source(work/coding)

frontend/
├── shared/
│   └── api-types.ts         ← 修改:todo_update 事件类型扩展 source? + parent_task_id?
└── renderer/
    ├── hooks/
    │   └── useChatStream.ts ← 修改:todo_update 处理逻辑支持子任务创建,删除 setTodos 双写
    ├── components/
    │   ├── chat/
    │   │   ├── ChatView.tsx     ← 修改:删除 useState<TodoItem[]>,从 store 派生 todos
    │   │   └── TodoProgress.tsx ← 修改:加进度条 + 过渡动画
    │   └── workspace/
    │       ├── WorkspacePanel.tsx ← 修改:CompactTaskList → TaskTimeline
    │       ├── TaskTimeline.tsx   ← 修改:sessionId 过滤 + 父子分组渲染
    │       └── CompactTaskList.tsx ← 删除:功能被 TaskTimeline 覆盖
    ├── stores/
    │   └── tasks.ts         ← 修改:Task 接口扩展,migrate v4→v5,version 4→5
    └── App.tsx              ← 修改:右侧面板可调宽(drag handle)
```

## 公共 API

### `sse/events.py` 变更

```python
# 修改:make_todo_update_event 新增 source + parent_task_id 参数
def make_todo_update_event(
    todos: list[dict[str, Any]],
    task_id: str | None = None,
    trace_id: str | None = None,
    source: str | None = None,           # 新增:任务来源(work/coding/code/deep/rag/web/团队角色)
    parent_task_id: str | None = None,   # 新增:父任务 ID(Team 子任务场景)
) -> dict[str, str]:
    """构造 todo_update SSE 事件(原生 deepagents Todo schema)。

    Args:
        todos: deepagents state.todos 原生列表,每项 {content, status}
        task_id: 任务分组标识(Team 多子任务场景注入 thread_id)
        trace_id: 观测中心 trace_id
        source: 任务来源(work/coding/code/deep/rag/web/团队角色),
                用于前端区分主任务与子任务
        parent_task_id: 父任务 ID(Team 子任务场景 = parent_thread_id),
                        用于前端建立父子任务关联

    Returns:
        SSE 事件 dict

    payload 格式::

        {
            "todos": [{"content": "读取文件", "status": "completed"}],
            "task_id": "thread-xxx",
            "source": "deep",              // 可选
            "parent_task_id": "thread-xxx"  // 可选
        }
    """
    payload: dict[str, Any] = {"todos": todos}
    if task_id is not None:
        payload["task_id"] = task_id
    if source is not None:
        payload["source"] = source
    if parent_task_id is not None:
        payload["parent_task_id"] = parent_task_id
    return make_sse_event("todo_update", payload, trace_id=trace_id)
```

### `team/orchestrator.py` 变更

```python
# 修改:_emit_todo_in_progress 注入 source + parent_task_id
def _emit_todo_in_progress(
    writer: Callable,
    todos: list[dict],
    task_index: int,
    parent_thread_id: str,
    agent_role: str,  # 新增:子任务 agent 角色(code/deep/rag/web/...)
) -> None:
    """子任务开始时标记对应 todo 为 in_progress 并发送 todo_update 事件。

    新增 source 和 parent_task_id 字段,供前端创建子任务并关联父任务。
    """
    updated = list(todos)
    if 0 <= task_index < len(updated):
        updated[task_index] = {**updated[task_index], "status": "in_progress"}
    writer(make_todo_update_event(
        updated,
        task_id=parent_thread_id,
        source=agent_role,              # 新增
        parent_task_id=parent_thread_id, # 新增
    ))
```

调用方(各子任务节点)需传入 `agent_role`:
- `_deep_node` → `agent_role="deep"`
- `_code_node` → `agent_role="code"`
- `_builtin_node` → `agent_role=task.agent`(rag/web)
- `_team_role_node` → `agent_role=task.agent`(frontend_dev/backend_dev/...)
- `_custom_node` → `agent_role=task.agent`

### `deepagent/streaming.py` 变更

**注**:`_stream_agent_events` 已有 `source` 参数(L46,默认 "deep"),`approval_runner.py` 已透传。
本变更只需在 `make_todo_update_event` 调用时传入已有的 `source` 变量。

```python
# 修改:主路径 todo_update 注入 source(复用已有 source 参数)
async def _stream_agent_events(...):
    # source 参数已存在(L46): source: str = "deep"
    # approval_runner 透传: work supervisor → "work", coding expert → "coding"
    _last_todos: list[dict] = []
    async for state in agent.astream(...):
        current_todos = state.get("todos", []) if hasattr(state, "get") else []
        if current_todos != _last_todos:
            await _obs("todo_update", {"todos": current_todos, "task_id": thread_id})
            yield make_todo_update_event(
                current_todos,
                task_id=thread_id,
                source=source,  # 新增:复用已有 source 参数
            )
            _last_todos = list(current_todos)
```

### 前端 `api-types.ts` 变更

```typescript
// todo_update 事件类型扩展(新增 source? + parent_task_id?)
| {
    type: "todo_update";
    todos: { content: string; status: TodoStatus; task_id?: string }[];
    task_id?: string;
    source?: string;           // 新增:任务来源
    parent_task_id?: string;   // 新增:父任务 ID
    trace_id?: string;
  }
```

### 前端 `stores/tasks.ts` 变更

```typescript
// Task 接口扩展
export interface Task {
  id: string;
  title: string;
  status: "pending" | "running" | "done" | "failed";
  todos?: { content: string; status: TodoStatus }[];
  createdAt: number;
  updatedAt?: number;
  sessionId: string;
  // 新增字段(可选)
  parentTaskId?: string;                          // 父任务 ID
  taskSource?: "work" | "coding" | "team";        // 任务来源场景
  agentRole?: string;                             // team 子任务的 agent 角色
}

// migrate v4 → v5(非破坏性,补默认值)
export function migrateTasksState(persisted: unknown, version: number): Partial<TasksState> {
  // ... 既有 v0→v1, v1→v2, v2→v3, v3→v4 ...
  if (version < 5) {
    tasks = tasks.map((t) => ({
      ...t,
      parentTaskId: typeof (t as any).parentTaskId === "string" ? (t as any).parentTaskId : undefined,
      taskSource: typeof (t as any).taskSource === "string" ? (t as any).taskSource : "work",
      agentRole: typeof (t as any).agentRole === "string" ? (t as any).agentRole : undefined,
    }));
  }
  // ...
}

// version: 4 → 5
{ name: "agentx-tasks", storage: ..., version: 5, migrate: migrateTasksState, ... }
```

### 前端 `useChatStream.ts` 变更

```typescript
// UseChatStreamArgs 删除 setTodos 参数
export interface UseChatStreamArgs {
  threadId?: string;
  activeThreadIdRef?: MutableRefObject<string | null>;
  pendingIdRef: MutableRefObject<string | null>;
  currentTaskIdRef: MutableRefObject<string | null>;
  lastUserQueryRef: MutableRefObject<string>;
  // setTodos: ...,  ← 删除
  setErrorMsg: (msg: string | null) => void;
  // ...
}

// todo_update 处理逻辑:支持子任务创建
case "todo_update": {
  const taskId = typeof e.task_id === "string" && e.task_id.length > 0 ? e.task_id : undefined;
  const parentTaskId = typeof e.parent_task_id === "string" && e.parent_task_id.length > 0
    ? e.parent_task_id : undefined;
  const source = typeof e.source === "string" ? e.source : undefined;
  const incoming = normalizeTodos(e.todos, taskId);

  // 删除:setTodos 调用(数据源统一到 store)

  if (parentTaskId && source) {
    // Team 子任务场景:创建/更新子任务
    const childTaskId = `${parentTaskId}-child-${source}`;
    const existing = useTasksStore.getState().tasks.find((t) => t.id === childTaskId);
    if (existing) {
      updateTask(childTaskId, { todos: incoming, status: "running" });
    } else {
      addTask({
        id: childTaskId,
        title: `${source} 子任务`,
        status: "running",
        todos: incoming,
        createdAt: Date.now(),
        sessionId: activeThreadIdRef?.current ?? currentIdRef.current ?? "",
        parentTaskId,
        taskSource: "team",
        agentRole: source,
      });
    }
  } else {
    // 主任务场景(原有逻辑)
    const tid = currentTaskIdRef.current;
    if (tid) {
      updateTask(tid, { todos: incoming });
    } else if (incoming.length > 0) {
      const newId = `task-${crypto.randomUUID()}`;
      currentTaskIdRef.current = newId;
      const rawQuery = lastUserQueryRef.current.replace(...).trim();
      const title = rawQuery.slice(0, 40) || "深度任务";
      const sessionId = activeThreadIdRef?.current ?? currentIdRef.current ?? "";
      addTask({
        id: newId, title, status: "running", todos: incoming,
        createdAt: Date.now(), sessionId,
        taskSource: source === "coding" ? "coding" : "work",
      });
    }
  }
  break;
}
```

### 前端 `ChatView.tsx` 变更

```typescript
// 删除:const [todos, setTodos] = useState<TodoItem[]>([]);
// 改为从 store 派生
const currentId = useChatStore((s) => s.currentId);
const todos = useTasksStore(useShallow((s) => {
  const sessionTasks = s.tasks.filter((t) => t.sessionId === currentId);
  const running = sessionTasks.find((t) => t.status === "running" && !t.parentTaskId);
  return running?.todos ?? [];
}));
const completedTodos = todos.filter((t) => t.status === "completed").length;

// 删除:setTodos([]) in handleSend
// 删除:useChatStream args 中的 setTodos

// TodoProgress 渲染不变(数据源变了)
{todos.length > 0 && (
  <TodoProgress todos={todos} completedTodos={completedTodos} />
)}
```

### 前端 `TaskTimeline.tsx` 变更

```typescript
// 1. sessionId 过滤修复
export function TaskTimeline() {
  const currentId = useChatStore((s) => s.currentId);
  const allTasks = useTasksStore((s) => s.tasks);
  const tasks = useMemo(
    () => allTasks.filter((t) => t.sessionId === currentId),
    [allTasks, currentId],
  );
  // ...
}

// 2. 父子分组渲染
export function TaskTimeline() {
  // ... 过滤后 ...
  const mainTasks = tasks.filter((t) => !t.parentTaskId);
  const childTasksByParent = useMemo(() => {
    const map = new Map<string, Task[]>();
    for (const t of tasks) {
      if (t.parentTaskId) {
        const list = map.get(t.parentTaskId) ?? [];
        list.push(t);
        map.set(t.parentTaskId, list);
      }
    }
    return map;
  }, [tasks]);

  const sorted = [...mainTasks].sort((a, b) => (b.createdAt ?? 0) - (a.createdAt ?? 0));

  return (
    <ul className="space-y-2">
      {sorted.map((t) => (
        <li key={t.id}>
          <TaskCard task={t} />
          {/* 嵌套渲染子任务 */}
          {childTasksByParent.has(t.id) && (
            <ul className="ml-4 mt-1 space-y-1 border-l border-default pl-2">
              {childTasksByParent.get(t.id)!.map((child) => (
                <TaskCard key={child.id} task={child} compact />
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}

// TaskCard 新增 compact 模式(子任务用更小缩进 + agentRole 标签)
function TaskCard({ task, compact }: { task: Task; compact?: boolean }) {
  // ... 既有逻辑 ...
  // compact 模式:显示 agentRole 标签(formatTaskLabel)
}
```

### 前端 `WorkspacePanel.tsx` 变更

```typescript
// import 替换
// import { CompactTaskList } from "./CompactTaskList";  ← 删除
import { TaskTimeline } from "./TaskTimeline";

// 渲染替换(L142-144)
<div className="flex-1 min-h-0 overflow-auto">
  <TaskTimeline />  {/* 替换 CompactTaskList */}
</div>
```

### 前端 `TodoProgress.tsx` 变更

```typescript
// 新增进度条(复用 TaskTimeline 的设计)
export function TodoProgress({ todos, completedTodos }: { todos: TodoItem[]; completedTodos: number }) {
  const progress = todos.length > 0 ? (completedTodos / todos.length) * 100 : 0;
  // ... 既有分组逻辑 ...
  return (
    <div className="mx-auto w-full max-w-3xl border-t border-default px-4 py-2.5">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="font-semibold uppercase tracking-wide text-muted-c">任务进度</span>
        <span className="text-muted-c">{completedTodos}/{todos.length}</span>
      </div>
      {/* 新增:进度条 */}
      <div className="mb-2 h-1 overflow-hidden rounded-full bg-subtle">
        <div
          className="h-full rounded-full bg-brand-500 transition-all duration-300"
          style={{ width: `${progress}%` }}
        />
      </div>
      {/* ... 既有 TodoList 渲染 ... */}
    </div>
  );
}
```

### 前端 `App.tsx` 变更

```typescript
// 右侧面板可调宽(手写 drag handle)
const [panelWidth, setPanelWidth] = useState(288); // 默认 w-72 = 288px

const startResize = (e: React.MouseEvent) => {
  e.preventDefault();
  const startX = e.clientX;
  const startWidth = panelWidth;
  const onMove = (ev: MouseEvent) => {
    const delta = startX - ev.clientX; // 向左拖 = 增宽
    const newWidth = Math.min(480, Math.max(240, startWidth + delta));
    setPanelWidth(newWidth);
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
};

// 渲染(L296-304)
{workspaceOpen && (
  <aside style={{ width: panelWidth }} className="shrink-0 border-l border-default bg-surface flex">
    <div
      className="absolute -left-0.5 top-0 h-full w-1 cursor-col-resize hover:bg-brand-500/30"
      onMouseDown={startResize}
    />
    <WorkspacePanel onFileClick={(file) => { void openFileViewer(file); }} />
  </aside>
)}
```

## 行为规格

### 阶段 1:任务面板展示 todo 列表

**用户发起 work/coding 对话(LLM 调用 write_todos)**:
1. 后端 streaming 层 diff `state.todos`,产出 `todo_update` SSE 事件(source=work/coding)
2. 前端 useChatStream 接收,`addTask` 创建主任务(taskSource=work/coding)
3. TaskTimeline 渲染任务卡片:标题 + 进度条 + 展开/收起 todo 三态列表

**用户发起 coding_team 对话**:
1. `_plan_node` 产出 todo_update(parent_task_id 无),前端创建主任务
2. 各子任务节点 `_emit_todo_in_progress` 产出 todo_update(source=agent_role, parent_task_id=parent_thread_id)
3. 前端创建子任务(parentTaskId 关联),TaskTimeline 嵌套渲染

### 阶段 3:数据源统一后的聊天区 TodoProgress

**切会话**:
1. ChatView 的 `currentId` 变化
2. `useTasksStore` selector 重新计算,过滤新会话的 running 任务
3. TodoProgress 自动显示新会话最新任务的 todos,无需 `setTodos` 重置

**新一轮发送**:
1. `currentTaskIdRef.current = null`(useChatStream 重置)
2. 首个 todo_update 创建新 task,store 更新
3. ChatView selector 自动感知 store 变化,TodoProgress 更新

### 任务流分组渲染

**Team 路径父子任务**:
```
TaskTimeline
├─ [主任务] 分析项目并修改入口文件        ← parentTaskId=undefined
│   ├─ 进度条: ████████░░ 80%
│   ├─ ○ 读取 src/main.py               ← 主任务 todos
│   ├─ ◐ 修改入口逻辑
│   └─ ○ 运行测试
│   ├─ [code #0] 读取 main.py           ← parentTaskId=主任务, agentRole=code
│   │   └─ ✓ 读取 src/main.py
│   └─ [deep #1] 修改文件                ← parentTaskId=主任务, agentRole=deep
│       └─ ◐ 修改入口逻辑
├─ [独立任务] 翻译 README.md              ← parentTaskId=undefined (work 路径)
```

## 兼容性约束

1. **SSE 事件契约**: `todo_update` 新增 2 个可选字段(source + parent_task_id),向后兼容
   - 前端旧版本忽略未知字段,不影响
   - 后端旧版本不发这两个字段,前端新版本按 undefined 处理(降级为主任务场景)
   - 需同步更新三处:[chat.py](../../../backend/app/api/chat.py) + [api-types.ts](../../../frontend/shared/api-types.ts) + [useChatStream.ts](../../../frontend/renderer/hooks/useChatStream.ts)

2. **前端持久化**: `agentx-tasks` localStorage version 4 → 5
   - 非破坏性 migrate:新字段可选,老任务补 undefined / "work"
   - 老任务无 parentTaskId,按主任务渲染(向后兼容)

3. **CompactTaskList 删除**: 检查无其他 import 引用(当前仅 WorkspacePanel.tsx 引用)

4. **ChatView 删除 setTodos**: useChatStream 的 UseChatStreamArgs 接口变更,需同步删除所有 setTodos 调用点

5. **面板可调宽**: panelWidth 不持久化(首版),后续可存入 zustand store

6. **Team 安全约束不变**: source + parent_task_id 是展示元数据,不影响 `_validate_task` / `_looks_like_dangerous_task` 安全校验

7. **deepagents TodoListMiddleware 不变**: 三态 schema(pending/in_progress/completed)不变,blocked 仍用 in_progress + 新建 blocker todo 表达
