# Proposal: 迁移到 deepagents 原生 Todo 系统(write_todos 统一)

## Why

AgentX 在 [2026-07-08-deepagents-migration](../2026-07-08-deepagents-migration/proposal.md) 的
Decision 11 中声称"删除 JSON plan,用 `write_todos` 替换",但实际执行后**只删除了系统提示里的
JSON plan 指令,未真正消费 deepagents 原生 `TodoListMiddleware` 维护的结构化 `state.todos`**。
当前存在三套平行的非标准任务追踪系统,严重违反 AGENTS.md §1.1「优先用现成框架」和
§3 R12「手写 LLM 输出解析」:

### 现状三套自造轮子

| # | 自造轮子 | 位置 | 问题 |
|---|---|---|---|
| 1 | **手造 todo_update 字符串事件** | [deepagent/streaming.py:205-264](../../../backend/app/deepagent/streaming.py#L205-L264) | 每次 tool_call/tool_result 都 yield 一个伪 todo 事件(`"调用工具: xxx"` / `"工具 xxx 完成"`),把原生结构化 TodoList 降级成"工具调用进度条"。`astream(stream_mode="values")` 拿到 state 后**只读 `state.get("messages")`,完全没读 `state.get("todos")`** |
| 2 | **手写 JSON plan 解析** | [utils/plan_extraction.py:73-113](../../../backend/app/utils/plan_extraction.py#L73-L113) | 用 `parse_json_markdown` 从 LLM 文本提取 `{"plan": [...]}` / `{"plan_update": {...}}`,产出 `plan` / `plan_update` SSE 事件。这是手写 LLM 输出解析(违反 R12),与原生 `write_todos` 结构化工具调用平行存在 |
| 3 | **Team 路径独立 TeamPlan schema** | [team/planner.py:43-55](../../../backend/app/team/planner.py#L43-L55) + [team/orchestrator.py:85-144](../../../backend/app/team/orchestrator.py#L85-L144) | 用 `llm.with_structured_output(TeamPlan)` + 自定义 `TeamPlan`/`TeamPlanItem` pydantic schema 拆任务,产出 `team_plan` / `team_progress` / `team_result` 事件,与 deepagents 主路径完全隔离 |

### 原生能力被"声明但未消费"

deepagents 0.6+ 的 `create_deep_agent` 默认在主 agent + 所有子 agent 的 middleware 栈首部注入
`TodoListMiddleware`(见 [.venv/.../deepagents/graph.py:774](../../../.venv/Lib/site-packages/deepagents/graph.py#L774)),
提供:

- **`write_todos` 标准工具**:LLM 调用它在 `state.todos` 写入结构化 todo 列表
- **`Todo` TypedDict**:`{content: str, status: Literal["pending","in_progress","completed"]}`
- **`PlanningState`**:`todos` 字段经 LangGraph checkpointer 自动持久化,支持中断断点续跑
- **内置系统提示**:`WRITE_TODOS_SYSTEM_PROMPT` 自动注入,指导 LLM 何时用/如何用

但项目代码**从未读取 `state.todos`**——streaming 层只读 messages,导致 LLM 调用 `write_todos` 后
结构化数据被丢弃,前端只能看到降级的字符串事件。

### 前端契约与原生 schema 不兼容

| 维度 | 当前前端 | 原生 deepagents |
|---|---|---|
| TodoItem 字段 | `{text, done, taskId?}` | `{content, status}` |
| 状态语义 | done/undone 二态 | pending/in_progress/completed 三态 |
| 多任务分组 | `task_id` 字段(Team 子任务) | 无(单 agent 内 todo 列表) |
| 事件类型 | `todo_update` + `plan` + `plan_update` + `team_plan` + `team_progress` + `team_result`(6 种) | `todo_update`(1 种,从 state.todos 透传) |

本次迁移**完成上次未完成的 `write_todos` 迁移**,真正消费原生 `state.todos`,删除三套自造轮子,
统一前后端 Todo schema 到原生 `{content, status}`,并按用户决策将 Team 路径也纳入统一。

## What Changes

### 阶段一(P0):主路径消费原生 state.todos

#### P0.1: streaming 层读取 state.todos 并产出原生 schema 事件

- [deepagent/streaming.py](../../../backend/app/deepagent/streaming.py) `_stream_agent_events`:
  每次 `astream(stream_mode="values")` 到达 state 时,**读取 `state.get("todos")`**,
  与上次快照 diff,产出 `todo_update` SSE 事件(payload 为原生 `[{content, status, task_id?}]`)
- **删除手造的字符串 todo 事件**:移除 [streaming.py:205-206](../../../backend/app/deepagent/streaming.py#L205-L206)
  (ToolMessage → `make_todo_event(f"工具 {tool_name} 完成")`) 和
  [streaming.py:263-264](../../../backend/app/deepagent/streaming.py#L263-L264)
  (AIMessage tool_calls → `make_todo_event(f"调用工具: {tc_name}")`)
- 保留 tool_call / tool_result 事件(工具调用追踪与 todo 追踪是两个正交维度)

#### P0.2: 删除 plan_extraction.py 手写 JSON 解析

- 删除 [utils/plan_extraction.py](../../../backend/app/utils/plan_extraction.py) 整个文件
  (`extract_plan_or_update` / `_normalize_plan_items`)
- 删除 [deepagent/streaming.py:282-286](../../../backend/app/deepagent/streaming.py#L282-L286)
  中调用 `extract_plan_or_update` 的分支
- 删除 SSE 事件白名单中的 `plan` / `plan_update`(见 [sse/events.py:77-78](../../../backend/app/sse/events.py#L77-L78))

#### P0.3: 系统提示清理

- [deepagent/agent.py:42-44](../../../backend/app/deepagent/agent.py#L42-L44) `_DEEP_SYSTEM_PROMPT`:
  删除"请使用 write_todos 工具维护任务清单"指令——`TodoListMiddleware` 自带
  `WRITE_TODOS_SYSTEM_PROMPT` 自动注入(见 [.venv/.../langchain/agents/middleware/todo.py:119-136](../../../.venv/Lib/site-packages/langchain/agents/middleware/todo.py#L119-L136)),
  项目层提示重复且可能冲突
- Work Supervisor / Coding Expert 的系统提示同步清理(若有类似指令)

### 阶段二(P1):Team 路径统一到 write_todos

#### P1.1: Team Orchestrator 改用 deepagent + write_todos 拆任务

- [team/orchestrator.py](../../../backend/app/team/orchestrator.py) `_plan_node`:
  从 `llm.with_structured_output(TeamPlan)` 改为调用一个**轻量 deepagent**(带 `write_todos` 工具)
  拆任务,LLM 调用 `write_todos` 写入 `state.todos`
- 删除 [team/planner.py](../../../backend/app/team/planner.py) 的 `TeamPlan` / `TeamPlanItem`
  pydantic schema + `_postprocess_plan` + `_ORCHESTRATOR_PROMPT`(改由 deepagent 系统提示驱动)
- 保留 `_validate_task` / `_looks_like_dangerous_task` / `_build_project_context`(安全约束不变)

#### P1.2: TeamState 增加 todos 字段 + reducer

- [team/blackboard.py](../../../backend/app/team/blackboard.py) `TeamState`:
  增加 `todos: Annotated[list[dict], _merge_todos]` 字段
- 新增 `_merge_todos` reducer:按 `task_id`(=子任务序号)合并,子任务节点返回的部分 todos
  更新覆盖到全局 state
- `_dispatch_node` 读 `state.todos`,为每个 todo 创建 `Send`(task_index = todo 在列表中的序号)
- 子任务节点(`_deep_node` / `_code_node` 等)开始时把对应 todo 标记为 `in_progress`,
  完成时标记为 `completed`,通过 reducer 合并

#### P1.3: 删除 team_plan / team_progress / team_result 事件

- 删除 [team/orchestrator.py:133-144](../../../backend/app/team/orchestrator.py#L133-L144)
  的 `team_plan` 事件 emit(改由 streaming 层从 state.todos 产出 todo_update)
- 删除 [team/scheduler.py:50](../../../backend/app/team/scheduler.py#L50)
  `_PASSTHROUGH_EVENTS` 中的 `team_progress`(子任务进度通过 todo_update 反映)
- 删除 SSE 事件白名单中的 `team_plan` / `team_progress` / `team_result` / `team_done`
  (见 [sse/events.py:73-76](../../../backend/app/sse/events.py#L73-L76))
- **保留 `team_done`**:Team 整体完成信号仍需要(用于前端关闭 team part),
  但 payload 简化为 `{status: "done" | "error"}`

### 阶段三(P2):前端契约破坏性升级

#### P2.1: TodoItem schema 升级到原生

- [hooks/useChatStream.ts](../../../frontend/renderer/hooks/useChatStream.ts) `TodoItem`:
  `{text, done, taskId?}` → `{content, status, taskId?}`
  (status: `"pending" | "in_progress" | "completed"`)
- `normalizeTodos` 函数:从原生 `{content, status}` 归一化(不再处理 `{text, done}`)
- 删除 `normalizePlanTasks` 函数(plan 事件已废弃)

#### P2.2: api-types.ts 事件类型清理

- [shared/api-types.ts](../../../frontend/shared/api-types.ts):
  - `todo_update` 事件 payload 改为 `{todos: {content: string; status: TodoStatus; task_id?: string}[]}`
  - 删除 `plan` / `plan_update` 事件类型
  - 删除 `team_plan` / `team_progress` / `team_result` 事件类型
  - 保留 `team_done`(payload 简化)
  - 新增 `TodoStatus` 类型导出
- 删除 `PlanTask` interface

#### P2.3: TodoProgress.tsx 渲染三态

- [components/chat/TodoProgress.tsx](../../../frontend/renderer/components/chat/TodoProgress.tsx):
  - `TodoList` 组件:渲染三态(pending=○, in_progress=◐ 旋转, completed=✓)
  - `completedTodos` 计算:从 `t.done` 改为 `t.status === "completed"`
  - `groupTodosByTaskId` 逻辑不变(taskId 仍用于 Team 多子任务分组)

#### P2.4: tasks store + TaskTimeline 适配

- [stores/tasks.ts](../../../frontend/renderer/stores/tasks.ts) `Task.todos`:
  `{text, done}[]` → `{content, status}[]`
- [components/workspace/TaskTimeline.tsx](../../../frontend/renderer/components/workspace/TaskTimeline.tsx):
  `completedTodos` 计算适配三态
- persist migrate 增加 v3 → v4:旧 `{text, done}` 转换为 `{content: text, status: done ? "completed" : "pending"}`

#### P2.5: useChatStream 事件处理清理

- [hooks/useChatStream.ts](../../../frontend/renderer/hooks/useChatStream.ts):
  - 删除 `case "plan"` / `case "plan_update"` 分支
  - 删除 `case "team_plan"` / `case "team_progress"` / `case "team_result"` 分支
  - `case "todo_update"`:改为从原生 `{content, status}` 归一化
  - 保留 `case "team_done"`(简化处理)

### 阶段四(P3):CLI + 文档 + 测试

#### P3.1: CLI renderer 适配

- [cli/renderer.py](../../../backend/app/cli/renderer.py) `_render_todo_update`:
  从 `{text, done}` 改为 `{content, status}` 三态渲染
- 删除 `_render_plan` / `_render_plan_update` / `_render_team_plan` / `_render_team_progress` /
  `_render_team_result`(保留 `_render_team_done`)

#### P3.2: 文档更新

- [AGENTS.md](../../../AGENTS.md) §13 SSE 事件契约:
  - 删除 `plan` / `plan_update` / `team_plan` / `team_progress` / `team_result`
  - `todo_update` schema 更新为原生 `{content, status, task_id?}`
  - 保留 `team_done`
- [docs/agents/02-sse-event-contract.md](../../../docs/agents/02-sse-event-contract.md) 同步更新

#### P3.3: 测试更新

- 删除 `tests/python/unit/test_plan_extraction.py`(若存在)
- 新增 `tests/python/unit/test_todo_streaming.py`:验证 streaming 层从 state.todos 产出 todo_update
- 更新 `tests/python/unit/test_team_path.py`:适配 Team 改用 write_todos
- 更新 `tests/python/unit/test_streaming.py`(若存在):删除手造 todo 事件断言
- 前端 `TodoProgress.test.tsx` 适配三态渲染

## Capabilities

### Modified Capabilities

- `todo-system`: 从三套自造轮子(手造 todo_update 字符串 + plan_extraction JSON 解析 + Team TeamPlan schema)
  统一到 deepagents 原生 `TodoListMiddleware` + `write_todos` 工具 + `state.todos` 持久化

### Removed Artifacts

- `backend/app/utils/plan_extraction.py`(整文件删除)
- `TeamPlan` / `TeamPlanItem` pydantic schema(team/planner.py)
- `_postprocess_plan` / `_ORCHESTRATOR_PROMPT`(team/planner.py)
- SSE 事件类型:`plan` / `plan_update` / `team_plan` / `team_progress` / `team_result`
- 前端 `PlanTask` interface + `normalizePlanTasks` 函数

## Impact

- **后端**:
  - 修改 ~8 个文件(deepagent/streaming.py / deepagent/agent.py / team/orchestrator.py /
    team/planner.py / team/blackboard.py / team/scheduler.py / sse/events.py / cli/renderer.py)
  - 删除 1 个文件(utils/plan_extraction.py)
  - team/planner.py 大幅瘦身(删除 schema + prompt,保留安全校验函数)
- **前端**:
  - 修改 ~5 个文件(useChatStream.ts / api-types.ts / TodoProgress.tsx / tasks.ts / TaskTimeline.tsx)
  - 破坏性契约变更:TodoItem schema 从 `{text, done}` → `{content, status}`
  - persist migrate v3 → v4(旧数据转换)
- **API**:无新增端点
- **SSE 事件契约**:**破坏性变更**——删除 5 种事件,1 种事件 schema 变更(todo_update)
- **测试**:更新 ~5 个测试文件,新增 1 个测试文件
- **文档**:更新 AGENTS.md §13 + docs/agents/02-sse-event-contract.md
- **依赖**:无新增(deepagents 已在 2026-07-08-migration 升级到 0.6+)

## Future Extensibility

- P4: 探索 `TodoListMiddleware` 子类化,支持自定义 todo 元数据(如优先级、截止时间)
- P4: 评估 LangGraph `astream(stream_mode="updates")` 替代 `stream_mode="values"`,
  实现 todos 字段级增量推送(当前用 values + diff)
- P4: 前端 TodoProgress 支持折叠/展开子任务组(Team 多子代理场景)
