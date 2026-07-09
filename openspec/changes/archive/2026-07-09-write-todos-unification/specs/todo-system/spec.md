# Spec: Todo 系统(todo-system)

## 概述

项目 Todo 系统统一到 deepagents 原生 `TodoListMiddleware` + `write_todos` 工具 + `state.todos`
持久化方案。streaming 层从 LangGraph state 读取结构化 todos 并产出原生 schema 的 `todo_update`
SSE 事件;Team 路径 Orchestrator 改用 deepagent + `write_todos` 拆任务,子任务进度通过 todos
status 变化反映;前端 TodoItem 破坏性升级为 `{content, status}` 三态(pending/in_progress/completed)。

## 包结构

```
backend/app/
├── sse/
│   └── events.py            ← 修改:新增 make_todo_update_event,删除 make_todo_event/make_team_event
├── deepagent/
│   ├── streaming.py         ← 修改:读取 state.todos + diff,删除手造 todo 事件 + plan_extraction 调用
│   └── agent.py             ← 修改:_DEEP_SYSTEM_PROMPT 删除 write_todos 指令
├── team/
│   ├── blackboard.py        ← 修改:TeamState 增加 todos + _merge_todos reducer,SubtaskState 增加 task_index
│   ├── planner.py           ← 修改:删除 TeamPlan schema,新增 _ORCHESTRATOR_SYSTEM_PROMPT + _todos_to_team_tasks
│   ├── orchestrator.py      ← 修改:_plan_node 改用 deepagent,删除 team_plan 事件,_dispatch_node 用 todos
│   └── scheduler.py         ← 修改:子任务节点更新 todo status,删除 team_progress/team_result 事件
├── cli/
│   └── renderer.py          ← 修改:_render_todo_update 三态,删除 plan/team_* 渲染方法
└── utils/
    └── plan_extraction.py   ← 删除:手写 JSON 解析

frontend/
├── shared/
│   └── api-types.ts         ← 修改:新增 TodoStatus,更新 todo_update,删除 plan/team_* 类型
└── renderer/
    ├── hooks/
    │   └── useChatStream.ts ← 修改:TodoItem 升级,删除 normalizePlanTasks/plan/team_* 分支
    ├── components/
    │   └── chat/
    │       └── TodoProgress.tsx  ← 修改:三态渲染,completedTodos 计算
    ├── stores/
    │   └── tasks.ts         ← 修改:Task.todos 升级,migrate v3→v4,version 3→4
    └── components/
        └── workspace/
            └── TaskTimeline.tsx  ← 修改:completedTodos 适配三态
```

## 公共 API

### `sse/events.py` 变更

```python
# 新增(替代旧 make_todo_event)
def make_todo_update_event(
    todos: list[dict[str, Any]],
    task_id: str | None = None,
    trace_id: str | None = None,
) -> dict[str, str]:
    """构造 todo_update SSE 事件(原生 deepagents Todo schema)。
    
    Args:
        todos: deepagents state.todos 原生列表,每项 {content: str, status: str}
               (status: "pending" | "in_progress" | "completed")
        task_id: 任务分组标识(Team 多子任务场景注入 thread_id,单 agent 场景可省)
        trace_id: 观测中心 trace_id
    
    Returns:
        SSE 事件 dict {"event": "todo_update", "data": "{\"todos\":[...],\"task_id\":...}"}
    
    payload 格式:
        {
            "todos": [
                {"content": "读取文件", "status": "completed"},
                {"content": "修改代码", "status": "in_progress"}
            ],
            "task_id": "thread-xxx"  // 可选
        }
    """

# 删除
def make_todo_event(text: str, done: bool, task_id: str | None = None) -> dict[str, str]: ...
def make_team_event(event: str, data: Any, trace_id: str | None = None) -> dict[str, str]: ...

# _JSON_EVENTS 白名单更新
_JSON_EVENTS = (
    "todo_update",
    "approval_request",
    "reasoning",
    "tool_call",
    "tool_result",
    "delegation",
    "classification",
    "team_done",      # 保留(Team 整体完成信号)
    "error",
    "_subtask_done",
    # 删除: "plan", "plan_update", "team_plan", "team_progress", "team_result"
)
```

### `deepagent/streaming.py` 变更

