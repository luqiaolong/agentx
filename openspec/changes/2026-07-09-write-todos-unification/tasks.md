# 任务追踪 — 迁移到 deepagents 原生 Todo 系统(write_todos 统一)

## 阶段零:前置验证

- [ ] T0.1 验证 deepagents `create_deep_agent(tools=[])` 仍注入 `TodoListMiddleware`,
  LLM 可调用 `write_todos`(写集成测试)
- [ ] T0.2 验证 `astream(stream_mode="values")` 的 state 含 `todos` 字段
  (LLM 调用 write_todos 后 state.todos 非空)
- [ ] T0.3 确认 `make_todo_event` / `extract_plan_or_update` / `make_team_event` 的所有调用方
  (grep 验证无遗漏)

## 阶段一(P0):主路径消费原生 state.todos

### P0.1: streaming 层读取 state.todos

- [ ] T1.1 [sse/events.py](../../../backend/app/sse/events.py) 新增
  `make_todo_update_event(todos: list[dict], task_id: str | None) -> dict`:
  构造原生 schema 的 todo_update 事件(payload: `{todos: [{content, status}], task_id?}`)
- [ ] T1.2 [sse/events.py](../../../backend/app/sse/events.py) 删除旧
  `make_todo_event(text, done, task_id)` 函数(被 `make_todo_update_event` 替代)
- [ ] T1.3 [deepagent/streaming.py](../../../backend/app/deepagent/streaming.py) `_stream_agent_events`:
  - 在 `async for state in agent.astream(...)` 循环内,每次到达 state 时读取
    `state.get("todos", [])`,与 `_last_todos` 快照 diff
  - todos 变化时 `yield make_todo_update_event(current_todos, task_id=thread_id)`
  - 初始化 `_last_todos: list[dict] = []`
