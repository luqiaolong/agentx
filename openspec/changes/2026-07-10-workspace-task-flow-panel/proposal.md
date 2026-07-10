# Proposal: 右侧任务面板任务流 + Todo 列表展示

## Why

[2026-07-09-write-todos-unification](../archive/2026-07-09-write-todos-unification/proposal.md) 完成了
后端 Todo 系统到 deepagents 原生 `write_todos` 的统一迁移，`todo_update` SSE 事件已产出原生
`{content, status}` 三态 schema。但**前端任务面板侧的展示能力严重滞后**，用户在日常使用中无法有效感知
agent 的规划与执行进度：

### 现状三大问题

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| 1 | **TaskTimeline.tsx 是死代码**，实际渲染的 CompactTaskList 只有单行摘要（无 todo 明细、无进度条） | [WorkspacePanel.tsx:144](../../../frontend/renderer/components/workspace/WorkspacePanel.tsx#L144) 用 `<CompactTaskList />`，TaskTimeline 无任何 import | 用户必须切到"摘要"Tab 点开弹窗才能看到 todo 项，发现性极差 |
| 2 | **无任务流概念**，Task 模型无 parentId/dependencies/order，平铺列表 | [stores/tasks.ts:5-14](../../../frontend/renderer/stores/tasks.ts#L5-L14) Task 接口无关系字段 | Team 路径的并行子任务无法以"父任务 + 子任务分组"形式展示，后端 `task_id` 里编码的 `-team-<role>-<idx>` 层级信息被丢弃 |
| 3 | **聊天区 TodoProgress 与工作区面板数据源割裂** | [ChatView.tsx:66](../../../frontend/renderer/components/chat/ChatView.tsx#L66) `useState<TodoItem[]>` 本地 state vs [tasks.ts](../../../frontend/renderer/stores/tasks.ts) zustand store 双写 | 切会话后聊天区 todos 清空（`setTodos([])` at [ChatView.tsx:419](../../../frontend/renderer/components/chat/ChatView.tsx#L419)），工作区保留但不展示 todo 明细；双写存在数据漂移隐患 |

### 后端已就绪但前端未消费的能力

- `todo_update` 事件 payload 已是原生 `{content, status}` schema（[events.py:98-127](../../../backend/app/sse/events.py#L98-L127)）
- Team 路径 `_plan_node` 拆任务后 `state.todos` 经 `_merge_todos` reducer 归并，产出全局 todo 视图（[orchestrator.py:567-577](../../../backend/app/team/orchestrator.py#L567-L577)）
- `task_id` 字段已注入（Team 路径 = parent_thread_id），但**前端无法区分主任务与子任务**

### 主路径提示词残留（已在本次修复前处理）

[config/prompts/agent.py:41-45](../../../backend/app/config/prompts/agent.py#L41-L45) 和 76-80 曾残留旧 JSON plan
指令（`{"plan": [...]}` / `{"plan_update": {...}}`），对应的 SSE 事件已删除但 prompt 仍在引导 LLM 输出 JSON，
导致回复污染。**本次提交前已修复**：替换为引导使用 `write_todos` 工具。

Team 路径 `_plan_node` 返回的 `state.todos` 曾带 `[agent:xxx]` 前缀泄漏到前端。**本次提交前已修复**：
新增 `_strip_agent_prefix_from_todos` 在返回前剥离前缀。

## What Changes

### 阶段 1（P0）：激活 TaskTimeline，替换 CompactTaskList

**目标**：让任务面板直接展示 todo 列表 + 进度条，无需点进弹窗。

- [WorkspacePanel.tsx:144](../../../frontend/renderer/components/workspace/WorkspacePanel.tsx#L144)：
  `<CompactTaskList />` → `<TaskTimeline />`
- [TaskTimeline.tsx:189](../../../frontend/renderer/components/workspace/TaskTimeline.tsx#L189)：
  **关键修复**——加 `useChatStore(s => s.currentId)` 按 sessionId 过滤（当前死代码未过滤，会泄露其他会话任务）
- [CompactTaskList.tsx](../../../frontend/renderer/components/workspace/CompactTaskList.tsx)：删除（功能已被 TaskTimeline 完全覆盖）

### 阶段 2（P1）：Task 模型扩展 + SSE 事件扩展 + 任务流分组渲染

**目标**：Team 路径的并行子任务以"父任务卡片 + 子任务嵌套"形式展示。

#### 2.1 后端 SSE todo_update 事件扩展

- [events.py:98-127](../../../backend/app/sse/events.py#L98-L127) `make_todo_update_event`：
  payload 新增可选 `source` 和 `parent_task_id` 字段
- [orchestrator.py:234-244](../../../backend/app/team/orchestrator.py#L234-L244) `_emit_todo_in_progress`：
  发送子任务级 todo_update 时注入 `source=agent_role` + `parent_task_id=parent_thread_id`
- [streaming.py:184-188](../../../backend/app/deepagent/streaming.py#L184-L188) 主路径 todo_update：
  注入 `source`（work/coding）

#### 2.2 前端 Task 模型扩展（persist v4 → v5）

- [stores/tasks.ts:5-14](../../../frontend/renderer/stores/tasks.ts#L5-L14) Task 接口新增：
  - `parentTaskId?: string` — 父任务 ID
  - `taskSource?: "work" | "coding" | "team"` — 任务来源场景
  - `agentRole?: string` — team 子任务的 agent 角色（code/deep/rag/web/frontend_dev...）
- [stores/tasks.ts:114](../../../frontend/renderer/stores/tasks.ts#L114) version 4 → 5
- [stores/tasks.ts:43-86](../../../frontend/renderer/stores/tasks.ts#L43-L86) migrate v4→v5：补默认值（无破坏性）

#### 2.3 前端 SSE 类型 + useChatStream 适配

- [api-types.ts:55-60](../../../frontend/shared/api-types.ts#L55-L60) todo_update 事件类型扩展 `source?` + `parent_task_id?`
- [useChatStream.ts:273-310](../../../frontend/renderer/hooks/useChatStream.ts#L273-L310) todo_update 处理逻辑：
  有 parent_task_id 时创建/更新子任务（parentTaskId 关联），无时创建主任务

#### 2.4 TaskTimeline 分组渲染

- [TaskTimeline.tsx](../../../frontend/renderer/components/workspace/TaskTimeline.tsx)：
  - 按 parentTaskId 分组：先渲染无 parent 的主任务，主任务卡片内嵌套渲染其子任务
  - 子任务卡片用更小缩进 + agentRole 标签（复用 `formatTaskLabel`）

### 阶段 3（P2）：数据源统一

**目标**：消除聊天区本地 state 与工作区 store 的双写漂移。

- [ChatView.tsx:66](../../../frontend/renderer/components/chat/ChatView.tsx#L66)：删除 `const [todos, setTodos] = useState`
- [ChatView.tsx:419](../../../frontend/renderer/components/chat/ChatView.tsx#L419)：删除 `setTodos([])` 重置
- [ChatView.tsx:539-541](../../../frontend/renderer/components/chat/ChatView.tsx#L539-L541)：TodoProgress 数据源改为从 useTasksStore 派生（当前 session + 最新 running 任务的 todos）
- [useChatStream.ts:279-284](../../../frontend/renderer/hooks/useChatStream.ts#L279-L284)：删除 `setTodos` 调用，只保留 store 更新
- [useChatStream.ts:45-50](../../../frontend/renderer/hooks/useChatStream.ts#L45-L50)：UseChatStreamArgs 删除 `setTodos` 参数

### 阶段 4（P2）：UX 增强

- [TodoProgress.tsx](../../../frontend/renderer/components/chat/TodoProgress.tsx)：加进度条（复用 TaskTimeline 的 `progress%` 宽度动画设计）
- [TodoProgress.tsx](../../../frontend/renderer/components/chat/TodoProgress.tsx) + [TaskTimeline.tsx](../../../frontend/renderer/components/workspace/TaskTimeline.tsx)：状态切换加 `transition-colors` 过渡动画
- [App.tsx:297](../../../frontend/renderer/App.tsx#L297)：右侧面板 `w-72` 改为可拖拽 resize（引入 `react-resizable-panels` 或手写 drag handle）

## Capabilities

### Modified Capabilities

- `task-flow-panel`: 右侧任务面板从"平铺单行摘要"升级为"任务流分组 + todo 列表 + 进度条"展示，
  支持父子任务嵌套、三态可视化、跨会话持久化

### Removed Artifacts

- `frontend/renderer/components/workspace/CompactTaskList.tsx`（功能被 TaskTimeline 完全覆盖）
- ChatView 的 `useState<TodoItem[]>` 本地 todos state（统一到 useTasksStore）

## Impact

- **后端**：修改 ~3 个文件（sse/events.py、team/orchestrator.py、deepagent/streaming.py）
- **前端**：修改 ~6 个文件（WorkspacePanel.tsx、TaskTimeline.tsx、tasks.ts、api-types.ts、useChatStream.ts、ChatView.tsx、TodoProgress.tsx、App.tsx），删除 1 个文件（CompactTaskList.tsx）
- **SSE 事件契约**：`todo_update` 事件新增 2 个可选字段（`source`、`parent_task_id`），向后兼容
- **前端持久化**：`agentx-tasks` localStorage version 4 → 5，非破坏性 migrate
- **测试**：更新前端组件测试，新增 task flow 分组测试
- **文档**：更新 AGENTS.md §13 + docs/agents/02-sse-event-contract.md（todo_update 字段扩展）
- **依赖**：可能新增 `react-resizable-panels`（阶段 4 面板可调宽，若选手写 drag handle 则无新依赖）

## Future Extensibility

- 任务流支持更复杂的依赖关系（DAG，非仅父子）
- 任务标题由 LLM 生成高质量摘要（当前截断用户 query 前 40 字符）
- 历史会话任务回放（时间旅行查看 todo 状态变化）