```python
# 之前(删除)
from app.utils.plan_extraction import extract_plan_or_update
from app.sse.events import make_todo_event

async def _stream_agent_events(...):
    async for state in agent.astream(inputs, config=config, stream_mode="values"):
        messages = state.get("messages", [])
        # 只读 messages,手造 todo 事件
        for msg in new_messages:
            if isinstance(msg, ToolMessage):
                yield make_tool_result_event(...)
                yield make_todo_event(f"工具 {tool_name} 完成", done=True, task_id=thread_id)  # 删除
            elif isinstance(msg, AIMessage):
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        yield make_tool_call_event(...)
                        yield make_todo_event(f"调用工具: {tc_name}", done=False, task_id=thread_id)  # 删除
                elif msg.content:
                    # 调用 extract_plan_or_update 解析 JSON plan  # 删除整块
                    plan_update = extract_plan_or_update(text)  # 删除
                    if plan_update:  # 删除
                        yield make_sse_event("plan", ...)  # 删除

# 之后
from app.sse.events import make_todo_update_event

async def _stream_agent_events(...):
    _last_todos: list[dict] = []
    async for state in agent.astream(inputs, config=config, stream_mode="values"):
        # 读取 state.todos(原生 deepagents TodoListMiddleware 维护)
        current_todos = state.get("todos", []) if hasattr(state, "get") else []
        if current_todos != _last_todos:
            await _obs("todo_update", {"todos": current_todos, "task_id": thread_id})
            yield make_todo_update_event(current_todos, task_id=thread_id)
            _last_todos = current_todos
        
        messages = state.get("messages", [])
        # messages 处理不变(tool_call/tool_result/reasoning/token 事件)
        for msg in new_messages:
            if isinstance(msg, ToolMessage):
                yield make_tool_result_event(...)  # 保留,不再 yield todo 事件
            elif isinstance(msg, AIMessage):
                if msg.tool_calls:
                    yield make_sse_event("reasoning", ...)
                    for tc in msg.tool_calls:
                        yield make_tool_call_event(...)  # 保留,不再 yield todo 事件
                elif msg.content:
                    text = strip_think(...)
                    if text:
                        yield make_sse_event("token", text)  # 保留,不再调用 extract_plan_or_update
```

### `deepagent/agent.py` 变更

```python
# 之前
_DEEP_SYSTEM_PROMPT = (
    "你是一个强大的个人助理。你可以读写文件、搜索知识库、搜索网页。"
    "执行危险操作（写文件、执行命令）前需要用户审批。"
    "请根据用户任务规划步骤，调用合适的工具完成。"
    "请使用 write_todos 工具维护任务清单，每完成一步更新对应 todo 的状态为 completed。"  # 删除
)

# 之后(deepagents TodoListMiddleware 自带 WRITE_TODOS_SYSTEM_PROMPT 自动注入)
_DEEP_SYSTEM_PROMPT = (
    "你是一个强大的个人助理。你可以读写文件、搜索知识库、搜索网页。"
    "执行危险操作（写文件、执行命令）前需要用户审批。"
    "请根据用户任务规划步骤，调用合适的工具完成。"
)
```

### `team/blackboard.py` 变更

```python
# 新增 reducer
def _merge_todos(left: list[dict], right: list[dict]) -> list[dict]:
    """按 _index 字段合并 right 到 left(子任务节点返回部分 todo 更新)。
    
    right 每项可含 _index 字段定位 left 中对应 todo,合并 status 后移除 _index。
    无 _index 或 index 越界时,right 整体追加到 left(Orchestrator 初始化场景)。
    """

class TeamState(TypedDict, total=False):
    # 既有字段...
    todos: Annotated[list[dict], _merge_todos]  # 新增:deepagents 原生 Todo schema

class SubtaskState(TypedDict, total=False):
    # 既有字段...
    task_index: int  # 新增:子任务在 todos 列表中的序号
```

### `team/planner.py` 变更

