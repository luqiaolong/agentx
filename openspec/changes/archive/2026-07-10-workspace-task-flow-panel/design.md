# Design: 右侧任务面板任务流 + Todo 列表展示

## Context

### 历史背景

[2026-07-09-write-todos-unification](../archive/2026-07-09-write-todos-unification/design.md) 完成了后端
Todo 系统统一：`todo_update` SSE 事件产出原生 `{content, status}` 三态 schema，Team 路径用 `write_todos`
拆任务，`state.todos` 经 `_merge_todos` reducer 归并。前端 `TodoItem` / `Task.todos` schema 也升级到三态，
persist migrate v3→v4。

但前端任务面板侧**只完成了 schema 升级，未完成展示能力升级**：

1. **TaskTimeline.tsx 被写好但从未被 import**（死代码）。WorkspacePanel 实际用的是 CompactTaskList，
   只显示单行 title + 状态标签，无 todo 明细、无进度条。用户想看 todo 必须切到"摘要"Tab 点开弹窗。
2. **Task 模型是纯平铺**，无 parentTaskId / dependencies / order。Team 路径的并行子任务在面板里
   看不出归属关系。后端 `task_id`（如 `{thread_id}-team-deep-0`）编码了 role + index，但前端只当字符串标签用。
3. **聊天区与工作区双写**：useChatStream 的 `todo_update` 分支同时写 `setTodos`（ChatView useState）
   和 `addTask/updateTask`（zustand store）。ChatView 每轮 `setTodos([])` 清空本地 state，
   导致切会话后聊天区 TodoProgress 丢失，而工作区 store 保留但不展示 todo 明细。

### 当前数据流（双写）

```
SSE todo_update
  ├─→ useChatStream setTodos(ChatView useState)  ← 每轮清空，切会话丢失
  └─→ useChatStream addTask/updateTask(zustand)  ← 持久化，但 CompactTaskList 不展示 todo
```

### 目标数据流（单源）

```
SSE todo_update
  └─→ useChatStream addTask/updateTask(zustand)
        ├─→ TaskTimeline（工作区面板，展示 todo + 进度条 + 父子分组）
        └─→ ChatView TodoProgress（从 store 派生，切会话自动读取）
```

## Goals / Non-Goals

**Goals:**
- 激活 TaskTimeline 替换 CompactTaskList，任务面板直接展示 todo 列表 + 进度条
- Task 模型引入 parentTaskId / taskSource / agentRole，支持父子任务嵌套渲染
- 后端 todo_update 事件扩展 source + parent_task_id，前端据此创建子任务
- 聊天区 TodoProgress 改为从 useTasksStore 派生，消除双写
- UX 增强：进度条、过渡动画、面板可调宽

**Non-Goals:**
- 不引入 DAG 依赖关系（仅父子两层，不做复杂拓扑）
- 不改后端 LangGraph StateGraph 拓扑（Team 路径的 plan→dispatch→aggregate 不变）
- 不改 deepagents TodoListMiddleware（三态 schema 不变）
- 不做历史会话任务回放（时间旅行）

## Decisions

### Decision 1: TaskTimeline 复活 vs CompactTaskList 改造

**选择：复活 TaskTimeline，删除 CompactTaskList**

理由：
- TaskTimeline 已具备 80% 能力（进度条、展开/收起、todo 三态、删除），只需修复 sessionId 过滤
- CompactTaskList 信息密度过低（无 todo 明细），改造它等于重写
- 复活死代码比改造活代码成本低，且 TaskTimeline 的 UX 设计更完整

### Decision 2: 任务流模型 — 父子两层 vs DAG

**选择：父子两层（parentTaskId 单字段）**

理由：
- Team 路径的 Orchestrator 拆任务是"一次性 fan-out"，子任务之间无依赖（并行执行），只有"属于同一个父任务"的关系
- DAG 需要依赖图 + 拓扑排序 + 循环检测，过度设计
- parentTaskId 单字段足以表达"主任务 + 子任务分组"，渲染时按 parentTaskId 分组即可
- 未来若需 DAG，可在 parentTaskId 基础上扩展 dependsOn 字段

### Decision 3: SSE 事件扩展方式 — 新增字段 vs 新事件类型

**选择：在 todo_update 事件新增可选字段（source + parent_task_id）**

理由：
- 新增事件类型会破坏 SSE 契约的向后兼容（前端旧版本收到未知事件类型会报错）
- 新增可选字段是向后兼容的（前端旧版本忽略未知字段）
- source 和 parent_task_id 是 todo_update 的元数据补充，语义上属于同一事件
- 符合 AGENTS.md §13「修改 SSE 事件必须同步三处」的约束（只需加字段，不需加事件类型）

### Decision 4: 子任务 Task 创建时机

**选择：前端收到带 parent_task_id 的 todo_update 时创建子任务**

