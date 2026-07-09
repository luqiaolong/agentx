# Design: 迁移到 deepagents 原生 Todo 系统(write_todos 统一)

## Context

### 历史背景

[2026-07-08-deepagents-migration](../2026-07-08-deepagents-migration/proposal.md) 的 Decision 11
声称"删除 JSON plan,用 `write_todos` 替换",但实际执行**只删除了系统提示里的 JSON plan 指令**,
未真正消费 deepagents 原生 `TodoListMiddleware` 维护的 `state.todos`。

### 当前三套自造轮子

1. **手造 todo_update 字符串事件**([deepagent/streaming.py:205-264](../../../backend/app/deepagent/streaming.py#L205-L264)):
   - `astream(stream_mode="values")` 拿到 state 后**只读 `state.get("messages")`**
   - ToolMessage 到达 → `make_todo_event(f"工具 {tool_name} 完成", done=True, task_id=thread_id)`
   - AIMessage with tool_calls → `make_todo_event(f"调用工具: {tc_name}", done=False, task_id=thread_id)`
   - 这把原生结构化 TodoList 降级成"工具调用进度条",且 `task_id=thread_id` 让所有事件归到同一组

2. **手写 JSON plan 解析**([utils/plan_extraction.py:73-113](../../../backend/app/utils/plan_extraction.py#L73-L113)):
   - `parse_json_markdown` 从 LLM 文本提取 `{"plan": [...]}` / `{"plan_update": {...}}`
   - 产出 `plan` / `plan_update` SSE 事件(违反 AGENTS.md §3 R12「手写 LLM 输出解析」)
   - streaming.py 第 282-286 行调用它

3. **Team 路径独立 TeamPlan schema**([team/planner.py:43-55](../../../backend/app/team/planner.py#L43-L55)):
   - `llm.with_structured_output(TeamPlan)` + 自定义 `TeamPlan` / `TeamPlanItem` pydantic
   - 产出 `team_plan` / `team_progress` / `team_result` 事件,与主路径完全隔离

### 原生能力(已声明未消费)

deepagents 0.6+ `create_deep_agent` 默认注入 `TodoListMiddleware`:
- **`write_todos` 工具**:LLM 调用它写结构化 todo 到 `state.todos`
- **`Todo` TypedDict**:`{content: str, status: Literal["pending","in_progress","completed"]}`
  (见 [.venv/.../langchain/agents/middleware/todo.py:31](../../../.venv/Lib/site-packages/langchain/agents/middleware/todo.py#L31))
- **`PlanningState.todos`**:经 LangGraph checkpointer 自动持久化,支持中断断点续跑
- **`WRITE_TODOS_SYSTEM_PROMPT`**:自动注入,指导 LLM 何时用/如何用
  (见 [todo.py:119-136](../../../.venv/Lib/site-packages/langchain/agents/middleware/todo.py#L119-L136))

但项目代码**从未读取 `state.todos`**——LLM 调用 `write_todos` 后结构化数据被丢弃。

### 前端契约与原生 schema 不兼容

| 维度 | 当前前端 | 原生 deepagents |
|---|---|---|
| TodoItem 字段 | `{text, done, taskId?}` | `{content, status}` |
| 状态语义 | done/undone 二态 | pending/in_progress/completed 三态 |
| 多任务分组 | `task_id`(Team 子任务) | 无(单 agent 内) |
| 事件类型 | 6 种(todo_update + plan + plan_update + team_plan + team_progress + team_result) | 1 种(todo_update) |

## Goals / Non-Goals

**Goals:**
- streaming 层真正读取 `state.get("todos")` 并产出原生 schema 的 `todo_update` 事件
- 删除手造的字符串 todo 事件 + plan_extraction.py 手写 JSON 解析
- Team 路径改用 deepagent + `write_todos` 拆任务,删除 TeamPlan pydantic schema
- 前端 TodoItem 破坏性升级为 `{content, status, taskId?}` 三态
- 删除 5 种冗余 SSE 事件(plan/plan_update/team_plan/team_progress/team_result),保留 team_done
- 严格遵循原生 3 态(pending/in_progress/completed),不扩展 blocked

**Non-Goals:**
- 不子类化 `TodoListMiddleware` 扩展 blocked 状态(用户决策:严格遵循原生 3 态)
- 不改 LangGraph `astream` 的 stream_mode(保持 `values` + diff,不改 `updates`)
- 不改 deepagents 的 `Todo` TypedDict 本身(fork 维护成本高)
- 不删除 `team_done` 事件(Team 整体完成信号仍需要,前端用它关闭 team part)
- 不改 Team 的 `Send` fan-out 并行机制(LangGraph 原生能力,保留)
- 不改 `_validate_task` / `_looks_like_dangerous_task` 安全约束(保留)
- 不改 checkpointer 机制(`state.todos` 已自动持久化,无需额外处理)

## Decisions

### Decision 1: streaming 层用 state diff 而非 stream_mode="updates"

**问题**: 如何从 `astream` 拿到 `state.todos` 变化?两个选项:
- A. `astream(stream_mode="updates")`:只推送 state 增量,自然拿到 todos diff
- B. `astream(stream_mode="values")` + 内存快照 diff:每次拿全量 state,与上次 todos 快照对比

**选择**: B(values + diff)

**理由**:
- 当前 streaming.py 已用 `stream_mode="values"`(为尊重 `interrupt_on`,在 tools 节点前暂停)
- `stream_mode="updates"` 不尊重 `interrupt_on`(会直接执行工具),改 stream_mode 风险高
- diff 逻辑简单:`_last_todos` 快照 + 比较 content/status,只发变化的 todo

**实现**:
```python
_last_todos: list[dict] = []

async for state in agent.astream(inputs, config=config, stream_mode="values"):
    current_todos = state.get("todos", []) if hasattr(state, "get") else []
    if current_todos != _last_todos:
        # todos 变化 → 产出 todo_update 事件(原生 schema)
        yield make_todo_update_event(current_todos, thread_id)
        _last_todos = current_todos
    # ... 其余 messages 处理不变
```

### Decision 2: todo_update 事件 payload 对齐原生 schema

**问题**: `make_todo_event(text, done, task_id)` 当前构造 `{text, done, task_id}` payload。
原生 `Todo` 是 `{content, status}`。如何映射?

**选择**: 新增 `make_todo_update_event(todos, task_id)` 函数,直接透传原生 schema + 注入 task_id

**payload 格式**:
```json
{
  "type": "todo_update",
  "todos": [
    {"content": "读取文件", "status": "completed"},
    {"content": "修改代码", "status": "in_progress"}
  ],
  "task_id": "thread-xxx"
}
```

**字段映射**:
- `content` ← 原生 `Todo.content`(直接透传)
- `status` ← 原生 `Todo.status`(pending/in_progress/completed,直接透传)
- `task_id` ← 注入 `thread_id`(用于 Team 多子任务分组,原生 Todo 无此字段)

**删除**: 旧 `make_todo_event(text, done, task_id)` 函数(违反原生 schema)

### Decision 3: Team Orchestrator 改用 deepagent + write_todos

**问题**: Team 路径 `_plan_node` 用 `llm.with_structured_output(TeamPlan)` 拆任务,
如何迁移到 `write_todos`?

**选择**: 构建轻量 deepagent 作为 Orchestrator,LLM 调用 `write_todos` 写入 `state.todos`

**实现**:
```python
async def _plan_node(state: TeamState) -> dict:
    # 构建轻量 Orchestrator agent(只带 write_todos 工具 + 系统提示)
    orchestrator_agent = create_deep_agent(
        model=state["chat_model"],
        tools=[],  # 不带项目工具,Orchestrator 只拆任务不执行
        system_prompt=_ORCHESTRATOR_SYSTEM_PROMPT,  # 指导拆任务到 write_todos
        # 不传 interrupt_on/memory/skills/backend —— Orchestrator 不执行工具
    )
    result = await orchestrator_agent.ainvoke(
        {"messages": [{"role": "user", "content": _build_orchestrator_input(state)}]},
        config={"configurable": {"thread_id": f"{state['thread_id']}-orchestrator"}},
    )
    todos = result.get("todos", [])
    # 转换为 TeamPlanTask(保留 _validate_task / _looks_like_dangerous_task 安全校验)
    tasks = _todos_to_team_tasks(todos, state)
    return {"plan": tasks, "todos": todos}
```

**`_ORCHESTRATOR_SYSTEM_PROMPT`**: 替代旧 `_ORCHESTRATOR_PROMPT` ChatPromptTemplate,
指导 LLM "把用户请求拆成子任务,每个子任务用 `write_todos` 工具写入,content 描述任务,
status 设为 pending"。deepagents 的 `WRITE_TODOS_SYSTEM_PROMPT` 自动注入 `write_todos` 用法,
Orchestrator 系统提示只需补充"如何拆任务 + 子任务 agent 类型约定"。

**`_todos_to_team_tasks`**: 从 `state.todos` 提取子任务,解析 `content` 里的 agent 类型
(格式约定:`[agent:code] 读取文件 xxx`),转换为 `TeamPlanTask(agent, input, purpose)`。
保留 `_validate_task` 校验 + `_looks_like_dangerous_task` 安全改写。

### Decision 4: TeamState.todos reducer 设计

**问题**: Team 用 `Send` fan-out 并行执行子任务,每个子任务节点如何更新父 graph 的 todos?

**选择**: `TeamState` 增加 `todos: Annotated[list[dict], _merge_todos]` + 按 index 合并

**reducer 实现**:
```python
def _merge_todos(left: list[dict], right: list[dict]) -> list[dict]:
    """按 todo 在列表中的 index 合并,right 覆盖 left 同 index 的 status。
    
    子任务节点返回 {"todos": [{...单条更新...}]},通过 task_index 定位。
    """
    if not left:
        return right
    if not right:
        return left
    merged = list(left)
    for update in right:
        idx = update.get("_index")
        if idx is not None and 0 <= idx < len(merged):
            merged[idx] = {**merged[idx], **update}
            merged[idx].pop("_index", None)
    return merged
```

**子任务节点更新逻辑**:
- `_dispatch_node` 为每个 todo 创建 `Send`,带 `task_index` 注入 `SubtaskState`
- 子任务节点开始时返回 `{"todos": [{"_index": task_index, "status": "in_progress"}]}`
- 子任务节点完成时返回 `{"todos": [{"_index": task_index, "status": "completed"}]}`
- 失败时返回 `{"todos": [{"_index": task_index, "status": "in_progress"}]}`(保持 in_progress,
  errors dict 单独记录,不污染 todo 状态)

### Decision 5: blocked 状态严格遵循原生 3 态

**问题**: 用户问题提到 blocked 状态,但原生 `Todo.status` 只有 pending/in_progress/completed。

**选择**: 严格遵循原生 3 态,blocked 用 `in_progress` + 新建 blocker todo 表达

**理由**(用户决策):
- 原生系统提示([todo.py:99](../../../.venv/Lib/site-packages/langchain/agents/middleware/todo.py#L99))
  推荐做法:遇到 blocker 时保持当前 todo `in_progress` + 新建一条描述 blocker 的新 todo
- 子类化扩展 4 态需 fork `TodoListMiddleware` + `Todo` TypedDict,每月同步上游需 rebase
- 3 态足以表达所有语义(blocked 是 in_progress 的特殊情况)

**前端渲染**: pending=○, in_progress=◐(旋转), completed=✓。无 blocked 单独样式。

### Decision 6: 保留 team_done,删除其他 team_* 事件

**问题**: Team 路径有 team_plan / team_progress / team_result / team_done 4 种事件,迁移后保留哪些?

**选择**: 只保留 `team_done`,删除 team_plan / team_progress / team_result

**理由**:
- `team_plan` → 由 streaming 层从 `state.todos` 产出 `todo_update` 替代
- `team_progress` → 子任务进度通过 `todo_update`(status: in_progress/completed)反映
- `team_result` → 子任务结果摘要通过 `tool_result` 事件反映(子任务节点内部仍 yield tool_result)
- `team_done` → Team 整体完成信号,**前端需要它关闭 team part 渲染**([useChatStream.ts:373-379](../../../frontend/renderer/hooks/useChatStream.ts#L373-L379)
  `upsertTeamNode(pendingId, {status: "done" | "error"})`)

**team_done payload 简化**: 从 `{status}` 改为 `{status: "done" | "error"}`(不变,但移除冗余字段)

### Decision 7: 前端 TodoItem 破坏性升级 + persist migrate

**问题**: 前端 TodoItem 从 `{text, done}` → `{content, status}`,旧持久化数据如何处理?

**选择**: 破坏性升级 + persist migrate v3 → v4

**migrate 逻辑**:
```typescript
if (version < 4) {
  tasks = tasks.map((t) => ({
    ...t,
    todos: (t.todos ?? []).map((old) => ({
      content: typeof old.text === "string" ? old.text : String(old.text ?? ""),
      status: old.done ? "completed" : "pending",
    })),
  }));
}
```

**store version**: 3 → 4

**TodoProgress.tsx 三态渲染**:
- `status === "completed"` → 绿色 ✓(原 done 样式)
- `status === "in_progress"` → 黄色 ◐ 旋转动画(原 not-done 样式 + spin)
- `status === "pending"` → 灰色 ○(新样式)

**completedTodos 计算**: `t.done` → `t.status === "completed"`

### Decision 8: 删除 plan_extraction.py + normalizePlanTasks

**问题**: `plan_extraction.py` 手写 JSON 解析 + 前端 `normalizePlanTasks` 归一化 plan 事件,
迁移后是否保留?

**选择**: 全部删除

**理由**:
- `plan` / `plan_update` SSE 事件由 `plan_extraction.py` 产出,迁移后不再产出
- 前端 `normalizePlanTasks`([useChatStream.ts:41-56](../../../frontend/renderer/hooks/useChatStream.ts#L41-L56))
  仅处理 plan 事件,事件删除后函数无用
- `api-types.ts` 的 `PlanTask` interface + `plan` / `plan_update` 事件类型一并删除
- CLI renderer 的 `_render_plan` / `_render_plan_update` 一并删除

### Decision 9: _DEEP_SYSTEM_PROMPT 清理 write_todos 指令

**问题**: [deepagent/agent.py:42-44](../../../backend/app/deepagent/agent.py#L42-L44) 写了
"请使用 write_todos 工具维护任务清单",与 deepagents 自带的 `WRITE_TODOS_SYSTEM_PROMPT` 重复。

**选择**: 删除项目层的 write_todos 指令

**理由**:
- `TodoListMiddleware` 自带 `WRITE_TODOS_SYSTEM_PROMPT`([todo.py:119-136](../../../.venv/Lib/site-packages/langchain/agents/middleware/todo.py#L119-L136))
  自动注入,内容详尽(何时用、如何拆、何时更新状态、如何处理 blocker)
- 项目层提示重复且可能冲突(如状态枚举不一致)
- 删除后由 deepagents 单一源头注入

## Risks

### Risk 1: state.todos diff 性能

**风险**: 每次 `astream(values)` 都拿全量 state,diff todos 可能在大列表时性能差。

**缓解**: Team 子任务通常 < 20 个,单 agent todo 通常 < 10 个,diff O(n) 可忽略。
若未来 todo 数量增长,再评估 `stream_mode="updates"` 迁移(P4)。

### Risk 2: Team Orchestrator deepagent 不带工具时的行为

**风险**: `create_deep_agent(tools=[])` 是否仍注入 `TodoListMiddleware`?LLM 能否调用 `write_todos`?

**缓解**: `TodoListMiddleware` 在 middleware 栈首部,与项目工具正交(见
[graph.py:774](../../../.venv/Lib/site-packages/deepagents/graph.py#L774))。`write_todos` 是
middleware 注入的内置工具,不在 `tools=` 参数里,LLM 可调用。迁移前写集成测试验证。

### Risk 3: TeamState.todos reducer 与 LangGraph 并行 Send 的兼容性

**风险**: 多个 `Send` 子任务节点并行返回 `{"todos": [{_index, status}]}`,reducer 合并顺序不确定。

**缓解**: `_merge_todos` 按 `_index` 定位更新,与顺序无关(每个子任务更新自己的 index)。
LangGraph `Annotated[list, _merge_todos]` 保证每个节点返回后调用 reducer 合并。

### Risk 4: 前端破坏性变更导致旧会话历史渲染异常

**风险**: 旧持久化的 task.todos 是 `{text, done}` 格式,升级后前端读 `{content, status}` 会 undefined。

**缓解**: persist migrate v3 → v4 转换旧数据(Decision 7)。迁移失败时 TodoProgress 做
防御性兜底(`typeof t.content === "string"` 校验,异常项跳过)。

### Risk 5: Team Orchestrator LLM 不调用 write_todos

**风险**: LLM 可能不调用 `write_todos`,而是直接在 content 里输出文本计划。

**缓解**:
- `_ORCHESTRATOR_SYSTEM_PROMPT` 明确要求"必须用 write_todos 工具拆任务"
- `result.get("todos", [])` 为空时,回退到旧的 `_looks_like_dangerous_task` + 单任务 deep
  (降级处理,不阻塞流程)
- deepagents 的 `WRITE_TODOS_SYSTEM_PROMPT` 自带强引导

### Risk 6: 删除 team_progress 后子任务实时进度丢失

**风险**: 旧 team_progress 事件实时反映子任务开始/完成,删除后前端如何看到进度?

**缓解**: streaming 层从 `state.todos` 产出 `todo_update`,子任务 status 变化
(pending → in_progress → completed)通过 todo_update 实时反映。前端 TodoProgress 渲染三态,
比旧 team_progress 的 running/done/error 二态更细粒度。

## Migration Strategy

### 分阶段迁移(4 阶段)

1. **P0(主路径)**: streaming 层读 state.todos + 删除手造事件 + 删除 plan_extraction.py
   - 最小可验证单元,不动 Team 路径
   - 独立提交,出问题可单独 revert

2. **P1(Team 路径)**: Orchestrator 改用 deepagent + TeamState.todos reducer
   - 风险最高阶段,需集成测试验证 Send fan-out + todos 合并
   - 保留旧 team_done 事件,删除其他 team_* 事件

3. **P2(前端)**: TodoItem 破坏性升级 + persist migrate
   - 与 P0/P1 后端可独立部署(前端做兼容兜底)
   - persist migrate 保证旧数据不丢

4. **P3(CLI + 文档 + 测试)**: 收尾

### 回滚策略

- 每个阶段独立提交,出问题可单独 revert
- P0 阶段 streaming.py 改动保留 git 历史,可快速回退到手造事件
- P1 阶段 team/planner.py 的 TeamPlan schema 保留在 git 历史(删除前确认无引用)
- P2 前端 persist migrate 是单向的(v3 → v4),回滚需手动降级 store version
- SSE 事件类型删除是破坏性变更,前后端必须同步部署(P2 前端上线时 P0/P1 后端必须已上线)

### 部署顺序

**必须**: P0 + P1 后端 → P2 前端(同步部署)
- 后端先删 plan/team_* 事件,前端旧版本会忽略未知事件(安全)
- 前端先升级,后端旧版本仍发 plan/team_*(前端 normalizePlanTasks 已删,会报错)

**推荐**: 后端灰度部署 P0 → 验证 → P1 → 验证 → 前端 P2 全量