```python
# 删除
class TeamPlanItem(BaseModel): ...
class TeamPlan(BaseModel): ...
_ORCHESTRATOR_PROMPT = ChatPromptTemplate.from_messages([...])
def _postprocess_plan(plan: TeamPlan, max_tasks: int) -> tuple[list[TeamPlanTask], str]: ...

# 新增
_ORCHESTRATOR_SYSTEM_PROMPT = """你是一个任务拆解专家（Orchestrator）。
请把用户请求拆分成若干子任务，使用 write_todos 工具写入任务清单。

每个 todo 的 content 必须以 [agent:类型] 开头，格式：
[agent:code] 读取 src/main.py 并分析入口逻辑
[agent:deep] 修改 src/main.py 添加日志输出
[agent:rag] 检索知识库中关于 FastAPI 最佳实践

可用 agent 类型：
{experts}

约束：
1. 涉及写文件、编辑文件、执行系统命令的任务，agent 必须设为 deep
2. 不要编造文件路径；若用户没给路径，子任务输入里说明需要搜索或推断
3. 子任务数量不要超过 {max_tasks} 个
4. 若任务简单，可只返回一个子任务
5. 所有 todo 的 status 设为 pending

项目上下文：
{context}
"""

def _todos_to_team_tasks(
    todos: list[dict],
    settings: Any,
) -> tuple[list[TeamPlanTask], str]:
    """从 state.todos 解析子任务,转换为 TeamPlanTask 列表。
    
    解析每个 todo 的 content 前缀 [agent:xxx] 确定 agent 类型,
    剥离前缀后的内容作为 input。
    调用 _validate_task 校验 + _looks_like_dangerous_task 安全改写。
    
    Args:
        todos: deepagents 原生 Todo 列表 [{content, status}, ...]
        settings: 全局配置(用于 _validate_task 校验 agent 可用性)
    
    Returns:
        (tasks, reasoning): tasks 是 TeamPlanTask 列表,reasoning 是空串
        (deepagents write_todos 不产 reasoning,原 TeamPlan.reasoning 字段废弃)
    """

# 保留不变
_BASE_EXPERTS = ...
def _build_team_experts_description(settings): ...
def _build_project_context(): ...
def _validate_task(task, settings): ...
def _looks_like_dangerous_task(input_text): ...
```

### `team/orchestrator.py` 变更

```python
# 之前
async def _plan_node(state: TeamState) -> dict:
    prompt = _build_orchestrator_prompt(user_message, max_tasks, context, scene, settings)
    plan = await llm.with_structured_output(TeamPlan).ainvoke(prompt)
    tasks, reasoning = _postprocess_plan(plan, max_tasks)
    yield make_team_event("team_plan", {"plan": [...], "reasoning": reasoning})  # 删除
    return {"plan": tasks, "reasoning": reasoning}

# 之后
from deepagents import create_deep_agent

async def _plan_node(state: TeamState) -> dict:
    settings = get_settings()
    # 构建轻量 Orchestrator agent(只带 write_todos 工具,不执行项目工具)
    # system_prompt 用 _ORCHESTRATOR_SYSTEM_PROMPT 模板填充:
    #   - experts: _BASE_EXPERTS + _build_team_experts_description(settings)
    #   - max_tasks: settings.team_max_tasks(既有配置项)
    #   - context: _build_project_context()
    # user_input = state["message"](用户原始请求)
    system_prompt = _ORCHESTRATOR_SYSTEM_PROMPT.format(
        experts=_BASE_EXPERTS + "\n" + _build_team_experts_description(settings),
        max_tasks=settings.team_max_tasks,
        context=_build_project_context(),
    )
    orchestrator = create_deep_agent(
        model=state["chat_model"],
        tools=[],
        system_prompt=system_prompt,
    )
    result = await orchestrator.ainvoke(
        {"messages": [{"role": "user", "content": state["message"]}]},
        config={"configurable": {"thread_id": f"{state['thread_id']}-orchestrator"}},
    )
    todos = result.get("todos", [])
    tasks, reasoning = _todos_to_team_tasks(todos, settings)
    # 不再 yield team_plan 事件(streaming 层从 state.todos 产出 todo_update)
    return {"plan": tasks, "todos": todos, "reasoning": reasoning}

async def _dispatch_node(state: TeamState) -> list[Send]:
    # 读 state.todos,为每个 todo 创建 Send,带 task_index
    sends = []
    for idx, todo in enumerate(state.get("todos", [])):
        task = state["plan"][idx]  # _todos_to_team_tasks 保证 plan 与 todos 同序
        sends.append(Send(
            _get_runner(task.agent),
            {
                **state,
                "task": {"agent": task.agent, "input": task.input, "purpose": task.purpose},
                "task_index": idx,
            },
        ))
    return sends
```

