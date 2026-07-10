# 任务追踪 — 右侧任务面板任务流 + Todo 列表展示

## 阶段零:前置验证

- [ ] T0.1 验证 TaskTimeline.tsx 确实无任何 import(grep 确认死代码)
- [ ] T0.2 验证 CompactTaskList.tsx 仅被 WorkspacePanel.tsx 引用(grep 确认删除安全)
- [ ] T0.3 验证 useChatStream 的 setTodos 调用点(grep 确认所有引用点,为阶段 3 删除做准备)

## 阶段一(P0):激活 TaskTimeline,替换 CompactTaskList

### P0.1: TaskTimeline sessionId 过滤修复

- [ ] T1.1 [TaskTimeline.tsx:189](../../../frontend/renderer/components/workspace/TaskTimeline.tsx#L189)
  `TaskTimeline` 函数:
  - 新增 `import { useChatStore } from "@/stores/chat";`
  - 新增 `const currentId = useChatStore((s) => s.currentId);`
  - `const tasks = useTasksStore((s) => s.tasks)` 改为用 useMemo 按 sessionId 过滤
  - 空状态文案不变
- [ ] T1.2 写测试 `TaskTimeline.test.tsx`:验证只渲染当前会话任务,不泄露其他会话

### P0.2: WorkspacePanel 替换 CompactTaskList

- [ ] T2.1 [WorkspacePanel.tsx:13](../../../frontend/renderer/components/workspace/WorkspacePanel.tsx#L13):
  `import { CompactTaskList }` → `import { TaskTimeline }`
- [ ] T2.2 [WorkspacePanel.tsx:144](../../../frontend/renderer/components/workspace/WorkspacePanel.tsx#L144):
  `<CompactTaskList />` → `<TaskTimeline />`
- [ ] T2.3 删除 [CompactTaskList.tsx](../../../frontend/renderer/components/workspace/CompactTaskList.tsx) 整文件
- [ ] T2.4 grep 验证无残留 `CompactTaskList` 引用

## 阶段二(P1):SSE 事件扩展 + Task 模型扩展 + 任务流分组

### P1.1: 后端 make_todo_update_event 扩展

- [ ] T3.1 [sse/events.py:98-127](../../../backend/app/sse/events.py#L98-L127) `make_todo_update_event`:
  新增 `source: str | None = None` 和 `parent_task_id: str | None = None` 参数
- [ ] T3.2 payload 构造逻辑:source/parent_task_id 非 None 时注入 payload
- [ ] T3.3 docstring 更新:说明 source 和 parent_task_id 的用途
- [ ] T3.4 写测试 `test_todo_update_event.py`:验证 source/parent_task_id 字段正确注入

### P1.2: 后端 _emit_todo_in_progress 注入 source + parent_task_id

- [ ] T4.1 [orchestrator.py:234-244](../../../backend/app/team/orchestrator.py#L234-L244)
  `_emit_todo_in_progress`:
  - 新增 `agent_role: str` 参数
  - 调用 `make_todo_update_event` 时传入 `source=agent_role` + `parent_task_id=parent_thread_id`
- [ ] T4.2 [orchestrator.py](../../../backend/app/team/orchestrator.py) 所有调用 `_emit_todo_in_progress` 的位置,
  传入 `agent_role=task.agent`:
  - `_deep_node`(L261 附近)
  - `_code_node`(L301 附近)
  - `_builtin_node`(L333 附近)
  - `_team_role_node`(L362 附近)
  - `_custom_node`(L392 附近)
- [ ] T4.3 写测试 `test_team_path.py`:验证子任务 todo_update 事件含 source + parent_task_id

### P1.3: 后端主路径 todo_update 注入 source

**注**:`_stream_agent_events` 已有 `source` 参数(L46,默认 "deep"),`approval_runner.py` 已传 `source=source`。
本任务只需在 `make_todo_update_event` 调用时把已有的 `source` 传进去。

- [ ] T5.1 [streaming.py:184-188](../../../backend/app/deepagent/streaming.py#L184-L188)
  `make_todo_update_event` 调用新增 `source=source` 参数(复用已有 source 变量)
- [ ] T5.2 验证调用链 source 值正确:
  - `run_work_supervisor` → `approval_runner` → `_stream_agent_events(source="work")`
  - `run_coding_expert` → `approval_runner` → `_stream_agent_events(source="coding")`
  - 验证 approval_runner 透传 source 到 `_stream_agent_events`(grep 确认)
- [ ] T5.3 写测试:验证主路径 todo_update 事件 payload 含 source 字段

### P1.4: 前端 api-types.ts 扩展

- [ ] T6.1 [api-types.ts:55-60](../../../frontend/shared/api-types.ts#L55-L60) todo_update 事件类型:
  新增 `source?: string` 和 `parent_task_id?: string` 字段
- [ ] T6.2 注释更新:说明 source 和 parent_task_id 的语义

### P1.5: 前端 Task 模型扩展 + migrate v4→v5

- [ ] T7.1 [stores/tasks.ts:5-14](../../../frontend/renderer/stores/tasks.ts#L5-L14) Task 接口:
  新增 `parentTaskId?: string` / `taskSource?: "work" | "coding" | "team"` / `agentRole?: string`
- [ ] T7.2 [stores/tasks.ts:43-86](../../../frontend/renderer/stores/tasks.ts#L43-L86) migrateTasksState:
  新增 v4→v5 迁移(补默认值,非破坏性)
- [ ] T7.3 [stores/tasks.ts:114](../../../frontend/renderer/stores/tasks.ts#L114) `version: 4` → `version: 5`
- [ ] T7.4 写测试 `tasks.test.ts`:验证 migrate v4→v5 正确补默认值

### P1.6: 前端 useChatStream todo_update 处理逻辑扩展

- [ ] T8.1 [useChatStream.ts:273-310](../../../frontend/renderer/hooks/useChatStream.ts#L273-L310)
  todo_update case:
  - 提取 `parent_task_id` 和 `source` 字段
  - 有 parent_task_id + source 时:创建/更新子任务(幂等 ID = `{parent_task_id}-child-{source}`)
  - 无 parent_task_id 时:原有主任务逻辑,addTask 时填 taskSource
- [ ] T8.2 写测试 `useChatStream.test.ts`:验证子任务创建 + 主任务创建两条路径

### P1.7: 前端 TaskTimeline 父子分组渲染

- [ ] T9.1 [TaskTimeline.tsx](../../../frontend/renderer/components/workspace/TaskTimeline.tsx):
  - 按 parentTaskId 分组:mainTasks(无 parent) + childTasksByParent(Map)
  - 排序:mainTasks 按 createdAt 降序
  - 渲染:主任务卡片后嵌套渲染其子任务(缩进 + 左边框)
- [ ] T9.2 TaskCard 新增 `compact?: boolean` 参数:
  - compact 模式:更小缩进 + agentRole 标签(复用 formatTaskLabel)
  - 非 compact:既有渲染(标题 + 进度条 + todo 列表)
- [ ] T9.3 写测试:验证父子任务嵌套渲染 + compact 模式

## 阶段三(P2):数据源统一

### P2.1: ChatView 删除本地 todos state

**setTodos 引用点清单**(grep 确认共 5 处):
- ChatView.tsx:66 `const [todos, setTodos] = useState<TodoItem[]>([]);`
- ChatView.tsx:88 `setTodos,`(传给 useChatStream)
- ChatView.tsx:188 `setTodos([]);`(切会话时)
- ChatView.tsx:419 `setTodos([]);`(handleSend 时)
- ChatView.tsx:518 `setTodos([]);`(编辑重发时)

- [ ] T10.1 [ChatView.tsx:66](../../../frontend/renderer/components/chat/ChatView.tsx#L66):
  删除 `const [todos, setTodos] = useState<TodoItem[]>([]);`
- [ ] T10.2 ChatView 新增从 useTasksStore 派生 todos 的 selector:
  - 过滤当前 sessionId + running 状态 + 无 parentTaskId 的主任务
  - 用 `useShallow` 或 `useMemo` 稳定引用避免无限重渲染
- [ ] T10.3 删除 ChatView.tsx 所有 `setTodos([])` 调用:L188, L419, L518
- [ ] T10.4 [ChatView.tsx:88](../../../frontend/renderer/components/chat/ChatView.tsx#L88):
  useChatStream 调用删除 `setTodos` 参数
- [ ] T10.5 [ChatView.tsx:496](../../../frontend/renderer/components/chat/ChatView.tsx#L496):
  `completedTodos` 计算改用 store 派生的 todos

### P2.2: useChatStream 删除 setTodos 双写

**setTodos 引用点清单**(grep 确认共 6 处):
- useChatStream.ts:52 `setTodos: ...` (UseChatStreamArgs 类型声明)
- useChatStream.ts:72 `const { ..., setTodos, ... } = args;` (解构)
- useChatStream.ts:101 `const callbacksRef = useRef({ setTodos, ... });` (ref 初始化)
- useChatStream.ts:103 `callbacksRef.current = { setTodos, ... };` (ref 更新)
- useChatStream.ts:104 `}, [setTodos, ...]);` (useEffect 依赖)
- useChatStream.ts:279 `callbacksRef.current.setTodos(...)` (todo_update 调用)

- [ ] T11.1 [useChatStream.ts:52](../../../frontend/renderer/hooks/useChatStream.ts#L52)
  UseChatStreamArgs:删除 `setTodos` 字段
- [ ] T11.2 [useChatStream.ts:72](../../../frontend/renderer/hooks/useChatStream.ts#L72)
  解构删除 `setTodos`
- [ ] T11.3 [useChatStream.ts:101,103,104](../../../frontend/renderer/hooks/useChatStream.ts#L101)
  callbacksRef 删除 setTodos(ref 初始化 + 更新 + useEffect 依赖)
- [ ] T11.4 [useChatStream.ts:279-284](../../../frontend/renderer/hooks/useChatStream.ts#L279-L284)
  todo_update case:删除 `callbacksRef.current.setTodos(...)` 调用
- [ ] T11.5 grep 验证全项目无残留 `setTodos` 引用

## 阶段四(P2):UX 增强

### P3.1: TodoProgress 加进度条

- [ ] T12.1 [TodoProgress.tsx:89-130](../../../frontend/renderer/components/chat/TodoProgress.tsx#L89-L130)
  TodoProgress 组件:
  - 计算 `progress = completedTodos / todos.length * 100`
  - 在标题行下方、TodoList 上方插入进度条 div
  - 进度条样式: `h-1 rounded-full bg-subtle` + 内部 `bg-brand-500 transition-all duration-300`
- [ ] T12.2 写测试验证进度条宽度随 completedTodos 变化

### P3.2: 状态切换过渡动画

- [ ] T13.1 [TodoProgress.tsx](../../../frontend/renderer/components/chat/TodoProgress.tsx) TodoList:
  todo 项 badge 加 `transition-colors duration-200`
- [ ] T13.2 [TaskTimeline.tsx](../../../frontend/renderer/components/workspace/TaskTimeline.tsx) TaskCard:
  todo 项 badge 加 `transition-colors duration-200`

### P3.3: 右侧面板可调宽

- [ ] T14.1 [App.tsx:38](../../../frontend/renderer/App.tsx#L38):新增 `const [panelWidth, setPanelWidth] = useState(288);`
- [ ] T14.2 App.tsx 新增 `startResize` 函数(mousedown + mousemove + mouseup)
  - 宽度范围 240px-480px
  - 向左拖 = 增宽(delta = startX - clientX)
- [ ] T14.3 [App.tsx:296-304](../../../frontend/renderer/App.tsx#L296-L304) aside:
  - `className="w-72"` → `style={{ width: panelWidth }}`
  - 新增 drag handle div(absolute -left-0.5, cursor-col-resize)
- [ ] T14.4 手动测试:拖拽 handle 平滑调整宽度,不触发 Tauri 窗口 resize

## 阶段五(P3):文档 + 测试收尾

### P3.1: 文档更新

- [ ] T15.1 [AGENTS.md](../../../AGENTS.md) §13 SSE 事件契约:
  todo_update schema 新增 `source?` + `parent_task_id?` 字段说明
- [ ] T15.2 [docs/agents/02-sse-event-contract.md](../../../docs/agents/02-sse-event-contract.md)
  同步更新 todo_update 字段表
- [ ] T15.3 [docs/agents/05-frontend-naming.md](../../../docs/agents/05-frontend-naming.md):
  TaskTimeline 替换 CompactTaskList 的说明(若涉及)

### P3.2: 测试收尾

- [ ] T16.1 运行 `uv run pytest tests/python/unit -m "not integration"`:后端全通过
- [ ] T16.2 运行 `uv run ruff check backend/`:无 lint 错误
- [ ] T16.3 运行 `pnpm typecheck`:前端类型检查通过
- [ ] T16.4 运行 `pnpm test`:前端单测全通过
- [ ] T16.5 端到端冒烟测试:
  - work 对话:任务面板显示 todo 列表 + 进度条
  - coding_team 对话:任务面板显示父子任务嵌套
  - 切会话:聊天区 TodoProgress 自动切换到新会话任务
  - 拖拽右侧面板:宽度可调

## 预期修改文件

### 后端修改文件
- `backend/app/sse/events.py` — make_todo_update_event 新增 source + parent_task_id
- `backend/app/team/orchestrator.py` — _emit_todo_in_progress 注入 source + parent_task_id
- `backend/app/deepagent/streaming.py` — 主路径 todo_update 注入 source

### 前端修改文件
- `frontend/shared/api-types.ts` — todo_update 事件类型扩展
- `frontend/renderer/stores/tasks.ts` — Task 接口扩展 + migrate v4→v5
- `frontend/renderer/hooks/useChatStream.ts` — todo_update 子任务处理 + 删除 setTodos
- `frontend/renderer/components/chat/ChatView.tsx` — 删除 useState todos,从 store 派生
- `frontend/renderer/components/chat/TodoProgress.tsx` — 加进度条 + 过渡动画
- `frontend/renderer/components/workspace/WorkspacePanel.tsx` — CompactTaskList → TaskTimeline
- `frontend/renderer/components/workspace/TaskTimeline.tsx` — sessionId 过滤 + 父子分组渲染
- `frontend/renderer/App.tsx` — 右侧面板可调宽

### 前端删除文件
- `frontend/renderer/components/workspace/CompactTaskList.tsx`

### 测试文件
- `tests/python/unit/test_team_path.py` — 更新:验证 source + parent_task_id
- `frontend/renderer/components/workspace/TaskTimeline.test.tsx` — 新增:sessionId 过滤 + 父子分组
- `frontend/renderer/stores/tasks.test.ts` — 新增/更新:migrate v4→v5

### 文档文件
- `AGENTS.md` — §13 todo_update 字段扩展
- `docs/agents/02-sse-event-contract.md` — 同步

## 规模判定

- 涉及文件数: ~14(1 删除 + 3 后端修改 + 8 前端修改 + 2 测试新增/更新)
- 涉及模块数: 3(backend sse/team/deepagent + frontend renderer + shared)
- 规模: **L(大改)** — 跨前后端 + SSE 契约扩展 + 前端数据源统一,需全流程执行

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T0 | 前置验证(死代码/引用点确认) | grep | TaskTimeline 无 import,CompactTaskList 仅 WorkspacePanel 引用 | ⬜ |
| T1 | TaskTimeline sessionId 过滤修复 | TaskTimeline.tsx | 只渲染当前会话任务 | ⬜ |
| T2 | WorkspacePanel 替换 CompactTaskList + 删除 | WorkspacePanel.tsx, CompactTaskList.tsx | TaskTimeline 渲染,grep 无 CompactTaskList 残留 | ⬜ |
| T3 | make_todo_update_event 扩展 source + parent_task_id | sse/events.py | payload 含可选 source + parent_task_id | ⬜ |
| T4 | _emit_todo_in_progress 注入 source + parent_task_id | team/orchestrator.py | 子任务 todo_update 事件含两个字段 | ⬜ |
| T5 | 主路径 todo_update 注入 source | deepagent/streaming.py | work/coding 场景 source 正确 | ⬜ |
| T6 | api-types.ts todo_update 扩展 | frontend/shared/api-types.ts | source? + parent_task_id? 类型声明 | ⬜ |
| T7 | Task 模型扩展 + migrate v4→v5 | stores/tasks.ts | 新字段可选,migrate 补默认值,version=5 | ⬜ |
| T8 | useChatStream todo_update 子任务处理 | useChatStream.ts | 有 parent_task_id 创建子任务,无则主任务 | ⬜ |
| T9 | TaskTimeline 父子分组渲染 | TaskTimeline.tsx | 主任务嵌套子任务,compact 模式 | ⬜ |
| T10 | ChatView 删除本地 todos,从 store 派生 | ChatView.tsx | 无 useState<TodoItem[]>,selector 稳定 | ⬜ |
| T11 | useChatStream 删除 setTodos 双写 | useChatStream.ts | 无 setTodos 调用,UseChatStreamArgs 无 setTodos | ⬜ |
| T12 | TodoProgress 加进度条 | TodoProgress.tsx | 进度条宽度随 completedTodos 变化 | ⬜ |
| T13 | 状态切换过渡动画 | TodoProgress.tsx, TaskTimeline.tsx | badge 加 transition-colors | ⬜ |
| T14 | 右侧面板可调宽 | App.tsx | drag handle 240-480px 范围可调 | ⬜ |
| T15 | 文档更新(AGENTS.md §13 + docs) | AGENTS.md, docs/agents/ | SSE 契约同步 | ⬜ |
| T16 | 测试收尾 + 全量验证 | tests/, pnpm test, pytest, ruff | 全部通过,无 lint 错误 | ⬜ |