- [ ] T1.4 [deepagent/streaming.py:205-206](../../../backend/app/deepagent/streaming.py#L205-L206):
  删除 ToolMessage 分支的 `make_todo_event(f"工具 {tool_name} 完成")` 调用
- [ ] T1.5 [deepagent/streaming.py:263-264](../../../backend/app/deepagent/streaming.py#L263-L264):
  删除 AIMessage tool_calls 分支的 `make_todo_event(f"调用工具: {tc_name}")` 调用
- [ ] T1.6 删除 streaming.py 顶部的 `from app.utils.plan_extraction import extract_plan_or_update`
  import(P0.2 删除该模块后无引用)
- [ ] T1.7 写测试 `tests/python/unit/test_todo_streaming.py`:
  - 构造 mock agent,astream 返回含 todos 字段的 state
  - 验证 yield 的 todo_update 事件 payload 是原生 `{content, status}` schema
  - 验证 todos 未变化时不重复 yield(去重)

### P0.2: 删除 plan_extraction.py 手写 JSON 解析

- [ ] T2.1 删除 [utils/plan_extraction.py](../../../backend/app/utils/plan_extraction.py) 整个文件
- [ ] T2.2 [deepagent/streaming.py:282-286](../../../backend/app/deepagent/streaming.py#L282-L286):
  删除 AIMessage without tool_calls 分支中调用 `extract_plan_or_update` 的代码块
- [ ] T2.3 [sse/events.py:77-78](../../../backend/app/sse/events.py#L77-L78):
  从 JSON 事件白名单 `_JSON_EVENTS` 删除 `"plan"` / `"plan_update"`
- [ ] T2.4 grep 验证无残留 `plan_extraction` / `extract_plan_or_update` 引用

### P0.3: 系统提示清理

- [ ] T3.1 [deepagent/agent.py:42-44](../../../backend/app/deepagent/agent.py#L42-L44)
  `_DEEP_SYSTEM_PROMPT`:删除"请使用 write_todos 工具维护任务清单"指令
  (由 deepagents `WRITE_TODOS_SYSTEM_PROMPT` 自动注入)
- [ ] T3.2 grep 验证 Work Supervisor / Coding Expert 系统提示无类似 write_todos 指令
  (若有则删除)

## 阶段二(P1):Team 路径统一到 write_todos

### P1.1: TeamState 增加 todos 字段 + reducer

- [ ] T4.1 [team/blackboard.py](../../../backend/app/team/blackboard.py) `TeamState`:
  增加 `todos: Annotated[list[dict], _merge_todos]` 字段
- [ ] T4.2 [team/blackboard.py](../../../backend/app/team/blackboard.py) 新增
  `_merge_todos(left, right)` reducer:按 `_index` 字段合并 right 到 left,
  right 每项的 `_index` 定位 left 中对应 todo,合并 status 字段后移除 `_index`
- [ ] T4.3 [team/blackboard.py](../../../backend/app/team/blackboard.py) `SubtaskState`:
  增加 `task_index: int` 字段(子任务在 todos 列表中的序号)
- [ ] T4.4 写测试 `tests/python/unit/test_team_todos_reducer.py`:
  验证 `_merge_todos` 按 index 合并 + 多节点并行返回时正确归并

### P1.2: Team Orchestrator 改用 deepagent + write_todos

- [ ] T5.1 [team/planner.py](../../../backend/app/team/planner.py) 删除
  `TeamPlan` / `TeamPlanItem` pydantic schema + `_ORCHESTRATOR_PROMPT` ChatPromptTemplate +
  `_postprocess_plan` 函数
- [ ] T5.2 [team/planner.py](../../../backend/app/team/planner.py) 新增
  `_ORCHESTRATOR_SYSTEM_PROMPT` 常量(str,指导 LLM 用 write_todos 拆任务 +
  子任务 agent 类型约定格式 `[agent:code] 任务描述`)
- [ ] T5.3 [team/planner.py](../../../backend/app/team/planner.py) 新增
  `_todos_to_team_tasks(todos: list[dict], settings) -> tuple[list[TeamPlanTask], str]`:
  从 state.todos 解析子任务,提取 content 里的 `[agent:xxx]` 前缀确定 agent 类型,
  调用 `_validate_task` + `_looks_like_dangerous_task` 安全校验
- [ ] T5.4 [team/planner.py](../../../backend/app/team/planner.py) 保留
  `_BASE_EXPERTS` / `_build_team_experts_description` / `_build_project_context` /
  `_validate_task` / `_looks_like_dangerous_task` 函数(安全约束不变)
- [ ] T5.5 [team/orchestrator.py](../../../backend/app/team/orchestrator.py) `_plan_node`:
  - 替换 `llm.with_structured_output(TeamPlan).ainvoke(prompt)` 为构建轻量 deepagent
    (`create_deep_agent(model, tools=[], system_prompt=_ORCHESTRATOR_SYSTEM_PROMPT)`)
    + `ainvoke` + 从 result 取 `todos`
  - 调用 `_todos_to_team_tasks(todos, settings)` 转换为 TeamPlanTask
  - 返回 `{"plan": tasks, "todos": todos, "reasoning": reasoning}`
- [ ] T5.6 [team/orchestrator.py](../../../backend/app/team/orchestrator.py) `_dispatch_node`:
  读 `state["todos"]`,为每个 todo 创建 `Send`,带 `task_index` 注入 `SubtaskState`
- [ ] T5.7 [team/scheduler.py](../../../backend/app/team/scheduler.py) 子任务节点
  (`_run_subtask_stream` / `_run_team_role_subtask`):
  - 开始时返回 `{"todos": [{"_index": task_index, "status": "in_progress"}]}`
  - 完成时返回 `{"todos": [{"_index": task_index, "status": "completed"}]}`
- [ ] T5.8 写测试 `tests/python/unit/test_team_orchestrator.py`:
  验证 _plan_node 用 deepagent 拆任务 + _todos_to_team_tasks 转换 + 安全校验

### P1.3: 删除 team_plan / team_progress / team_result 事件

- [ ] T6.1 [team/orchestrator.py:133-144](../../../backend/app/team/orchestrator.py#L133-L144):
  删除 `make_team_event("team_plan", ...)` 调用(改由 streaming 层从 state.todos 产出)
- [ ] T6.2 [team/scheduler.py](../../../backend/app/team/scheduler.py) 删除
  `team_progress` / `team_result` 事件 emit 逻辑
  (子任务进度通过 todo_update 反映,结果通过 tool_result 反映)
- [ ] T6.3 [team/scheduler.py:50](../../../backend/app/team/scheduler.py#L50)
  `_PASSTHROUGH_EVENTS`:删除 `team_progress` / `team_result`(保留 `team_done`)
- [ ] T6.4 [sse/events.py:73-75](../../../backend/app/sse/events.py#L73-L75):
  从 `_JSON_EVENTS` 删除 `"team_plan"` / `"team_progress"` / `"team_result"`
- [ ] T6.5 [sse/events.py](../../../backend/app/sse/events.py) 删除 `make_team_event` 函数
  (team_done 用 `make_sse_event` 直接构造)
- [ ] T6.6 [team/orchestrator.py](../../../backend/app/team/orchestrator.py) team_done 事件:
  改用 `make_sse_event("team_done", {"status": "done" | "error"})` 直接构造
- [ ] T6.7 更新 `tests/python/unit/test_team_path.py`:删除 team_plan/team_progress 断言,
  增加 todo_update 断言

## 阶段三(P2):前端契约破坏性升级

### P2.1: api-types.ts 事件类型清理

- [ ] T7.1 [shared/api-types.ts](../../../frontend/shared/api-types.ts):
  - 新增 `export type TodoStatus = "pending" | "in_progress" | "completed";`
  - `todo_update` 事件 payload 改为
    `{todos: {content: string; status: TodoStatus; task_id?: string}[]}`
  - 删除 `PlanTask` interface
  - 删除 `plan` / `plan_update` 事件类型
  - 删除 `team_plan` / `team_progress` / `team_result` 事件类型
  - 保留 `team_done`(payload 简化为 `{status: "done" | "error"}`)

### P2.2: useChatStream.ts 适配

- [ ] T8.1 [hooks/useChatStream.ts:9-13](../../../frontend/renderer/hooks/useChatStream.ts#L9-L13)
  `TodoItem`:改为 `{content: string; status: TodoStatus; taskId?: string}`
- [ ] T8.2 [hooks/useChatStream.ts:21-39](../../../frontend/renderer/hooks/useChatStream.ts#L21-L39)
  `normalizeTodos`:从原生 `{content, status, task_id}` 归一化(不再处理 `{text, done}`)
- [ ] T8.3 [hooks/useChatStream.ts:41-56](../../../frontend/renderer/hooks/useChatStream.ts#L41-L56):
  删除 `normalizePlanTasks` 函数
- [ ] T8.4 [hooks/useChatStream.ts:282-291](../../../frontend/renderer/hooks/useChatStream.ts#L282-L291):
  删除 `case "plan"` / `case "plan_update"` 分支
- [ ] T8.5 [hooks/useChatStream.ts:330-372](../../../frontend/renderer/hooks/useChatStream.ts#L330-L372):
  删除 `case "team_plan"` / `case "team_progress"` / `case "team_result"` 分支
- [ ] T8.6 [hooks/useChatStream.ts:292-329](../../../frontend/renderer/hooks/useChatStream.ts#L292-L329)
  `case "todo_update"`:改为调用 `normalizeTodos(e.todos, taskId)`(已是原生 schema,
  归一化逻辑简化)
- [ ] T8.7 保留 `case "team_done"` 分支(简化处理,只更新 team part status)

### P2.3: TodoProgress.tsx 三态渲染

- [ ] T9.1 [components/chat/TodoProgress.tsx:45-74](../../../frontend/renderer/components/chat/TodoProgress.tsx#L45-L74)
  `TodoList` 组件:渲染三态
  - `status === "completed"` → 绿色 ✓(原 done 样式)
  - `status === "in_progress"` → 黄色 ◐ + spin 动画(原 not-done 样式 + animate-spin)
  - `status === "pending"` → 灰色 ○(新样式)
  - 文本从 `t.text` 改为 `t.content`
- [ ] T9.2 [components/chat/TodoProgress.tsx:76-117](../../../frontend/renderer/components/chat/TodoProgress.tsx#L76-L117)
  `TodoProgress` 组件:`completedTodos` 计算从 `t.done` 改为 `t.status === "completed"`
- [ ] T9.3 `groupTodosByTaskId` 逻辑不变(taskId 仍用于 Team 多子任务分组)
- [ ] T9.4 更新 `TodoProgress.test.tsx`:测试三态渲染 + content 字段

### P2.4: tasks store + TaskTimeline 适配

- [ ] T10.1 [stores/tasks.ts:8](../../../frontend/renderer/stores/tasks.ts#L8)
  `Task.todos`:`{text: string; done: boolean}[]` → `{content: string; status: TodoStatus}[]`
- [ ] T10.2 [stores/tasks.ts:39-73](../../../frontend/renderer/stores/tasks.ts#L39-L73)
  `migrateTasksState`:增加 v3 → v4 迁移
  ```typescript
  if (version < 4) {
    tasks = tasks.map((t) => ({
      ...t,
      todos: (t.todos ?? []).map((old) => ({
        content: typeof old.text === "string" ? old.text : String(old.text ?? ""),
        status: old.done ? "completed" as const : "pending" as const,
      })),
    }));
  }
  ```
- [ ] T10.3 [stores/tasks.ts:101](../../../frontend/renderer/stores/tasks.ts#L101)
  `version: 3` → `version: 4`
- [ ] T10.4 [components/workspace/TaskTimeline.tsx](../../../frontend/renderer/components/workspace/TaskTimeline.tsx):
  `completedTodos` 计算适配三态(`t.status === "completed"`)
- [ ] T10.5 grep 验证无残留 `t.done` / `t.text` 引用(在 todo 相关组件中)

## 阶段四(P3):CLI + 文档 + 测试收尾

### P3.1: CLI renderer 适配

- [ ] T11.1 [cli/renderer.py:155-167](../../../backend/app/cli/renderer.py#L155-L167)
  `_render_todo_update`:从 `{text, done}` 改为 `{content, status}` 三态渲染
  - completed → `✓` 绿色
  - in_progress → `◐` 黄色
  - pending → `○` 灰色
- [ ] T11.2 [cli/renderer.py:242-250](../../../backend/app/cli/renderer.py#L242-L250):
  删除 `_render_plan` / `_render_plan_update` 方法
- [ ] T11.3 [cli/renderer.py:181-231](../../../backend/app/cli/renderer.py#L181-L231):
  删除 `_render_team_plan` / `_render_team_progress` / `_render_team_result` 方法
- [ ] T11.4 保留 `_render_team_done`(简化处理)

### P3.2: 文档更新

- [ ] T12.1 [AGENTS.md](../../../AGENTS.md) §13 SSE 事件契约:
  - 删除 `plan` / `plan_update` / `team_plan` / `team_progress` / `team_result`
  - `todo_update` schema 更新为 `{todos: [{content, status, task_id?}], trace_id?}`
  - 保留 `team_done`(payload `{status}`)
- [ ] T12.2 [docs/agents/02-sse-event-contract.md](../../../docs/agents/02-sse-event-contract.md)
  同步更新(若存在)
- [ ] T12.3 [docs/agents/03-team-architecture.md](../../../docs/agents/03-team-architecture.md)
  (若存在):更新 Team Orchestrator 改用 write_todos 的说明

### P3.3: 测试收尾

- [ ] T13.1 删除 `tests/python/unit/test_plan_extraction.py`(若存在)
- [ ] T13.2 更新 `tests/python/unit/test_team_path.py`:适配 write_todos + 删除 team_* 事件断言
- [ ] T13.3 更新 `tests/python/unit/test_streaming.py`(若存在):删除手造 todo 事件断言
- [ ] T13.4 运行全量 `pytest tests/python/`,确保全部通过
- [ ] T13.5 运行前端 `pnpm test`,确保 TodoProgress.test.tsx 等通过
- [ ] T13.6 运行 `ruff check backend/`,确保无 lint 错误
- [ ] T13.7 端到端冒烟测试:DeepAgent 对话 + Team 对话,验证 todo_update 三态正常显示

## 预期修改文件

### 后端新建文件
- 无(harness.py 已在 2026-07-08-migration 创建)

### 后端删除文件
- `backend/app/utils/plan_extraction.py` — 手写 JSON 解析(整文件删除)

### 后端修改文件
- `backend/app/sse/events.py` — 新增 make_todo_update_event,删除 make_todo_event/make_team_event,
  清理 _JSON_EVENTS 白名单
- `backend/app/deepagent/streaming.py` — 读取 state.todos + diff,删除手造 todo 事件,
  删除 plan_extraction 调用
- `backend/app/deepagent/agent.py` — _DEEP_SYSTEM_PROMPT 删除 write_todos 指令
- `backend/app/team/planner.py` — 删除 TeamPlan schema + prompt,新增 _ORCHESTRATOR_SYSTEM_PROMPT +
  _todos_to_team_tasks
- `backend/app/team/orchestrator.py` — _plan_node 改用 deepagent,删除 team_plan 事件,
  _dispatch_node 用 todos + task_index
- `backend/app/team/blackboard.py` — TeamState 增加 todos + _merge_todos reducer,
  SubtaskState 增加 task_index
- `backend/app/team/scheduler.py` — 子任务节点更新 todo status,删除 team_progress/team_result 事件
- `backend/app/cli/renderer.py` — _render_todo_update 三态,删除 plan/team_* 渲染方法

### 前端修改文件
- `frontend/shared/api-types.ts` — 新增 TodoStatus,更新 todo_update,删除 plan/team_* 类型
- `frontend/renderer/hooks/useChatStream.ts` — TodoItem 升级,删除 normalizePlanTasks/plan/team_* 分支
- `frontend/renderer/components/chat/TodoProgress.tsx` — 三态渲染,completedTodos 计算
- `frontend/renderer/stores/tasks.ts` — Task.todos 升级,migrate v3→v4,version 3→4
- `frontend/renderer/components/workspace/TaskTimeline.tsx` — completedTodos 适配三态

### 后端测试文件
- `tests/python/unit/test_todo_streaming.py` — 新增,验证 streaming 层 todo_update
- `tests/python/unit/test_team_todos_reducer.py` — 新增,验证 _merge_todos reducer
- `tests/python/unit/test_team_orchestrator.py` — 新增/更新,验证 _plan_node 用 deepagent
- `tests/python/unit/test_team_path.py` — 更新,删除 team_* 事件断言
- `tests/python/unit/test_plan_extraction.py` — 删除(若存在)
- `tests/python/unit/test_streaming.py` — 更新(若存在)

### 前端测试文件
- `frontend/renderer/components/chat/TodoProgress.test.tsx` — 更新,三态渲染测试

### 文档文件
- `AGENTS.md` — §13 SSE 事件契约更新
- `docs/agents/02-sse-event-contract.md` — 同步(若存在)

## 规模判定

- 涉及文件数: ~17(1 删除 + 8 后端修改 + 5 前端修改 + 3 测试新增/更新)
- 涉及模块数: 4(deepagent/ + team/ + sse/ + frontend renderer/)
- 规模: **L(大改)** — 跨前后端 + 破坏性契约变更,需全流程执行

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T0 | 前置验证(deepagent tools=[] 仍注入 TodoListMiddleware) | tests/ | write_todos 可调用,state.todos 可读 | ⬜ |
| T1 | streaming 层读 state.todos + make_todo_update_event | sse/events.py, deepagent/streaming.py | todo_update 事件是原生 schema,todos 未变化不重复 yield | ⬜ |
| T2 | 删除 plan_extraction.py + plan/plan_update 事件 | utils/plan_extraction.py, streaming.py, sse/events.py | grep 无残留引用 | ⬜ |
| T3 | _DEEP_SYSTEM_PROMPT 清理 write_todos 指令 | deepagent/agent.py | 系统提示无 write_todos 指令(由 middleware 注入) | ⬜ |
| T4 | TeamState todos + _merge_todos reducer | team/blackboard.py | 并行子任务 todos 正确归并 | ⬜ |
| T5 | Team Orchestrator 改用 deepagent + write_todos | team/planner.py, team/orchestrator.py | LLM 调用 write_todos 拆任务,_todos_to_team_tasks 转换正确 | ⬜ |
| T6 | 删除 team_plan/team_progress/team_result 事件 | team/orchestrator.py, team/scheduler.py, sse/events.py | 只保留 team_done,grep 无残留 | ⬜ |
| T7 | api-types.ts 事件类型清理 | frontend/shared/api-types.ts | TodoStatus 导出,plan/team_* 类型删除 | ⬜ |
| T8 | useChatStream.ts 适配 | frontend/renderer/hooks/useChatStream.ts | TodoItem 升级,plan/team_* 分支删除 | ⬜ |
| T9 | TodoProgress.tsx 三态渲染 | frontend/renderer/components/chat/TodoProgress.tsx | 三态正确渲染,completedTodos 计算正确 | ⬜ |
| T10 | tasks store + TaskTimeline 适配 + migrate v3→v4 | frontend/renderer/stores/tasks.ts, TaskTimeline.tsx | 旧数据正确迁移,version=4 | ⬜ |
| T11 | CLI renderer 三态 + 删除 plan/team_* 渲染 | backend/app/cli/renderer.py | _render_todo_update 三态,plan/team_* 方法删除 | ⬜ |
| T12 | 文档更新(AGENTS.md §13 + docs/agents/) | AGENTS.md, docs/agents/ | SSE 契约同步 | ⬜ |
| T13 | 测试收尾 + 全量验证 | tests/, pnpm test, pytest, ruff | 全部通过,无 lint 错误 | ⬜ |