### `team/scheduler.py` 变更

```python
# 子任务节点开始/完成时更新 todo status
async def _run_subtask_stream(...):
    task_index = subtask_state.get("task_index", 0)
    # 开始:标记 in_progress
    yield {"todos": [{"_index": task_index, "status": "in_progress"}]}
    # ... 子任务执行,内部 yield tool_result 等事件 ...
    # 完成:标记 completed
    yield {"todos": [{"_index": task_index, "status": "completed"}]}

# 删除
# yield make_team_event("team_progress", {"agent": ..., "status": "running"})
# yield make_team_event("team_progress", {"agent": ..., "status": "done"})
# yield make_team_event("team_result", {"agent": ..., "summary": ...})

# _PASSTHROUGH_EVENTS 更新
_PASSTHROUGH_EVENTS = {
    "approval_request",
    "token",
    "tool_result",
    "reasoning",
    "delegation",
    # 删除: "team_progress", "team_result"
}
```

### `cli/renderer.py` 变更

```python
# 之前
def _render_todo_update(self, data: str) -> None:
    parsed = json.loads(data)
    todos = parsed.get("todos", [])
    for todo in todos:
        text = todo.get("text", "")
        done = todo.get("done", False)
        marker = f"{Fore.GREEN}✓" if done else f"{Fore.YELLOW}○"
        print(f"  {marker} {text}")

# 之后
def _render_todo_update(self, data: str) -> None:
    parsed = json.loads(data)
    todos = parsed.get("todos", [])
    for todo in todos:
        content = todo.get("content", "")
        status = todo.get("status", "pending")
        if status == "completed":
            marker = f"{Fore.GREEN}✓{Style.RESET_ALL}"
        elif status == "in_progress":
            marker = f"{Fore.YELLOW}◐{Style.RESET_ALL}"
        else:  # pending
            marker = f"{Fore.LIGHTBLACK_EX}○{Style.RESET_ALL}"
        print(f"  {marker} {content}")

# 删除
def _render_plan(self, data): ...
def _render_plan_update(self, data): ...
def _render_team_plan(self, data): ...
def _render_team_progress(self, data): ...
def _render_team_result(self, data): ...
# 保留 _render_team_done
```

### 前端 `api-types.ts` 变更

```typescript
// 新增
export type TodoStatus = "pending" | "in_progress" | "completed";

// todo_update 事件(更新)
| { type: "todo_update"; todos: { content: string; status: TodoStatus; task_id?: string }[]; task_id?: string; trace_id?: string }

// 删除
// | { type: "plan"; plan: PlanTask[]; trace_id?: string }
// | { type: "plan_update"; plan: PlanTask[]; trace_id?: string }
// | { type: "team_plan"; ... }
// | { type: "team_progress"; ... }
// | { type: "team_result"; ... }

// 保留(简化)
| { type: "team_done"; status?: "error" | "done"; trace_id?: string }

// 删除 interface
// export interface PlanTask { task_id: string; text: string; done?: boolean; }
```

### 前端 `useChatStream.ts` 变更

```typescript
// 之前
export interface TodoItem {
  text: string;
  done: boolean;
  taskId?: string;
}
function normalizeTodos(todosField, fallbackTaskId?): TodoItem[] { /* 处理 {text, done} */ }
function normalizePlanTasks(planField): TodoItem[] { /* 处理 plan 事件 */ }

// 之后
export interface TodoItem {
  content: string;
  status: TodoStatus;
  taskId?: string;
}
function normalizeTodos(todosField, fallbackTaskId?): TodoItem[] {
  if (!Array.isArray(todosField)) return [];
  return todosField
    .map((item): TodoItem | null => {
      if (typeof item !== "object" || item === null) return null;
      const obj = item as Record<string, unknown>;
      const content = typeof obj.content === "string" ? obj.content : String(obj.content ?? "");
      const status = (["pending", "in_progress", "completed"].includes(obj.status as string)
        ? obj.status : "pending") as TodoStatus;
      const taskId = typeof obj.task_id === "string" && obj.task_id.length > 0
        ? obj.task_id : fallbackTaskId;
      return { content, status, ...(taskId ? { taskId } : {}) };
    })
    .filter((x): x is TodoItem => x !== null);
}
// 删除 normalizePlanTasks

// switch 分支删除
// case "plan": ...
// case "plan_update": ...
// case "team_plan": ...
// case "team_progress": ...
// case "team_result": ...

// case "todo_update" 简化(已是原生 schema)
case "todo_update": {
  const taskId = typeof e.task_id === "string" && e.task_id.length > 0 ? e.task_id : undefined;
  const incoming = normalizeTodos(e.todos, taskId);
  callbacksRef.current.setTodos((prev) => {
    if (!taskId) return incoming;
    const kept = prev.filter((t) => t.taskId !== taskId);
    return [...kept, ...incoming];
  });
  // ... updateTask 逻辑不变
  break;
}
```