Team 路径的事件流：
1. `_plan_node` 产出 todo_update（task_id = parent_thread_id，无 parent_task_id）→ 前端创建/更新主任务
2. 各子任务节点 `_emit_todo_in_progress` 产出 todo_update（task_id = parent_thread_id，parent_task_id = parent_thread_id，source = agent_role）→ 前端创建子任务（parentTaskId = parent_task_id）

**子任务 ID 生成**：`{parent_task_id}-child-{agent_role}-{index}`，确保幂等（同一子任务多次 todo_update 不会重复创建）

### Decision 5: 数据源统一 — ChatView 如何从 store 派生 todos

**选择：ChatView 用 selector 从 useTasksStore 派生当前 running 任务的 todos**

```typescript
// ChatView.tsx（删除 useState<TodoItem[]>）
const currentId = useChatStore((s) => s.currentId);
const todos = useTasksStore((s) => {
  const sessionTasks = s.tasks.filter((t) => t.sessionId === currentId);
  const running = sessionTasks.find((t) => t.status === "running");
  return running?.todos ?? [];
});
```

理由：
- 切会话时 selector 自动重新计算，todos 自动切换到新会话的最新任务
- 无需 `setTodos([])` 重置（store 驱动，非本地 state）
- 消除双写：useChatStream 只写 store，ChatView 只读 store

**注意**：selector 返回的数组引用稳定性需用 `useMemo` 或 zustand 的 `shallow` 比较保证，避免无限重渲染。

### Decision 6: 面板可调宽 — react-resizable-panels vs 手写 drag handle

**选择：手写 drag handle（不引入新依赖）**

理由：
- 项目前端无现有 resize 库，引入 `react-resizable-panels` 增加包体积
- 手写 drag handle 只需 mousedown + mousemove + mouseup 三个事件，~30 行代码
- 右侧面板宽度范围 240px-480px，持久化到 zustand store
- 符合 AGENTS.md §1.2 P1「组合优于重写」——用原生事件而非引入库

### Decision 7: persist migrate v4 → v5 策略

**选择：非破坏性 migrate，新字段可选**

```typescript
if (version < 5) {
  tasks = tasks.map((t) => ({
    ...t,
    parentTaskId: typeof t.parentTaskId === "string" ? t.parentTaskId : undefined,
    taskSource: typeof t.taskSource === "string" ? t.taskSource : "work",
    agentRole: typeof t.agentRole === "string" ? t.agentRole : undefined,
  }));
}
```

理由：
- 三个新字段都是可选的，老任务不填即为 undefined / "work"
- 无数据转换，只补默认值
- 不需要清理老任务（parentTaskId=undefined 的任务按主任务渲染）

## Alternatives Considered

### Alternative A: 只做阶段 1（激活 TaskTimeline），不做任务流

否决理由：用户明确要求"有效展示任务的 todo 列表"和"任务流"。阶段 1 只解决可见性，不解决任务流分组。
Team 路径的并行子任务在 TaskTimeline 里仍是平铺卡片，用户看不出归属关系。

### Alternative B: 前端从 task_id 正则解析 role/index，不改后端 SSE

否决理由：`task_id` 格式（`-team-<role>-<idx>`）是隐式约定，不是显式契约。后端改 task_id 格式前端就会
解析失败。显式扩展 SSE 事件字段（source + parent_task_id）是契约级保障，符合 AGENTS.md §13 约束。

### Alternative C: 保留 CompactTaskList 作为"精简模式"备选

否决理由：两套组件做同一件事违反单一职责。TaskTimeline 的展开/收起已足够提供精简视图（收起后只显示
title + 进度条）。保留 CompactTaskList 会增加维护成本和 UI 不一致风险。

## Risk Analysis

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| TaskTimeline 复活后 sessionId 过滤未修复，泄露其他会话任务 | 中 | 高 | 阶段 1 必须同步修复过滤逻辑，写测试覆盖 |
| ChatView selector 引用不稳定导致无限重渲染 | 中 | 中 | 用 zustand `shallow` 比较或 `useMemo` 稳定引用 |
| persist v5 migrate 遗漏边界 case | 低 | 低 | 新字段可选，migrate 只补默认值，无破坏性 |
| SSE 事件扩展未同步更新三处契约 | 低 | 高 | tasks.md 明确列出三处同步点，review 时检查 |
| 面板可调宽与 Tauri 窗口 resize 冲突 | 低 | 低 | drag handle 只改 aside 宽度，不触发 Tauri 窗口 resize |

## Testing Strategy

- **后端**：`test_team_path.py` 验证 `_emit_todo_in_progress` 产出带 source + parent_task_id 的 todo_update
- **前端单元**：TaskTimeline 分组渲染测试（主任务 + 子任务嵌套）、tasks store migrate v4→v5 测试
- **前端集成**：useChatStream todo_update 处理测试（有/无 parent_task_id 两种路径）
- **E2E 冒烟**：Team 对话验证任务面板显示父子任务分组 + todo 三态 + 进度条
