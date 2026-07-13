# Design: AgentTeam v2 全量重构

## Context

### 历史背景

AgentX Team 路径自 2026-07-06 引入，经过 deepagents 迁移、Phase 1 稳定性硬化，
**feature/agent-team-dag-orchestration 分支（含 Phase 1+2+DAG 完整实现）已合并到 master**。
合并后 [backend/app/team/](file:///d:/java/agentprojects/agentx/backend/app/team/)
仍是 5 文件结构（`orchestrator.py` 1250 行 / `planner.py` 469 行 / `scheduler.py` 500 行 /
`aggregator.py` 217 行 / `blackboard.py` 245 行），未拆分为 v2 设计的 11 文件。

代码 review 发现 v2 OpenSpec 的 13 项改动中：3 项已实现但有命名/细节偏差、5 项部分实现、
5 项完全未实现。本 change 在合并后代码基础上做增量改进，不再 `supersedes` 两个 prior change
（详见 [proposal.md](proposal.md#dependencies)）。

### 合并后的当前数据流（含 Phase 1+2+DAG 实现）

```
run_team_path (orchestrator.py:1139 team_semaphore)
  ├─ _should_downgrade_to_single → 降级
  ├─ _plan_node (orchestrator.py)
  │     ├─ planner._parse_todos_from_text (正则 _AGENT_PREFIX_RE: planner.py:115-119)
  │     └─ planner._validate_dag (Kahn 分层: planner.py:344-417)  ← DAG 已实现
  │     └─ tasks: list[TeamPlanTask]  ← 无 deps 字段
  ├─ _dispatch_batch_node (orchestrator.py:336-411)  ← 单 wave 派发
  │     └─ _dispatch_batch_router → Send fan-out 当前 wave
  │     └─ _inject_dependency_context (orchestrator.py:140-180)  ← 单函数注入
  │     └─ _NODE_DISPATCH (orchestrator.py:582-588)  ← 派发表已实现
  │           ├─ deep/code/builtin/team_role/custom 各 runner
  │           └─ default → fallback 到 code (D7 部分实现，缺 warning)
  ├─ _check_next_level_node (orchestrator.py:874-883)
  │     ├─ _route_after_level → 有下层 → 回到 _dispatch_batch_node
  │     └─ 无下层 → _replan_check_node (orchestrator.py:898-1026)  ← 触发位置错误
  └─ _aggregate_node → 直接 END  ← 质量门失败路径缺失！
```

**关键差距**：
- `_aggregate_node` 后直接 `END`，质量门失败 → replan 路径完全缺失（D10/D16 修复）
- `_replan_check_node` 在「无下一层 wave」时触发，而非 spec 要求的「质量门失败时」触发
- abort 用 [scheduler.py:253-287](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py#L253-L287)
  `asyncio.wait(timeout=5)` 轮询式，而非 `_running_tasks` registry + 主动 cancel
- `team_semaphore` 用 [settings.py:187](file:///d:/java/agentprojects/agentx/backend/app/config/settings.py#L187)
  `agent_team_max_parallel` (默认 3, 范围 1-5)，应为 `team_max_concurrency` (默认 5, 范围 1-20)
- [blackboard.py:148-188](file:///d:/java/agentprojects/agentx/backend/app/team/blackboard.py#L148-L188)
  `TeamState` 无 `warnings` 字段
- [blackboard.py:179](file:///d:/java/agentprojects/agentx/backend/app/team/blackboard.py#L179)
  `findings: Annotated[dict[str, str], _merge_dict]` 仍是裸字符串 dict
- [orchestrator.py:433](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L433)
  `key = f"{result.agent}-{task_index}"` 仍是旧格式
- [sse/events.py:98-99](file:///d:/java/agentprojects/agentx/backend/app/sse/events.py#L98-L99)
  token 事件 payload 仍是纯字符串

### 目标数据流（v2 重构后）

```
run_team_path (runner.py)
  ├─ _should_downgrade_to_single → 降级
  ├─ plan_node (nodes.py)
  │     ├─ planner.plan_with_llm → llm.with_structured_output(TeamPlan)
  │     └─ tasks: list[TeamTask(id, agent, description, depends_on, ...)]
  ├─ dispatcher.resolve_waves(tasks) → waves: list[list[TeamTask]]  ← Kahn 分层
  ├─ dispatch_node (nodes.py) → Send("execute", {task, upstream_findings})  只 fan-out 当前 wave
  │     └─ execute_node (nodes.py)  ← 统一节点，替代 6 个分类节点
  │           ├─ scheduler.acquire_semaphore  ← 并发限流 (team_max_concurrency)
  │           ├─ scheduler.run_with_retry    ← 失败重试 (team_max_retries)
  │           ├─ scheduler.register_for_abort  ← asyncio.Task cancel (_running_tasks registry)
  │           ├─ _NODE_DISPATCH[task.agent].runner(task, upstream_findings)
  │           │     ├─ code/deep/rag/web/builtin...
  │           │     ├─ team_role (显式失败 + warning SSE, D11)
  │           │     └─ default → fallback 到 code + warning SSE (D7)
  │           └─ 写入 findings[key]  ← key = "{agent}:{task_id}:{wave_index}" (D13)
  ├─ barrier_node (nodes.py)
  │     ├─ 还有未执行 wave → dispatch_node（下一 wave）
  │     └─ 全部执行完 → aggregate_node
  ├─ aggregate_node (nodes.py)
  │     ├─ aggregator.run_aggregator → 质量门
  │     ├─ 发射 state.warnings 累积的 warning SSE (D14)
  │     └─ _route_after_aggregate
  │           ├─ 质量门通过 → END
  │           └─ 质量门失败 → replan_node  ← D16 修复的关键路由
  └─ replan_node (nodes.py)  ← 质量门失败时
        ├─ replan_count < max_replan_attempts → 重新喂给 planner → plan_node
        └─ replan_count >= max → team_done (失败)
```

## Design Decisions

每个设计决策按「现状 / 目标 / 差距」三段式描述，标注合并后代码的实际实现。

### D1. 模块拆分后的依赖图

**现状**：
[backend/app/team/](file:///d:/java/agentprojects/agentx/backend/app/team/) 仍是 5 文件结构：
- [orchestrator.py](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py)（1250 行，承载 plan / dispatch / 6 类节点派发 / DAG 多波次 / replan / 入口流 / SSE 发射）
- [planner.py](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py)（469 行，`_parse_todos_from_text` + `_validate_dag`）
- [scheduler.py](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py)（500 行，`_run_subtask_stream` + `_run_team_role_subtask`）
- [aggregator.py](file:///d:/java/agentprojects/agentx/backend/app/team/aggregator.py)（217 行）
- [blackboard.py](file:///d:/java/agentprojects/agentx/backend/app/team/blackboard.py)（245 行，`TeamState` + reducers）

未创建：`state.py` / `dispatcher.py` / `nodes.py` / `graph_builder.py` / `runner.py` / `classifier.py`。

**目标**：拆为 11 个模块，每个文件单一职责。依赖方向（自底向上）：

```
                    ┌─────────────┐
                    │  __init__.py│  (公共 API 导出)
                    └──────┬──────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
   ┌─────────┐       ┌─────────┐        ┌──────────┐
   │ state.py│       │blackboard│        │classifier│
   │(TypedDict│       │  .py    │        │  .py     │
   │ +Pydantic│       │(Reducers)│       │(Dangerous│
   │  schema) │       └────┬────┘        │ Task LLM)│
   └────┬────┘            │             └─────┬────┘
        │                  │                   │
        ▼                  ▼                   │
   ┌─────────┐       ┌─────────┐              │
   │planner.py│      │(被nodes │              │
   │(LLM +   │       │ 引用)   │              │
   │structured│      └─────────┘              │
   │ output) │                                │
   └────┬────┘                                │
        │                                     │
        ▼                                     │
   ┌───────────┐                              │
   │dispatcher.│                              │
   │  py       │                              │
   │(Kahn wave)│                              │
   └────┬──────┘                              │
        │                                     │
        ▼                                     │
   ┌─────────┐    ┌───────────┐    ┌─────────┐│
   │ nodes.py│───→│scheduler. │───→│runner.py││
   │(plan/   │    │  py      │    │(run_team││
   │ execute/│    │(Semaphore│    │  _path) ││
   │ barrier/│    │ +retry+  │    └─────────┘│
   │ aggregate│   │abort)    │        ▲      │
   │ /replan)│    └────┬─────┘        │      │
   └────┬────┘         │              │      │
        │              │              │      │
        ▼              ▼              │      │
   ┌──────────────┐  ┌──────────┐     │      │
   │graph_builder.│  │aggregator│     │      │
   │  py          │  │  .py     │─────┘      │
   │(StateGraph)  │  │(质量门+  │            │
   └──────┬───────┘  │ structured│           │
          │          │ findings) │           │
          │          └──────────┘            │
          └──────────────────────────────────┘
                   (classifier 被 nodes/planner 引用)
```

- **底层（无依赖）**：`state.py`、`blackboard.py`、`classifier.py`
- **中间层（依赖底层）**：`planner.py`、`dispatcher.py`、`aggregator.py`、`scheduler.py`
- **上层（依赖中间层）**：`nodes.py`、`graph_builder.py`
- **入口层**：`runner.py`、`__init__.py`

**差距**：
1. 新建 `state.py`：从 [orchestrator.py](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py)
   / [blackboard.py:148-188](file:///d:/java/agentprojects/agentx/backend/app/team/blackboard.py#L148-L188)
   提取 `TeamState` / `TeamPlanTask` / `SubtaskState`
2. 新建 `dispatcher.py`：从 [orchestrator.py:336-411](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L336-L411)
   提取 `_dispatch_batch_node` / `_dispatch_batch_router` +
   [orchestrator.py:140-180](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L140-L180)
   `_inject_dependency_context`（拆为 `_inject_upstream_findings` + `_compose_input_with_upstream`）+
   [orchestrator.py:874-883](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L874-L883)
   `_check_next_level_node` / `_route_after_level` +
   [planner.py:344-417](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L344-L417)
   `_validate_dag` 改名 `resolve_waves`
3. 新建 `nodes.py`：从 [orchestrator.py:582-588](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L582-L588)
   提取 `_NODE_DISPATCH` 派发表 + 各节点函数（plan_node / execute_node / barrier_node / aggregate_node / replan_node）
4. 新建 `graph_builder.py`：提取 `_build_team_graph` + 新增 `_route_after_aggregate`（D16）
5. 新建 `runner.py`：提取 `run_team_path` + astream 消费
6. 新建 `classifier.py`：基于 [utils/text.py:274-294](file:///d:/java/agentprojects/agentx/backend/app/utils/text.py#L274-L294)
   `compile_keyword_patterns` 封装 `DangerousTaskClassifier` 类

### D2. `TeamPlan` / `TeamTask` Pydantic schema 完整定义

**现状**：
[planner.py:115-119](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L115-L119)
仍用 `_AGENT_PREFIX_RE = re.compile(...)` 正则解析 `[agent:xxx]` 行，无 `with_structured_output`。
[planner.py:82-102](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L82-L102)
Orchestrator prompt 无法表达结构化字段。

**目标**：用 `with_structured_output(TeamPlan)` 替代正则，schema 定义如下：

```python
# state.py
class TeamTask(BaseModel):
    """Orchestrator LLM 输出的单个子任务（结构化）。"""

    id: str = Field(description="任务唯一标识，如 t1/t2/t3，用于 depends_on 引用")
    agent: str = Field(description="执行 agent 名: code/deep/rag/web/frontend_dev/builtin/team_role")
    description: str = Field(description="任务描述（自然语言）")
    depends_on: list[str] = Field(default_factory=list, description="依赖的 task id 列表")
    expected_output: str = Field(default="", description="预期产出描述，用于质量门判定")
    is_dangerous_hint: bool = Field(default=False, description="LLM 标注的危险任务提示")


class TeamPlan(BaseModel):
    """Orchestrator LLM 的结构化输出。"""

    tasks: list[TeamTask]
    summary: str = Field(default="", description="整体规划摘要")
    needs_iterative: bool = Field(default=False, description="是否建议迭代式拆解（触发 replan 检查）")


class Finding(BaseModel):
    """单个子任务的产出结果。"""

    agent: str
    task_id: str
    wave_index: int
    content: str
    success: bool = True
    error: Optional[str] = None
    retries: int = 0


class ClassificationResult(BaseModel):
    """DangerousTaskClassifier 的 LLM 结构化输出。"""

    is_dangerous: bool
    reason: str
    suggested_agent: str
```

Fallback：当 LLM provider 不支持 `with_structured_output` 时，回退到正则解析
（保留旧 `_AGENT_PREFIX_RE`）：

```python
# planner.py
_AGENT_PREFIX_RE = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])?\s*\[agent:\s*([a-zA-Z0-9_\-]+)\]"
    r"(?:\[after:\s*([\w,\s]+)\])?"
    r"\s*(.*)"
)


def _parse_plan_from_text_fallback(text: str) -> TeamPlan:
    """LLM 不支持结构化输出时的 fallback。"""
    tasks: list[TeamTask] = []
    for idx, line in enumerate(text.splitlines()):
        m = _AGENT_PREFIX_RE.match(line)
        if not m:
            continue
        agent, after, desc = m.group(1), m.group(2), m.group(3)
        depends_on = [s.strip().lstrip("#") for s in (after or "").replace(";", ",").split(",") if s.strip()]
        tasks.append(TeamTask(id=f"t{idx+1}", agent=agent.lower(), description=desc.strip(), depends_on=depends_on))
    return TeamPlan(tasks=tasks)
```

**差距**：
1. 新建 `Planner` 类，封装 Orchestrator LLM 调用（保留现有 `_validate_dag` / `_parse_todos_from_text`）
2. 实现 `plan_with_llm(message, context) -> TeamPlan`：调用 `chat_model.with_structured_output(TeamPlan).ainvoke()`
3. 实现 `replan(original_message, previous_plan, findings, errors, hint) -> TeamPlan`
4. 启动时检测 LLM 是否支持 `with_structured_output`，不支持则走 fallback

### D3. DAG 多波次派发的 StateGraph 拓扑

**现状**：
[planner.py:344-417](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L344-L417)
已实现 `_validate_dag`（Kahn 算法分层 + 循环检测），但命名为 `_validate_dag` 而非 spec 要求的
`resolve_waves`，且位于 `planner.py` 而非 `dispatcher.py`。
[orchestrator.py:336-411](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L336-L411)
已实现 `_dispatch_batch_node` + `_dispatch_batch_router`（单 wave 派发）。
[orchestrator.py:874-883](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L874-L883)
已实现 `_check_next_level_node` + `_route_after_level`。

**目标**：DAG 用 Kahn 算法分层，StateGraph 拓扑包含 `aggregate → _route_after_aggregate → replan`
质量门失败路由（详见 D16）：

```
                       ┌─────────┐
                       │  START  │
                       └────┬────┘
                            │
                            ▼
                       ┌─────────┐
                       │  plan   │  (nodes.plan_node)
                       └────┬────┘
                            │
                            ▼
                       ┌─────────┐
                ┌─────│dispatch │  (nodes.dispatch_node)
                │     └────┬────┘
                │          │ Send fan-out (current wave)
                │          ▼
                │     ┌─────────┐
                │     │ execute │  (nodes.execute_node, 并行 N 个)
                │     └────┬────┘
                │          │
                │          ▼
                │     ┌─────────┐
                │     │ barrier │  (nodes.barrier_node)
                │     └────┬────┘
                │          │
                │          ▼
                │     _route_after_barrier
                │     /              \
                │  有 wave          无 wave
                │  剩余             剩余
                │  /                  \
                └─┘                   ▼
                                ┌─────────┐
                                │aggregate│  (nodes.aggregate_node)
                                └────┬────┘
                                     │
                              _route_after_aggregate  ← D16 新增
                              /              \
                         质量门失败         质量门通过
                          /                    \
                         ▼                     ▼
                   ┌─────────┐             ┌─────────┐
                   │ replan  │             │  END    │
                   │  (node) │             └─────────┘
                   └────┬────┘
                        │ (replan_count < max)
                        │ 追加新任务到 plan
                        ▼
                   回到 plan_node
```

`_build_team_graph` 实现：

```python
# graph_builder.py
def _build_team_graph() -> Any:
    graph: Any = StateGraph(TeamState)

    graph.add_node("plan", plan_node)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("execute", execute_node)
    graph.add_node("barrier", barrier_node)
    graph.add_node("aggregate", aggregate_node)
    graph.add_node("replan", replan_node)

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "dispatch")
    graph.add_conditional_edges("dispatch", _dispatch_router)
    graph.add_edge("execute", "barrier")
    graph.add_conditional_edges("barrier", _route_after_barrier)
    graph.add_conditional_edges("aggregate", _route_after_aggregate)  # D16 新增
    graph.add_conditional_edges("replan", _route_after_replan)
    return graph.compile()
```

`resolve_waves` Kahn 算法伪代码（迁移自 [planner.py:344-417](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L344-L417)
`_validate_dag`）：

```python
# dispatcher.py
def resolve_waves(tasks: list[TeamTask]) -> list[list[TeamTask]]:
    """Kahn 算法按依赖分层。"""
    id_to_idx = {t.id: i for i, t in enumerate(tasks)}

    n = len(tasks)
    in_degree = [0] * n
    dependents: list[list[int]] = [[] for _ in range(n)]
    dropped: list[str] = []

    for i, task in enumerate(tasks):
        for dep_id in task.depends_on:
            if dep_id not in id_to_idx:
                logger.warning("DAG dep_id not found, dropping", dep_id=dep_id, task_id=task.id)
                dropped.append(dep_id)
                continue
            if dep_id == task.id:
                logger.warning("DAG self-loop, dropping", task_id=task.id)
                dropped.append(dep_id)
                continue
            dep_idx = id_to_idx[dep_id]
            dependents[dep_idx].append(i)
            in_degree[i] += 1

    waves: list[list[TeamTask]] = []
    completed = set()
    remaining = set(range(n))

    while remaining:
        current = [i for i in remaining if in_degree[i] == 0]
        if not current:
            min_indeg = min(in_degree[i] for i in remaining)
            current = [i for i in remaining if in_degree[i] == min_indeg]
            for i in current:
                logger.error("DAG cycle detected, breaking by force", task_id=tasks[i].id)
        waves.append([tasks[i] for i in current])
        for i in current:
            completed.add(i)
            remaining.discard(i)
            for j in dependents[i]:
                in_degree[j] -= 1

    return waves
```

边界情况：

| 情况 | waves 结果 |
|---|---|
| 所有任务无 `depends_on` | `[[t1, t2, t3, ...]]` 单层，等价于现有并行 fan-out |
| 单任务 | `[[t1]]` |
| 线性链 t1→t2→t3 | `[[t1], [t2], [t3]]` 三层串行 |
| 菱形 t1→t2, t1→t3, t2→t4, t3→t4 | `[[t1], [t2, t3], [t4]]` |
| 自环 t1.depends_on=[t1] | 自环边丢弃，`[[t1]]` |
| 越界 t1.depends_on=[t99] | 越界边丢弃，`[[t1]]` |
| 循环 t1→t2→t1 | 强制打破，`[[t1], [t2]]` 或 `[[t2], [t1]]`（取入度最小者） |

**差距**：
1. 把 [planner.py:344-417](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L344-L417)
   `_validate_dag` 迁移到 `dispatcher.py` 并改名为 `resolve_waves`（保持算法逻辑）
2. 把 [orchestrator.py:336-411](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L336-L411)
   `_dispatch_batch_node` / `_dispatch_batch_router` 迁移到 `dispatcher.py` 并改名为 `dispatch_node` / `_dispatch_router`
3. 把 [orchestrator.py:874-883](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L874-L883)
   `_check_next_level_node` / `_route_after_level` 迁移到 `nodes.py` 并改名为 `barrier_node` / `_route_after_barrier`
4. **新增 `_route_after_aggregate` 条件边**（D16）

### D4. `SubtaskState` 扩展字段 + upstream_findings 注入

**现状**：
[orchestrator.py:140-180](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L140-L180)
已实现 `_inject_dependency_context`（**单函数**），完成依赖上下文注入。
[blackboard.py:179](file:///d:/java/agentprojects/agentx/backend/app/team/blackboard.py#L179)
`findings: Annotated[dict[str, str], _merge_dict]` 仍是裸字符串 dict（应为 `dict[str, Finding]`）。

**目标**：`SubtaskState` 新增 `upstream_findings` / `wave_index` / `replan_count` 字段，
dispatcher 拆为双函数（`_inject_upstream_findings` + `_compose_input_with_upstream`）。

```python
# state.py
class SubtaskState(TypedDict, total=False):
    """单个子任务的输入 state（Send 携带）。"""

    task: TeamTask
    upstream_findings: dict[str, Finding]   # 新增：依赖任务的结果
    parent_thread_id: str
    child_thread_id: str
    todos: list[dict]
    history: list[Any]
    profile_prompt: str
    permission_mode: str
    workspace_path: str
    chat_model: Any
    wave_index: int                          # 新增：当前 wave 索引（用于 findings key）
    replan_count: int                        # 新增：replan 次数


class TeamState(TypedDict, total=False):
    """Team 路径的全局 state。"""

    message: str
    plan: list[TeamTask]                     # 改为 list[TeamTask]（替代旧 TeamPlanTask）
    pending_waves: list[list[TeamTask]]      # 新增：待执行的 wave 列表（Kahn 分层结果）
    findings: dict[str, Finding]             # 改为 dict[str, Finding]（替代旧 str）
    errors: list[str]
    warnings: list[str]                      # 新增：warning channel (D14)
    completed_task_ids: set[str]             # 新增：已完成的 task id 集合
    replan_count: int                        # 新增：replan 次数
    thread_id: str
    chat_model: Any
    # ... 其他透传字段 ...
```

`_inject_upstream_findings` 伪代码（拆分自现有 `_inject_dependency_context`）：

```python
# dispatcher.py
def _inject_upstream_findings(task: TeamTask, findings: dict[str, Finding]) -> dict[str, Finding]:
    """根据 task.depends_on 从 state.findings 提取上游结果。"""
    upstream: dict[str, Finding] = {}
    for dep_id in task.depends_on:
        for key, finding in findings.items():
            if finding.task_id == dep_id:
                upstream[key] = finding
                break
        else:
            logger.warning("upstream finding missing at dispatch time", dep_id=dep_id, task_id=task.id)
    return upstream


def _compose_input_with_upstream(task: TeamTask, upstream: dict[str, Finding]) -> str:
    """把 upstream_findings 拼入 agent context。"""
    if not upstream:
        return task.description

    settings = get_settings()
    max_chars = settings.team_result_max_chars

    sections: list[str] = ["[依赖任务结果]"]
    for key, finding in upstream.items():
        content = finding.content
        if len(content) > max_chars:
            content = content[:max_chars] + "\n[结果已截断]"
        sections.append(f"--- #{finding.task_id} [{finding.agent}] ---\n{content}")

    sections.append(f"[当前任务]\n{task.description}")
    return "\n\n".join(sections)
```

**差距**：
1. 把 [orchestrator.py:140-180](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L140-L180)
   `_inject_dependency_context` 拆分为 `_inject_upstream_findings` + `_compose_input_with_upstream`
2. [blackboard.py:179](file:///d:/java/agentprojects/agentx/backend/app/team/blackboard.py#L179)
   findings 类型从 `dict[str, str]` 改为 `dict[str, Finding]`
3. `SubtaskState` 新增 `upstream_findings` / `wave_index` / `replan_count` 字段

### D5. Semaphore 限流实现

**现状**：
[orchestrator.py:1139](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L1139)
已实现 `team_semaphore = asyncio.Semaphore(max_parallel)`。
[settings.py:187](file:///d:/java/agentprojects/agentx/backend/app/config/settings.py#L187)
`agent_team_max_parallel: int = Field(default=3, ge=1, le=5)`——命名、默认值、范围均与 spec 不符。

**目标**：配置项改名为 `team_max_concurrency: int = Field(default=5, ge=1, le=20)`，
Semaphore 实现迁移到 `scheduler.py` 并改为 `lru_cache` 单例：

```python
# scheduler.py
import asyncio
from functools import lru_cache


@lru_cache(maxsize=1)
def _get_team_semaphore() -> asyncio.Semaphore:
    """全局团队并发 Semaphore（单例，进程内共享）。"""
    settings = get_settings()
    return asyncio.Semaphore(settings.team_max_concurrency)


async def acquire_and_run(
    task: TeamTask,
    runner_coro: Coroutine[Any, Any, TeamSubtaskResult],
    thread_id: str,
    abort_event: asyncio.Event,
) -> TeamSubtaskResult:
    """限流 + abort cancel + retry 的统一执行入口。"""
    semaphore = _get_team_semaphore()

    async with semaphore:
        # 注册 asyncio.Task 以支持 abort cancel
        task_obj = asyncio.create_task(runner_coro)
        register_running_task(thread_id, task_obj)

        try:
            abort_waiter = asyncio.create_task(abort_event.wait())
            done, pending = await asyncio.wait(
                {task_obj, abort_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if abort_waiter in done:
                task_obj.cancel()
                raise asyncio.CancelledError()
            return task_obj.result()
        except asyncio.CancelledError:
            return TeamSubtaskResult(
                agent=task.agent,
                success=False,
                payload="用户中止",
            )
        finally:
            if abort_waiter in pending:
                abort_waiter.cancel()
            _running_tasks[thread_id] = [t for t in _running_tasks.get(thread_id, []) if t is not task_obj]
```

**差距**：
1. [settings.py:187](file:///d:/java/agentprojects/agentx/backend/app/config/settings.py#L187)
   `agent_team_max_parallel` → `team_max_concurrency`，默认 3→5，范围 1-5→1-20（D15）
2. [orchestrator.py:1139](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L1139)
   `team_semaphore` 迁移到 `scheduler.py._get_team_semaphore`，改为 `lru_cache` 单例

### D6. `DangerousTaskClassifier` 接口定义

**现状**：
[utils/text.py:274-294](file:///d:/java/agentprojects/agentx/backend/app/utils/text.py#L274-L294)
已实现 `compile_keyword_patterns`（ASCII `\b` 词边界 + CJK 子串匹配）。
[planner.py:272-276](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L272-L276)
旧 `_looks_like_dangerous_task` 子串匹配应被替代。
[tests/python/unit/test_keyword_patterns.py](file:///d:/java/agentprojects/agentx/tests/python/unit/test_keyword_patterns.py)（109 行）
已覆盖 ASCII 词边界。

**目标**：新建 `classifier.py` 封装 `DangerousTaskClassifier` 类，LLM 路径 + 关键词降级 + 缓存
+ SSE warning 发射：

```python
# classifier.py
class DangerousTaskClassifier:
    """LLM 危险任务分类器，替代 _looks_like_dangerous_task 子串匹配。"""

    def __init__(self, chat_model: Any | None = None):
        self._chat_model = chat_model
        self._structured = None
        if chat_model is not None:
            try:
                self._structured = chat_model.with_structured_output(ClassificationResult)
            except Exception:
                logger.warning("LLM does not support structured output, fallback to keyword")

    async def classify(self, task: TeamTask, available_tools: list[str]) -> ClassificationResult:
        # 1. 缓存命中
        cache_key = self._cache_key(task.description)
        if cached := self._cache_get(cache_key):
            return cached

        # 2. LLM 路径
        if self._structured is not None:
            try:
                result = await self._structured.ainvoke(self._build_prompt(task, available_tools))
                self._cache_put(cache_key, result)
                return result
            except Exception:
                logger.warning("DangerousTaskClassifier LLM failed, fallback to keyword")

        # 3. 关键词降级（基于 utils/text.py:compile_keyword_patterns）
        return self._keyword_fallback(task)

    def _keyword_fallback(self, task: TeamTask) -> ClassificationResult:
        """改进的关键词匹配：复用 compile_keyword_patterns。
        ASCII 用 \b 词边界，CJK 用子串匹配（已有实现）。
        """
        # 复用 utils/text.py:compile_keyword_patterns
        ...
```

**差距**：
1. 新建 `classifier.py`，封装 `DangerousTaskClassifier` 类（基于 [utils/text.py:274-294](file:///d:/java/agentprojects/agentx/backend/app/utils/text.py#L274-L294)
   `compile_keyword_patterns`）
2. 实现 LLM `with_structured_output(ClassificationResult)` 路径 + 缓存（按 description hash）
3. 补 CJK 词边界测试（[tests/python/unit/test_keyword_patterns.py](file:///d:/java/agentprojects/agentx/tests/python/unit/test_keyword_patterns.py)
   已覆盖 ASCII，需补 CJK 误命中场景）
4. SSE warning 发射（受 D14 阻塞）——危险任务改写时发射 `warning` 事件 + 写入 `state.warnings`
5. 删除 [planner.py:272-276](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L272-L276)
   旧 `_looks_like_dangerous_task`

### D7. 未知 agent fallback 与统一 `execute_node`

**现状**：
[orchestrator.py:582-588](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L582-L588)
已实现 `_NODE_DISPATCH["default"]` fallback 到 `code` runner，但缺 SSE warning 发射与 `state.warnings` 写入。
6 个分类节点仍在 [orchestrator.py](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py) 内
（未提取到 `nodes.py`）。

**目标**：合并 6 个分类节点为单一 `execute_node`，`_NODE_DISPATCH` 派发表迁移到 `nodes.py`：

```python
# nodes.py
@dataclass
class SubtaskConfig:
    agent_type: str
    runner: Callable[..., Awaitable[TeamSubtaskResult]]
    workspace_inherit: bool = False
    emit_delegation: bool = True
    pre_run_hook: Callable | None = None


_NODE_DISPATCH: dict[str, SubtaskConfig] = {
    "deep": SubtaskConfig(agent_type="deep", runner=_get_deep_runner, workspace_inherit=True),
    "code": SubtaskConfig(agent_type="code", runner=_get_code_runner, workspace_inherit=True),
    "rag": SubtaskConfig(agent_type="rag", runner=_get_rag_runner, workspace_inherit=False),
    "web": SubtaskConfig(agent_type="web", runner=_get_web_runner, workspace_inherit=False),
    "builtin": SubtaskConfig(agent_type="builtin", runner=_get_builtin_runner, workspace_inherit=False),
    # team_role 与 custom 在运行时根据 settings 动态注册
}

_FALLBACK_CONFIG = SubtaskConfig(
    agent_type="default",
    runner=_get_code_runner,  # D7: fallback 到 code
    workspace_inherit=True,
    emit_delegation=True,
    pre_run_hook=_log_unknown_agent_fallback,  # logger.warning + 写入 state.warnings + SSE warning
)


async def execute_node(state: SubtaskState) -> dict:
    """统一执行节点，替代 6 个分类节点。"""
    task = state["task"]
    upstream_findings = state.get("upstream_findings", {})
    wave_index = state.get("wave_index", 0)
    thread_id = state["parent_thread_id"]
    abort_event = state.get("abort_event")

    config = _NODE_DISPATCH.get(task.agent, _FALLBACK_CONFIG)
    if config.pre_run_hook:
        config.pre_run_hook(task, state)  # fallback 时发射 warning

    # 危险任务分类（D6）
    classifier = DangerousTaskClassifier(state.get("chat_model"))
    classification = await classifier.classify(task, available_tools=_get_available_tools())
    if classification.is_dangerous and task.agent != classification.suggested_agent:
        # 改写到 suggested_agent + 发射 warning SSE (受 D14 阻塞)
        writer(make_sse_event("warning", {
            "task_id": task.id,
            "message": f"危险任务改写: {task.agent} -> {classification.suggested_agent}",
            "reason": classification.reason,
        }))
        task = task.model_copy(update={"agent": classification.suggested_agent})
        config = _NODE_DISPATCH.get(task.agent, _FALLBACK_CONFIG)

    # 注入 upstream findings 到 input
    injected_input = _compose_input_with_upstream(task, upstream_findings)

    # 限流 + retry + abort cancel（D5/D8/D9）
    result = await run_with_retry(
        task=task,
        runner_factory=lambda: acquire_and_run(
            task=task,
            runner_coro=config.runner(task, injected_input, state),
            thread_id=thread_id,
            abort_event=abort_event,
        ),
    )

    return _make_subtask_state_update(result, task, wave_index)
```

迁移映射表：

| 旧节点（删除） | 新统一节点 | 迁移策略 |
|---|---|---|
| `_deep_node` | `execute_node`（agent="deep"） | `_NODE_DISPATCH["deep"].runner = _get_deep_runner` |
| `_code_node` | `execute_node`（agent="code"） | `_NODE_DISPATCH["code"].runner = _get_code_runner` |
| `_builtin_node` | `execute_node`（agent="builtin"） | `_NODE_DISPATCH["builtin"].runner = _get_builtin_runner` |
| `_team_role_node` | `execute_node`（agent=team_role 名） | `_NODE_DISPATCH[role].runner = _build_custom_agent_runner`；配置缺失时显式失败（D11） |
| `_custom_node` | `execute_node`（agent=custom 名） | `_NODE_DISPATCH[custom].runner = _get_custom_runner` |
| `_default_node` | `execute_node`（default 分支） | `_NODE_DISPATCH` 查不到时走 `_FALLBACK_CONFIG.runner = _get_code_runner`（D7） |

**差距**：
1. 把 [orchestrator.py:582-588](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L582-L588)
   `_NODE_DISPATCH` 迁移到 `nodes.py`
2. 把 6 个分类节点合并为单一 `execute_node`
3. 新增 `_FALLBACK_CONFIG.pre_run_hook = _log_unknown_agent_fallback`：发射 warning SSE + 写入 `state.warnings`（受 D14 阻塞）
4. 删除 [orchestrator.py:353-564](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L353-L564)
   6 个旧节点函数

### D8. `asyncio.Task` abort 实现（registry + 主动 cancel）

**现状**：
[scheduler.py:253-287](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py#L253-L287)
`_run_subtask_stream._iterate` 用 `asyncio.wait(FIRST_COMPLETED, timeout=5)` **轮询式竞速 abort**——
每 5s 唤醒一次检查 abort_event，无法中断 LLM 长调用本身，且延迟较高。

**目标**：把子任务 runner 包装为 `asyncio.Task`，注册到 `_running_tasks[thread_id]` 全局 dict，
abort handler 主动调用 `task.cancel()` 中断 LLM 长调用：

```python
# scheduler.py
_running_tasks: dict[str, list[asyncio.Task]] = {}


def register_running_task(thread_id: str, task: asyncio.Task) -> None:
    _running_tasks.setdefault(thread_id, []).append(task)


def cancel_running_tasks(thread_id: str) -> int:
    """abort handler 调用：cancel 该 thread 的所有运行中子任务。"""
    tasks = _running_tasks.get(thread_id, [])
    cancelled = 0
    for t in tasks:
        if not t.done():
            t.cancel()
            cancelled += 1
    _running_tasks[thread_id] = []
    return cancelled


# abort handler 注册（在 runner.py 的 run_team_path 中）
async def run_team_path(state: TeamState, ...):
    thread_id = state["thread_id"]
    abort_event = state.get("abort_event")
    if abort_event is not None:
        abort_event.add_done_callback(lambda: cancel_running_tasks(thread_id))
    ...
```

`acquire_and_run` 中的 abort 处理详见 D5。

**差距**：
1. 把 [scheduler.py:253-287](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py#L253-L287)
   `_run_subtask_stream._iterate` 的 `asyncio.wait(timeout=5)` 轮询式改为 `register_running_task` +
   `cancel_running_tasks` 主动 cancel
2. 新增 `_running_tasks: dict[str, list[asyncio.Task]]` 全局 dict
3. 在 `runner.run_team_path` 注册 `abort_event.add_done_callback(lambda: cancel_running_tasks(thread_id))`
4. `acquire_and_run` 显式捕获 `CancelledError`，返回 `TeamSubtaskResult(success=False, payload="用户中止")`

### D9. Retry 策略表

**现状**：完全未实现。无 `max_retries` / 指数退避 / `TRANSIENT_ERRORS` 分类。

**目标**：新增 `max_retries=2`，指数退避 `1s/2s/4s`。仅重试瞬态失败，逻辑错误不重试。
记录 `retries` 字段到 `subtask_results`。

| 错误类型 | 是否重试 | 退避 | 说明 |
|---|---|---|---|
| `asyncio.TimeoutError` | 是 | 1s, 2s, 4s | 瞬态超时 |
| `httpx.ConnectError` / 网络异常 | 是 | 1s, 2s, 4s | 上游网络抖动 |
| `httpx.HTTPStatusError` 5xx | 是 | 1s, 2s, 4s | 上游服务故障 |
| `httpx.HTTPStatusError` 4xx | 否 | - | 客户端错误，重试无意义 |
| `ValueError` / 逻辑错误 | 否 | - | 任务描述非法，重试无意义 |
| `asyncio.CancelledError` | 否 | - | abort 中止，不重试 |
| `PermissionError` / 授权失败 | 否 | - | 权限问题，不重试 |
| 其他未识别 `Exception` | 否 | - | 保守不重试，记录到 errors |

```python
# scheduler.py
TRANSIENT_ERRORS = (asyncio.TimeoutError, httpx.ConnectError, httpx.HTTPStatusError)


async def run_with_retry(
    task: TeamTask,
    runner_factory: Callable[[], Coroutine],
    max_retries: int = 2,
) -> TeamSubtaskResult:
    """带重试的 runner 执行。"""
    settings = get_settings()
    max_retries = settings.team_max_retries

    for attempt in range(max_retries + 1):
        try:
            result = await runner_factory()
            result.retries = attempt
            return result
        except TRANSIENT_ERRORS as e:
            if attempt >= max_retries:
                logger.warning("subtask exhausted retries", task_id=task.id, attempts=attempt, error=str(e))
                return TeamSubtaskResult(
                    agent=task.agent,
                    success=False,
                    payload=f"重试 {attempt} 次后仍失败: {e}",
                    retries=attempt,
                )
            backoff = 2 ** attempt  # 1s, 2s, 4s
            logger.info("subtask retry", task_id=task.id, attempt=attempt, backoff=backoff)
            await asyncio.sleep(backoff)
        except asyncio.CancelledError:
            raise  # abort 中止，不重试
        except Exception as e:
            # 非瞬态错误，不重试
            logger.warning("subtask non-transient error, no retry", task_id=task.id, error=str(e))
            return TeamSubtaskResult(
                agent=task.agent,
                success=False,
                payload=f"非瞬态错误: {e}",
                retries=attempt,
            )
```

**差距**：
1. 新增 `TRANSIENT_ERRORS` 常量
2. 新增 `run_with_retry` 函数（含指数退避 + `team_max_retries` 配置）
3. 新增 [settings.py](file:///d:/java/agentprojects/agentx/backend/app/config/settings.py)
   `team_max_retries: int = Field(default=2, ge=0, le=5)` 配置（D15）
4. `TeamSubtaskResult` schema 新增 `retries: int` 字段

### D10. `replan_node` 流程（含路由修复，详见 D16）

**现状**：
[orchestrator.py:898-1026](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L898-L1026)
已实现 `_replan_check_node`，但**触发位置错误**——当前在
[orchestrator.py:874-883](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L874-L883)
`_check_next_level_node` 检测到「无下一层 wave」时触发（`check_next_level → replan_check`），
spec 要求在 `_aggregate_node` 质量门失败时触发（`_aggregate_node → _route_after_aggregate → replan`）。
当前 `_aggregate_node` 后直接 `END`，**质量门失败路径完全缺失**。

**目标**：`replan_node` 在质量门失败时把原任务 + findings + errors 重新喂给 planner：

```python
# nodes.py
async def replan_node(state: TeamState) -> dict:
    """质量门失败时的迭代式重规划。"""
    writer = get_stream_writer()
    settings = get_settings()
    replan_count = state.get("replan_count", 0)
    max_replans = settings.team_max_replan_attempts

    if replan_count >= max_replans:
        logger.info("team replan limit reached", replan_count=replan_count, max=max_replans)
        return {"_route": "end_failure"}

    findings = state.get("findings", {})
    errors = state.get("errors", [])
    plan = state.get("plan", [])
    message = state["message"]

    planner = Planner(state.get("chat_model"))
    new_plan = await planner.replan(
        original_message=message,
        previous_plan=plan,
        findings=findings,
        errors=errors,
        hint=plan[0].is_dangerous_hint if plan else False,
    )

    if not new_plan.tasks:
        return {"_route": "end_failure"}

    waves = resolve_waves(new_plan.tasks)

    writer(make_sse_event("replan", {
        "new_tasks": [t.model_dump() for t in new_plan.tasks],
        "replan_count": replan_count + 1,
        "reason": "quality_gate_failed",
    }))

    return {
        "plan": new_plan.tasks,
        "pending_waves": waves,
        "replan_count": replan_count + 1,
        "findings": {},
        "errors": [],
    }


def _route_after_replan(state: TeamState) -> str:
    """replan 条件边路由。"""
    pending_waves = state.get("pending_waves", [])
    if pending_waves:
        return "dispatch"
    return "end_failure"
```

**差距**：
1. 把 [orchestrator.py:898-1026](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L898-L1026)
   `_replan_check_node` 迁移到 `nodes.py` 并改名为 `replan_node`
2. **新增 `_route_after_aggregate` 条件边**（D16）：质量门通过 → END / 失败 → replan
3. **移除** `_check_next_level_node → _replan_check_node` 的路径（replan 不再在 wave 完成后触发）
4. `Planner.replan` 实现（结构化输出 + 原 plan/findings/errors 喂回）

### D11. 静默降级消除 + validate_team_subagents

**现状**：
[scheduler.py:356-369](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py#L356-L369)
已实现 `_run_team_role_subtask` 显式失败（不再静默降级到 coding Expert）。
[config/subagents.py:294-327](file:///d:/java/agentprojects/agentx/backend/app/config/subagents.py#L294-L327)
已实现 `validate_team_subagents`。
[main.py:113-115](file:///d:/java/agentprojects/agentx/backend/app/main.py#L113-L115)
lifespan 已调用。**仅缺 SSE warning 发射**。

**目标**：`_run_team_role_subtask` 配置缺失时发射 `warning` SSE 事件 + 写入 `state.warnings`
（受 D14 阻塞），不再静默 fallback。`validate_team_subagents` 启动校验已在位。

**差距**：
1. [scheduler.py:356-369](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py#L356-L369)
   `_run_team_role_subtask` 新增 SSE warning 发射 + `state.warnings` 写入（受 D14 阻塞）
2. [config/subagents.py:294-327](file:///d:/java/agentprojects/agentx/backend/app/config/subagents.py#L294-L327)
   `validate_team_subagents` 验证与 `team_*` 命名对齐（D15）

### D12. Token 事件 schema 统一

**现状**：
[sse/events.py:98-99](file:///d:/java/agentprojects/agentx/backend/app/sse/events.py#L98-L99)
token 事件 payload 为**纯字符串**，未结构化为 `{agent, content}`。

**目标**：所有 `execute` 节点统一 token 事件 schema 为 `{agent: str, content: str}`，
aggregator token 事件为 `{agent: "aggregator", content: str}`。

**差距**：
1. [sse/events.py:98-99](file:///d:/java/agentprojects/agentx/backend/app/sse/events.py#L98-L99)
   token 事件 payload 改为 `{agent: str, content: str}` 结构
2. 各 runner 发射 token 事件时填充 `agent` 字段
3. aggregator token 事件 `agent="aggregator"`

### D13. Findings Key 命名规范

**现状**：
[orchestrator.py:433](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L433)
仍用 `key = f"{result.agent}-{task_index}"` 旧格式——同类型多任务在多波次场景下会覆盖
（如两个 code 任务索引都是 `code-0`）。

**目标**：Key 格式改为 `{agent}:{task_id}:{wave_index}`，避免覆盖。

**新格式**：
- `code:t1:0` —— code agent 在 wave 0 执行 task t1 的结果
- `rag:t2:1` —— rag agent 在 wave 1 执行 task t2 的结果
- `deep:t3:2` —— deep agent 在 wave 2 执行 task t3 的结果

```python
# nodes.py (execute_node)
def _make_finding_key(agent: str, task: TeamTask, wave_index: int) -> str:
    return f"{agent}:{task.id}:{wave_index}"


def _make_subtask_state_update(result: TeamSubtaskResult, task: TeamTask, wave_index: int) -> dict:
    key = _make_finding_key(result.agent, task, wave_index)
    finding = Finding(
        agent=result.agent,
        task_id=task.id,
        wave_index=wave_index,
        content=result.payload,
        success=result.success,
        error=result.error,
        retries=result.retries,
    )
    return {
        "findings": {key: finding},  # reducer 合并
        "completed_task_ids": {task.id},
    }
```

**差距**：
1. [orchestrator.py:433](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L433)
   `key = f"{result.agent}-{task_index}"` 改为 `key = f"{agent}:{task.id}:{wave_index}"`
2. `_make_subtask_state_update` 新增 `Finding` 对象构造（不再用裸字符串）
3. `_merge_findings` reducer 用复合 key 合并

### D14. state.warnings channel（新增）

**现状**：
[blackboard.py:148-188](file:///d:/java/agentprojects/agentx/backend/app/team/blackboard.py#L148-L188)
`TeamState` 无 `warnings` 字段。D6/D7/D11 的可观测性（危险任务改写、fallback 触发、
team_role 缺配置等）均无统一的 warning 写入与 SSE 发射通道。

**目标**：`TeamState` 新增 `warnings: list[str]` 字段 + `_merge_warnings` reducer +
`aggregate_node` 发射 warning SSE。这是 D6/D7/D11 的共享基础设施。

```python
# state.py
class TeamState(TypedDict, total=False):
    # ... 其他字段 ...
    warnings: list[str]   # 新增：warning channel


# blackboard.py
def _merge_warnings(left: list[str], right: list[str]) -> list[str]:
    """list 拼接去重（保序）。"""
    seen = set(left)
    merged = list(left)
    for w in right:
        if w not in seen:
            merged.append(w)
            seen.add(w)
    return merged


# nodes.py (aggregate_node)
async def aggregate_node(state: TeamState) -> dict:
    """汇总节点：调用 aggregator + 质量门 + 发射累积的 warning SSE。"""
    writer = get_stream_writer()

    # 发射累积的 warning（来自 D6/D7/D11 写入）
    for warning in state.get("warnings", []):
        writer(make_sse_event("warning", {"message": warning}))

    # 调用 aggregator + 质量门
    result = await aggregator.run_aggregator(state)
    # ...
    return {...}
```

**目标行为**：
- D6 危险任务改写时：`state.warnings` 写入 `"危险任务改写: {old} -> {new}"` + SSE warning
- D7 fallback 触发时：`state.warnings` 写入 `"未知 agent {agent} fallback 到 code"` + SSE warning
- D11 team_role 缺配置时：`state.warnings` 写入 `"团队角色 {agent} 配置缺失 system_prompt"` + SSE warning
- `aggregate_node` 统一把累积的 warning 通过 SSE 发射给前端

**差距**：
1. [blackboard.py:148-188](file:///d:/java/agentprojects/agentx/backend/app/team/blackboard.py#L148-L188)
   `TeamState` 新增 `warnings: list[str]` 字段
2. [blackboard.py](file:///d:/java/agentprojects/agentx/backend/app/team/blackboard.py)
   新增 `_merge_warnings` reducer（list 拼接去重）
3. `aggregate_node` 新增累积 warning 的 SSE 发射逻辑
4. D6/D7/D11 的 pre_run_hook / classifier / `_run_team_role_subtask` 写入 `state.warnings`

### D15. 配置项重命名（新增）

**现状**：
[settings.py:187](file:///d:/java/agentprojects/agentx/backend/app/config/settings.py#L187)
`agent_team_max_parallel: int = Field(default=3, ge=1, le=5)`。
[settings.py:193](file:///d:/java/agentprojects/agentx/backend/app/config/settings.py#L193)
`agent_team_max_replans: int = Field(default=2, ge=0, le=5)`。
另有 `agent_team_subtask_timeout`。

**目标**：统一改为 `team_*` 前缀，默认值与范围与 spec 对齐：

| 旧配置项 | 新配置项 | 默认值 | 范围 | 用途 |
|---|---|---|---|---|
| `agent_team_max_parallel` | `team_max_concurrency` | 5（旧 3） | 1-20（旧 1-5） | Semaphore 并发上限 |
| `agent_team_max_replans` | `team_max_replan_attempts` | 1（旧 2） | 0-5 | replan 最大次数 |
| -（新增） | `team_max_retries` | 2 | 0-5 | 子任务失败重试次数 |
| -（新增） | `team_classifier_timeout` | 2.0 | 0.1-10.0 | 危险分类器 LLM 超时 |

```python
# settings.py
class Settings(BaseSettings):
    # ... 其他字段 ...
    team_max_concurrency: int = Field(default=5, ge=1, le=20)
    team_max_replan_attempts: int = Field(default=1, ge=0, le=5)
    team_max_retries: int = Field(default=2, ge=0, le=5)
    team_classifier_timeout: float = Field(default=2.0, ge=0.1, le=10.0)
    team_result_max_chars: int = Field(default=2000, ge=100, le=10000)  # 已存在，保留
```

**差距**：
1. [settings.py:187](file:///d:/java/agentprojects/agentx/backend/app/config/settings.py#L187)
   `agent_team_max_parallel` → `team_max_concurrency`，默认 3→5，范围 1-5→1-20
2. [settings.py:193](file:///d:/java/agentprojects/agentx/backend/app/config/settings.py#L193)
   `agent_team_max_replans` → `team_max_replan_attempts`，默认 2→1
3. 新增 `team_max_retries: int = Field(default=2, ge=0, le=5)`
4. 新增 `team_classifier_timeout: float = Field(default=2.0, ge=0.1, le=10.0)`
5. 全量替换引用：`git grep agent_team_` → `team_*`
   （[orchestrator.py:1139](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L1139)
   `team_semaphore` 引用处、[config/subagents.py:294-327](file:///d:/java/agentprojects/agentx/backend/app/config/subagents.py#L294-L327)
   `validate_team_subagents` 等）

### D16. D10 Replanner 路由修复（新增）

**现状**：
[orchestrator.py:874-883](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L874-L883)
`_check_next_level_node` + `_route_after_level` 把「无下一层 wave」路由到
[orchestrator.py:898-1026](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L898-L1026)
`_replan_check_node`。当前 `_aggregate_node` 后直接 `END`，**质量门失败路径完全缺失**。

**目标**：
1. `_aggregate_node` 后增加条件边 `_route_after_aggregate`：
   - 质量门通过 → END
   - 质量门失败 → `replan_node`
2. **移除** `check_next_level → replan_check` 的路径——replan 不再在「wave 完成后」触发，
   只在「质量门失败时」触发
3. `barrier_node` 后只路由到 `dispatch`（有 wave）或 `aggregate`（无 wave），不再路由到 replan

```python
# graph_builder.py
def _build_team_graph() -> Any:
    graph: Any = StateGraph(TeamState)

    graph.add_node("plan", plan_node)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("execute", execute_node)
    graph.add_node("barrier", barrier_node)
    graph.add_node("aggregate", aggregate_node)
    graph.add_node("replan", replan_node)

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "dispatch")
    graph.add_conditional_edges("dispatch", _dispatch_router)
    graph.add_edge("execute", "barrier")
    # barrier 只路由到 dispatch（有 wave）或 aggregate（无 wave），不再路由到 replan
    graph.add_conditional_edges("barrier", _route_after_barrier)
    # 新增：aggregate 条件边（质量门通过 → END / 失败 → replan）
    graph.add_conditional_edges("aggregate", _route_after_aggregate)
    graph.add_conditional_edges("replan", _route_after_replan)
    return graph.compile()


def _route_after_barrier(state: TeamState) -> str:
    """barrier 条件边：有 wave → dispatch；无 → aggregate（不再路由到 replan）。"""
    pending_waves = state.get("pending_waves", [])
    if pending_waves:
        return "dispatch"
    return "aggregate"


def _route_after_aggregate(state: TeamState) -> str:
    """aggregate 条件边：质量门通过 → END；失败 → replan。"""
    quality_gate_passed = state.get("quality_gate_passed", True)
    if quality_gate_passed:
        return END
    return "replan"
```

**差距**：
1. **移除** [orchestrator.py:874-883](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L874-L883)
   `_check_next_level_node → _replan_check_node` 的路径
2. **新增** `_route_after_aggregate` 条件边（质量门通过 → END / 失败 → replan）
3. `_aggregate_node` 新增 `quality_gate_passed` 字段写入 state
4. [orchestrator.py:898-1026](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L898-L1026)
   `_replan_check_node` 迁移到 `nodes.replan_node`，仅在 `_route_after_aggregate` 路由到 `replan` 时执行

## Risks

### R1. LLM 不支持 `with_structured_output`

**风险**：部分 LLM provider（如本地小模型）不支持 `with_structured_output`，导致 plan 解析失败。

**缓解**：
- 保留正则解析 fallback（D2 `_AGENT_PREFIX_RE`）
- 启动时检测 LLM 能力，若不支持则记录 warning 并默认走 fallback 路径
- 单元测试覆盖 fallback 路径

### R2. DAG wave 死锁

**风险**：`barrier_node → dispatch` 回流可能引发 LangGraph 死锁或递归超限。

**缓解**：
- `pending_waves` 是有限列表，每 wave 执行后弹出，不会无限回流
- 设置 `recursion_limit` 兜底（现有 chat.py 已有处理）
- 单元测试覆盖多层场景（5 wave 以上）

### R3. findings 注入导致 input 过长

**风险**：多依赖任务 input 拼接后超过 LLM context window。

**缓解**：
- 每个依赖 finding 截断到 `team_result_max_chars`（默认 2000）
- 3 依赖 × 2000 = 6000 字符，对 128k context 的现代 LLM 可接受
- 若仍超限，未来可引入「findings 压缩」（summarizer 中间层）

### R4. Semaphore 死锁

**风险**：子任务内部又 acquire 同一 semaphore（递归调用 team 路径）会导致死锁。

**缓解**：
- `asyncio.Semaphore` 在同一个 task 内可重入（Python 3.10+），但跨 task 不可重入
- 文档约定：子任务 runner 不得再调用 `run_team_path`（避免嵌套 team）
- 单元测试覆盖并发场景（10 子任务 + max_concurrency=5）

### R5. abort cancel 级联

**风险**：`task.cancel()` 可能引发未捕获的 `CancelledError` 在其他协程中级联。

**缓解**：
- `acquire_and_run` 显式捕获 `CancelledError`，返回 `TeamSubtaskResult(success=False, payload="用户中止")`
- `run_with_retry` 不重试 `CancelledError`（直接 raise）
- abort_waiter 在 finally 中 cancel，避免泄漏

### R6. replan 无限循环

**风险**：LLM 不断追加新任务，导致 replan 循环。

**缓解**：
- `team_max_replan_attempts=1`（可配置，默认保守）
- replan_count >= max 时直接结束（失败）
- 发射 `replan` SSE 事件便于前端观测

### R7. 危险分类器延迟

**风险**：LLM 分类延迟过高（>2s），拖慢整体 plan。

**缓解**：
- 缓存按 `task.description` hash，相同描述不重复分类
- LLM 不可用时降级到关键词匹配（毫秒级）
- 可配置超时（`team_classifier_timeout: float = 2.0`）

### R8. D16 路由修复破坏现有 wave 完成后行为

**风险**：移除 `check_next_level → replan_check` 路径后，原 wave 完成后的 replan 触发场景丢失。

**缓解**：
- 原 replan 触发场景本就错误（不应在 wave 完成后触发，应在质量门失败时触发）
- D16 修复后所有 replan 触发都集中在 `_aggregate_node → _route_after_aggregate → replan`
- 单元测试覆盖质量门失败 → replan → plan → dispatch 完整路径

## Testing Strategy

### 单元测试

**已存在（合并后）**：

- [tests/python/unit/test_team_dag.py](file:///d:/java/agentprojects/agentx/tests/python/unit/test_team_dag.py)（681 行）：
  DAG 分层 / dispatch / barrier 路由（仅需验证命名对齐：`_validate_dag` → `resolve_waves`）
- [tests/python/unit/test_team_fallback.py](file:///d:/java/agentprojects/agentx/tests/python/unit/test_team_fallback.py)（333 行）：
  fallback / team_role 显式失败（仅需补 SSE warning 断言）
- [tests/python/unit/test_team_stability.py](file:///d:/java/agentprojects/agentx/tests/python/unit/test_team_stability.py)（450 行）：稳定性
- [tests/python/unit/test_keyword_patterns.py](file:///d:/java/agentprojects/agentx/tests/python/unit/test_keyword_patterns.py)（109 行）：
  ASCII 词边界（需补 CJK + LLM 分类器测试）
- [tests/python/unit/test_quality_gate.py](file:///d:/java/agentprojects/agentx/tests/python/unit/test_quality_gate.py)（105 行）：
  质量门（需补 `_route_after_aggregate` 路由测试）

**需新增**：

- `tests/python/unit/test_team_v2.py`：
  - 模块拆分后的核心流程（plan / dispatch / execute / aggregate 端到端）
  - `_merge_warnings` reducer 合并去重
  - `state.warnings` channel 累积 + SSE 发射
- `tests/python/unit/test_team_v2_concurrency.py`：
  - Semaphore 限流（10 子任务 + `team_max_concurrency=5` 验证最多 5 并发）
  - `_running_tasks` registry + `cancel_running_tasks` abort cancel
  - retry 策略（瞬态错误重试、逻辑错误不重试、退避间隔）
- `tests/python/unit/test_team_v2_classifier.py`：
  - `DangerousTaskClassifier` LLM 路径
  - 关键词降级路径（CJK 词边界补齐）
  - 缓存命中
- `tests/python/unit/test_team_v2_replan.py`：
  - `replan_node` 质量门失败时追加任务
  - `_route_after_aggregate` 路由：质量门通过 → END / 失败 → replan
  - `team_max_replan_attempts` 限制
  - findings key 复合格式 `{agent}:{task_id}:{wave_index}`
  - token 事件 schema 统一 `{agent, content}`

### 集成测试

- 顺序任务端到端：`[code] 读取 → [deep][after:t1] 修改` 验证 deep 拿到 code 的 findings
- 并行任务回归：无 `depends_on` 的任务仍并行执行
- 混合任务：`[code] t1 → [code] t2` + `[deep][after:t1,t2] t3` 验证 t3 等 t1、t2 都完成
- replan：第一轮 `[code] 读取` → 质量门失败 → replan 追加 `[deep][after:t1] 修改`（D16 修复后路径）
- fallback：`[unknown_agent] 任务` 验证走 code runner + 发射 warning SSE
- abort：子任务执行中 abort 验证返回 "用户中止"
- retry：mock runner 抛 `TimeoutError` 验证重试 3 次
- 显式失败：team_role 缺 system_prompt 验证返回失败结果 + 发射 warning SSE