### 前端 `TodoProgress.tsx` 变更

```tsx
// TodoList 三态渲染
function TodoList({ todos }: { todos: TodoItem[] }) {
  return (
    <ul className="space-y-1">
      {todos.map((t, i) => {
        const isCompleted = t.status === "completed";
        const isInProgress = t.status === "in_progress";
        return (
          <li key={i} className="flex items-start gap-2">
            <span className={`mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border ${
              isCompleted ? "border-brand-600 bg-brand-700 text-brand-200"
              : isInProgress ? "border-yellow-500 bg-yellow-500/20 text-yellow-400 animate-spin"
              : "border-strong"
            }`}>
              {isCompleted && <svg>...</svg>}
              {isInProgress && <span className="text-[8px]">◐</span>}
            </span>
            <span className={isCompleted ? "text-muted-c line-through" : "text-secondary-c"}>
              {t.content}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

// completedTodos 计算
const completedTodos = todos.filter((t) => t.status === "completed").length;
```

### 前端 `stores/tasks.ts` 变更

```typescript
export interface Task {
  id: string;
  title: string;
  status: "pending" | "running" | "done" | "failed";
  todos?: { content: string; status: TodoStatus }[];  // 升级
  createdAt: number;
  updatedAt?: number;
  sessionId: string;
}

// migrate v3 → v4
export function migrateTasksState(persisted: unknown, version: number): Partial<TasksState> {
  // ... 既有 v0→v1, v1→v2, v2→v3 ...
  if (version < 4) {
    tasks = tasks.map((t) => ({
      ...t,
      todos: (t.todos ?? []).map((old: any) => ({
        content: typeof old.text === "string" ? old.text : String(old.text ?? ""),
        status: old.done ? "completed" as const : "pending" as const,
      })),
    }));
  }
  // ...
}

// version: 3 → 4
{ name: "agentx-tasks", storage: ..., version: 4, migrate: migrateTasksState, ... }
```

## 行为规格

### 主路径 todo 事件流

**LLM 调用 write_todos 时**:
1. `TodoListMiddleware` 接收工具调用,写入 `state.todos`(原生 `[{content, status}]`)
2. LangGraph checkpointer 自动持久化 `state.todos`
3. `astream(stream_mode="values")` 推送新 state 到 streaming 层
4. streaming 层 diff `state.todos` 与 `_last_todos`,变化时 yield `todo_update` SSE 事件
5. 前端 `useChatStream` 接收 `todo_update`,调用 `normalizeTodos` 归一化为 `{content, status, taskId?}`
6. `TodoProgress` 渲染三态

**LLM 更新 todo status 时**(如标记某 todo 为 completed):
1. `TodoListMiddleware` 更新 `state.todos` 对应项的 status
2. streaming 层 diff 检测到变化,yield `todo_update`
3. 前端更新对应 todo 的 status,`TodoProgress` 重新渲染

**中断断点续跑**:
1. 用户中断后,`state.todos` 已持久化到 checkpointer
2. 恢复时 `astream(None, config)` 从 checkpoint 续跑,`state.todos` 自动恢复
3. streaming 层首次 `_last_todos` 为空,首帧 state 到达时 yield 当前 todos 快照

### Team 路径 todo 事件流

**Orchestrator 拆任务**:
1. `_plan_node` 构建轻量 deepagent(tools=[]),LLM 调用 `write_todos` 写入 `state.todos`
2. `_todos_to_team_tasks` 从 todos 解析子任务,转换为 `TeamPlanTask` 列表
3. `_plan_node` 返回 `{"plan": tasks, "todos": todos}`
4. streaming 层从 `state.todos` 产出 `todo_update` 事件(替代旧 `team_plan`)

**子任务 fan-out 执行**:
1. `_dispatch_node` 读 `state.todos`,为每个 todo 创建 `Send`(带 `task_index`)
2. 子任务节点开始时返回 `{"todos": [{"_index": idx, "status": "in_progress"}]}`
3. `_merge_todos` reducer 合并到 `state.todos`,对应 todo 标记为 in_progress
4. streaming 层 diff 检测到 status 变化,yield `todo_update`(替代旧 `team_progress` running)
5. 子任务节点完成时返回 `{"todos": [{"_index": idx, "status": "completed"}]}`
6. streaming 层 yield `todo_update`(替代旧 `team_progress` done + `team_result`)

**Team 整体完成**:
1. 所有子任务完成,`_aggregate_node` 汇总 findings
2. yield `team_done` 事件(保留,前端用它关闭 team part 渲染)

### blocked 状态处理(严格 3 态)

原生 `Todo.status` 只有 pending/in_progress/completed,**不支持 blocked**。

**遇到 blocker 时**(遵循原生系统提示推荐做法):
1. LLM 保持当前 todo 为 `in_progress`
2. LLM 新建一条 todo,content 描述 blocker(如 "等待用户确认数据库密码")
3. 新 todo status 设为 `pending`
4. blocker 解决后,LLM 删除/标记 blocker todo 为 completed,继续原 todo

**前端渲染**: 无 blocked 单独样式。in_progress 的 todo 可能伴随 blocker 描述的 pending todo,
前端正常渲染三态。

### todo_update 事件去重

streaming 层用 `_last_todos` 快照 diff:
- `current_todos == _last_todos` → 不 yield(去重)
- `current_todos != _last_todos` → yield + 更新 `_last_todos`

**比较规则**: list 顺序敏感 + dict 字段全等。deepagents `TodoListMiddleware` 每次调用 `write_todos`
会整体替换 `state.todos`,顺序稳定(LLM 通常 append 新 todo,更新已有 todo 的 status)。

## 兼容性约束

1. **SSE 事件契约**: **破坏性变更**
   - 删除 5 种事件:`plan` / `plan_update` / `team_plan` / `team_progress` / `team_result`
   - `todo_update` schema 变更:`{text, done}` → `{content, status}`
   - 保留 `team_done`(payload 简化为 `{status}`)
   - 前后端必须同步部署(前端旧版本收到新 schema 会 undefined,后端旧版本发旧 schema 前端新版本报错)

2. **前端持久化**: `agentx-tasks` localStorage
   - store version 3 → 4
   - migrate v3→v4 转换旧 `{text, done}` 为 `{content: text, status: done ? "completed" : "pending"}`
   - 旧用户升级后历史任务 todos 自动转换,不丢数据

3. **deepagents 依赖**: 无新增
   - `TodoListMiddleware` / `write_todos` / `PlanningState.todos` 已在 deepagents 0.6+ 内置
   - `create_deep_agent(tools=[])` 仍注入 `TodoListMiddleware`(验证项 T0.1)

4. **LangGraph 兼容**:
   - `astream(stream_mode="values")` 不变(保持尊重 `interrupt_on`)
   - `Send` fan-out 机制不变(Team 并行子任务)
   - `Annotated[list, _merge_todos]` reducer 是 LangGraph 原生特性

5. **Team 安全约束不变**:
   - `_validate_task` 校验 agent 可用性(保留)
   - `_looks_like_dangerous_task` 启发式判断(保留)
   - 危险任务强制改写为 deep(保留)
   - `FORBIDDEN_SUBAGENT_TOOLS` 硬约束(保留)

6. **checkpointer 兼容**:
   - `state.todos` 由 LangGraph checkpointer 自动持久化(原生能力)
   - 中断断点续跑时 `state.todos` 自动恢复
   - 无需额外持久化逻辑

7. **观测中心兼容**:
   - `todo_update` 事件仍写 `observation_event` 表(payload schema 变更)
   - 旧 observation 数据保留(历史记录不改)
   - `plan` / `team_plan` 等事件不再写入(迁移后不再产出)

8. **不扩展 blocked 状态**(用户决策):
   - 严格遵循原生 3 态(pending/in_progress/completed)
   - 不子类化 `TodoListMiddleware`
   - 不 fork `Todo` TypedDict
   - blocked 语义用 in_progress + 新建 blocker todo 表达
