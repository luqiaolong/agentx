# Design: AgentTeam DAG 依赖编排

## Context

### 历史背景

AgentX Team 路径自 2026-07-06 引入，经过 deepagents 迁移、write_todos 统一、Phase 1 稳定性硬化，
已形成「planner 拆任务 → dispatch fan-out → 6 类节点并行 → aggregator 汇总」的 LangGraph StateGraph 拓扑
（[orchestrator.py:631-650](../../../backend/app/team/orchestrator.py#L631-L650) `_build_team_graph`）。

但当前架构是「plan-once + parallel fan-out + aggregate-once」，**无法表达子任务之间的依赖关系**。
真实软件开发团队协作是 DAG（架构师 → 前后端并行 → 测试依赖开发完成），当前并行 fan-out 把这些依赖强行抹平，
导致「读→改」「检索→基于检索结果操作」等顺序敏感任务出错。

本 change 引入完整 DAG 依赖编排，**不重构现有节点函数**（Phase 2 负责），只在 dispatch 层引入分层执行 +
结果注入，兼容 Phase 2 落地前后的两种节点结构。

### 当前数据流（无依赖编排）

```
run_team_path
  ├─ _should_downgrade_to_single → 降级
  ├─ _plan_node → llm.ainvoke → _parse_todos_from_text → _todos_to_team_tasks
  │     └─ tasks: list[TeamPlanTask(agent, input, purpose)]  ← 无 deps 字段
  ├─ _dispatch_node → Send(node_name, state)  一次性 fan-out 全部任务
  │     ├─ _deep_node    ┐
  │     ├─ _code_node    │  全部并行，互不通信
  │     ├─ _builtin_node ├─  findings reducer 归并，但子任务执行时看不到其他 findings
  │     ├─ _team_role_node│
  │     ├─ _custom_node  ┘
  │     └─ _default_node → success=False  ← 无 fallback
  └─ _aggregate_node → _run_aggregator → team_done
```

### 目标数据流（DAG 依赖编排）

```
run_team_path
  ├─ _should_downgrade_to_single → 降级
  ├─ _plan_node → llm.ainvoke → _parse_todos_from_text(支持[after:]) → _todos_to_team_tasks
  │     └─ tasks: list[TeamPlanTask(agent, input, purpose, deps)]  ← 新增 deps 字段
  │     └─ _validate_dag(tasks) → topological_levels: list[list[int]]  ← Kahn 分层
  ├─ _dispatch_batch_node → Send(node_name, {task, injected_input})  只 fan-out 当前层
  │     ├─ _deep_node    ┐
  │     ├─ _code_node    │  层内并行
  │     ├─ _builtin_node ├─  injected_input 含前置 findings
  │     ├─ _team_role_node│  _default_node fallback 到 code  ← D4
  │     ├─ _custom_node  ┘
  │     └─ (子任务返回 findings/errors/todo_update)
  ├─ check_next_level（条件边）
  │     ├─ 还有未执行层 → _dispatch_batch_node（下一层）
  │     └─ 全部执行完 → _replan_check_node
  ├─ _replan_check_node（D6 迭代式 replan）
  │     ├─ LLM 判定需要追加 → 解析新任务 → 追加 plan/pending_levels → _dispatch_batch_node
  │     └─ LLM 判定无需追加 / replan_count >= max → _aggregate_node
  └─ _aggregate_node → _run_aggregator → team_done
```

## Design Decisions

### D1. DAG 数据结构：`TeamPlanTask.deps` + `TeamState.pending_levels`

#### `TeamPlanTask` 扩展

```python
@dataclass
class TeamPlanTask:
    agent: str
    input: str
    purpose: str
    deps: list[int] = field(default_factory=list)  # 新增：依赖的任务索引列表
```

`deps` 语义：`deps=[0, 1]` 表示本任务依赖 `plan[0]` 和 `plan[1]`，必须在它们完成后执行。
`deps=[]` 表示根任务，首批并行执行。

#### `TeamState` 扩展

```python
class TeamState(TypedDict, total=False):
    # ... 现有字段 ...
    pending_levels: list[list[int]]  # 新增：待执行的拓扑层级，每层一组任务索引
    completed_tasks: set[int]        # 新增：已完成的任务索引集合（用于 replan 时确定新任务的 after 引用）
    replan_count: int                # 新增：replan 次数计数（防无限循环）
```

`pending_levels` 由 `_validate_dag` 在 `_plan_node` 中计算并初始化。
`_dispatch_batch_node` 每次取 `pending_levels[0]` 执行，`check_next_level` 检查 `pending_levels` 是否非空。

#### 为什么不用 `dict[int, list[int]]` 邻接表？

考虑到 `TeamPlanTask.deps` 已是列表，且 `pending_levels` 是分层结果，
额外的邻接表会引入双重表示（Phase 2 的 A4「双重表示」反面清单已警示）。
`deps` 字段 + `pending_levels` 派生结构已足够，Kahn 算法在 `_validate_dag` 内部用临时邻接表即可。

### D2. `[after:]` 解析：正则扩展 + 容错

#### 正则设计

现有 `_AGENT_PREFIX_RE`（[planner.py:108-110](../../../backend/app/team/planner.py#L108-L110)）：
```python
_AGENT_PREFIX_RE = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])?\s*\[agent:\s*([a-zA-Z0-9_\-]+)\]\s*(.*)"
)
```

扩展为支持可选的 `[after:N1,N2]` 标注：
```python
_AGENT_PREFIX_RE = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])?\s*\[agent:\s*([a-zA-Z0-9_\-]+)\]"
    r"(?:\[after:\s*([\d,\s]+)\])?"  # 可选 [after:0,1]
    r"\s*(.*)"
)
```

匹配示例：
- `[agent:code] 读取 src/main.py` → agent="code", deps=None, input="读取 src/main.py"
- `[agent:deep][after:0] 修改 src/main.py` → agent="deep", deps="0", input="修改 src/main.py"
- `[agent:rag][after:0, 1] 检索文档` → agent="rag", deps="0, 1", input="检索文档"

#### 容错策略

| LLM 输出 | 解析结果 | 处理 |
|---|---|---|
| `[after:0,1]` | deps=[0,1] | 正常 |
| `[after:0, 1]` | deps=[0,1] | strip 空格 |
| `[after:#0,#1]` | deps=[0,1] | 去掉 `#` 前缀 |
| `[after:0;1]` | deps=[0,1] | 分号也作分隔符 |
| `[after:abc]` | deps=[] | 非数字丢弃，logger.warning |
| `[after:99]`（越界） | deps=[] | `_validate_dag` 过滤越界索引，logger.warning |
| 无 `[after:]` | deps=[] | 根任务 |

#### `_parse_todos_from_text` 返回值扩展

```python
def _parse_todos_from_text(text: str) -> list[dict]:
    # 返回: [{content, status, deps}, ...]
    # deps 为 int 列表，空列表表示无依赖
```

### D3. 拓扑排序 + 循环检测：Kahn 算法分层

#### 算法

```python
def _validate_dag(tasks: list[TeamPlanTask]) -> tuple[list[list[int]], list[int]]:
    """Kahn 算法分层 + 循环检测。

    Returns:
        (levels, dropped_edges): levels 是分层结果，dropped_edges 是因循环被丢弃的边
    """
    n = len(tasks)
    # 构建邻接表 + 入度表
    in_degree = [0] * n
    adj: list[list[int]] = [[] for _ in range(n)]
    dropped_edges: list[int] = []
    for i, task in enumerate(tasks):
        for dep in task.deps:
            if dep < 0 or dep >= n or dep == i:
                # 越界或自环，丢弃
                dropped_edges.append(i)
                continue
            adj[dep].append(i)
            in_degree[i] += 1

    # Kahn 分层
    levels: list[list[int]] = []
    remaining = set(range(n))
    while remaining:
        # 当前层：入度为 0 的节点
        current_level = [i for i in remaining if in_degree[i] == 0]
        if not current_level:
            # 循环：把剩余节点中入度最小的强加进当前层（打破循环）
            min_indeg = min(in_degree[i] for i in remaining)
            current_level = [i for i in remaining if in_degree[i] == min_indeg]
            for i in current_level:
                dropped_edges.append(i)
                logger.error("DAG cycle detected, breaking by force", task_index=i, deps=tasks[i].deps)
        levels.append(current_level)
        for i in current_level:
            remaining.discard(i)
            for j in adj[i]:
                in_degree[j] -= 1
    return levels, dropped_edges
```

#### 循环检测策略

**不抛异常**，而是**强制打破**：把循环中入度最小的节点强加进当前层，使其 deps 标记为 dropped。
理由：LLM 输出的依赖可能偶尔成环（如 `[after:1]` + `[after:0]` 互相依赖），
强制打破后仍能执行（依赖任务拿不到对应 findings，但不会卡死）。

#### 边界情况

| 情况 | 处理 |
|---|---|
| 所有任务无 deps | levels = [[0,1,2,...]] 单层，等价于现有并行 fan-out |
| 单任务 | levels = [[0]] |
| 线性链 A→B→C | levels = [[0],[1],[2]] 三层串行 |
| 菱形 A→B, A→C, B→D, C→D | levels = [[0],[1,2],[3]] |
| 自环 `[after:0]` 引用自己 | dep 丢弃，logger.warning |
| 越界 `[after:99]` | dep 丢弃，logger.warning |

### D4. 分批 fan-out：StateGraph 拓扑调整（复用 Phase 2 单一 `subtask` 节点）

**DEPENDS_ON Phase 2 A1**：本节在 Phase 2 统一的 `_run_subtask_node`（节点名 `subtask`）之上叠加 DAG 编排节点，
不重新引入 6 个分类节点（`subtask_deep` / `subtask_code` / 等）。

#### 新 StateGraph 拓扑

```python
def _build_team_graph() -> Any:
    graph: Any = StateGraph(TeamState)
    # 复用 Phase 2 的节点
    graph.add_node("plan", _plan_node)
    graph.add_node("subtask", _run_subtask_node)  # Phase 2 单一节点
    graph.add_node("aggregate", _aggregate_node)  # Phase 2 保留

    # 本 change 新增的 DAG 编排节点
    graph.add_node("dispatch_batch", _dispatch_batch_node)
    graph.add_node("check_next_level", _check_next_level_node)
    graph.add_node("replan_check", _replan_check_node)

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "dispatch_batch")
    # dispatch_batch 返回 list[Send] → 单一 subtask 节点
    graph.add_conditional_edges("dispatch_batch", _dispatch_batch_router)
    # subtask → check_next_level
    graph.add_edge("subtask", "check_next_level")
    # check_next_level 条件边：还有层 → dispatch_batch；无层 → replan_check
    graph.add_conditional_edges("check_next_level", _route_after_level)
    # replan_check 条件边：有新任务 → dispatch_batch；无 → aggregate
    graph.add_conditional_edges("replan_check", _route_after_replan)
    graph.add_edge("aggregate", END)
    return graph.compile()
```

#### `_dispatch_batch_node` 实现

```python
def _dispatch_batch_node(state: TeamState) -> list[Send]:
    """派发当前层的所有子任务（层内并行），复用 Phase 2 单一 subtask 节点。"""
    pending_levels = state.get("pending_levels", [])
    if not pending_levels:
        return [Send("aggregate", {})]  # 无任务，直接聚合

    current_level = pending_levels[0]
    remaining_levels = pending_levels[1:]

    plan = state.get("plan", [])
    findings = state.get("findings", {})
    todos = state.get("todos", [])
    thread_id = state.get("thread_id", "")
    settings = get_settings()

    sends: list[Send] = []
    for idx in current_level:
        task = plan[idx]
        # D3: 注入依赖上下文
        injected_input = _inject_dependency_context(task, findings)
        # 复用 Phase 2 的 _NODE_DISPATCH 解析 config
        config = _NODE_DISPATCH.get(task.agent, _DEFAULT_FALLBACK_CONFIG)
        sends.append(Send("subtask", {
            "task": {
                "agent": task.agent,
                "input": injected_input,
                "purpose": task.purpose,
                "deps": task.deps,
            },
            "task_index": idx,
            "config": config,  # Phase 2 的 SubtaskConfig
            "parent_thread_id": thread_id,
            "todos": todos,
            "history": state.get("history"),
            "remaining_levels": remaining_levels,  # 让 subtask 节点返回时更新 pending_levels
            # ... 其他透传字段 ...
        }))

    return sends
```

**关键问题**：`list[Send]` 返回值不更新 state。需要在子任务节点返回时更新 `pending_levels`。
方案：子任务节点返回 `{"pending_levels": remaining_levels}` 通过 reducer 覆盖。
但多个并行子任务同时返回会冲突。

**解决方案**：`pending_levels` 用专用 reducer `_merge_pending_levels`：
- 第一个返回的子任务设置 `pending_levels = remaining_levels`
- 后续子任务返回 `pending_levels = []`（空列表），reducer 忽略空列表
- `check_next_level` 节点读取 `pending_levels`，非空则 `Send("dispatch_batch", {})`

```python
def _merge_pending_levels(left: list[list[int]], right: list[list[int]]) -> list[list[int]]:
    """reducer：非空 right 覆盖 left，空 right 忽略。"""
    return right if right else (left or [])
```

#### `_check_next_level_node` 实现

```python
async def _check_next_level_node(state: TeamState) -> dict:
    """检查是否还有未执行的层。"""
    pending_levels = state.get("pending_levels", [])
    if not pending_levels:
        return {}  # 无更多层，走 replan_check
    return {}  # 有更多层，走 dispatch_batch（由条件边路由）

def _route_after_level(state: TeamState) -> str:
    """条件边路由：有层 → dispatch_batch，无层 → replan_check。"""
    pending_levels = state.get("pending_levels", [])
    if pending_levels:
        return "dispatch_batch"
    return "replan_check"
```

### D5. 子任务间结果传递：`_inject_dependency_context`

```python
def _inject_dependency_context(task: TeamPlanTask, findings: dict[str, str]) -> str:
    """把依赖任务的 findings 注入到 task.input 头部。

    Args:
        task: 当前任务（含 deps 字段）
        findings: 已完成任务的 findings dict，key 格式 "{agent}-{task_index}"

    Returns:
        注入依赖上下文后的 input 字符串
    """
    if not task.deps:
        return task.input  # 无依赖，原样返回

    settings = get_settings()
    max_chars = settings.agent_team_result_max_chars
    sections: list[str] = ["[依赖任务结果]"]
    for dep_idx in task.deps:
        # 查找 dep_idx 对应的 finding（key 格式 "{agent}-{idx}"）
        # 遍历 findings 找到 key 后缀为 "-{dep_idx}" 的项
        dep_key = None
        for k in findings:
            if k.endswith(f"-{dep_idx}"):
                dep_key = k
                break
        if not dep_key:
            sections.append(f"--- #{dep_idx} (未完成或失败) ---")
            continue
        dep_content = findings[dep_key]
        if len(dep_content) > max_chars:
            dep_content = dep_content[:max_chars] + "\n[结果已截断]"
        sections.append(f"--- #{dep_idx} [{dep_key}] ---\n{dep_content}")

    sections.append(f"[当前任务]\n{task.input}")
    return "\n\n".join(sections)
```

#### 截断策略

每个依赖 finding 截断到 `agent_team_result_max_chars`（默认 2000），避免多依赖拼接导致 input 过长。
若 3 个依赖各 2000 字符，注入部分 6000 字符 + 原 input，对现代 LLM context window 可接受。

### D6. 未知 agent fallback 到 code（扩展 Phase 2 `_DEFAULT_CONFIG`）

**DEPENDS_ON Phase 2 A1**：本项在 Phase 2 统一的 `_run_subtask_node` + `_DEFAULT_CONFIG` 之上叠加 fallback 语义。

#### Phase 2 的 `_DEFAULT_CONFIG` 原行为

Phase 2 [architecture-cleanup/design.md D1](../2026-07-12-agent-team-architecture-cleanup/design.md) 设计：
```python
_DEFAULT_CONFIG = SubtaskConfig(
    agent_type="default",
    runner=None,  # 无 runner
    runner_args_factory=lambda task, ctx: (),
    workspace_inherit=False,
    emit_delegation=True,  # 发 delegation 后返回失败
)
```
未知 agent 经 `_DEFAULT_CONFIG` 派发 → emit delegation → 返回 `success=False`。

#### 本 change 的扩展

修改 `_DEFAULT_CONFIG`（或在 `_NODE_DISPATCH` 中为 default key 注册新配置）：
```python
_DEFAULT_FALLBACK_CONFIG = SubtaskConfig(
    agent_type="default",
    runner=_get_runner("code"),  # fallback 到 code runner
    runner_args_factory=lambda task, ctx: (task.input, ctx.child_thread_id),
    runner_kwargs_factory=lambda ctx: {
        "profile_prompt": ctx.profile_prompt,
        "permission_mode": ctx.permission_mode,
        "workspace_path": ctx.workspace_path,
        "parent_thread_id": ctx.parent_thread_id,
        "chat_model": ctx.chat_model,
    },
    workspace_inherit=True,  # code runner 需要 workspace
    emit_delegation=True,
    pre_run_hook=_log_unknown_agent_fallback,  # logger.warning 记录
)
```

`_run_subtask_node` 中 `agent_name=task.agent` 保留原始名（前端展示真实意图），但实际 runner 走 code。
无需新增节点函数，完全复用 Phase 2 的 `_run_subtask_node` + `_NODE_DISPATCH` 派发机制。

### D7. team_role_node 显式失败

#### `_run_team_role_subtask` 改造

```python
async def _run_team_role_subtask(task, ...):
    cfg = get_settings().team_subagents.get(task.agent)

    if not cfg or not cfg.system_prompt:
        # D5: 显式失败，不静默降级
        logger.error(
            "team_role missing system_prompt, failing explicitly",
            agent=task.agent,
            has_cfg=cfg is not None,
        )
        return TeamSubtaskResult(
            agent=task.agent,
            success=False,
            payload=f"团队角色 {task.agent} 配置缺失 system_prompt",
        )

    # 有专属配置：build_custom_agent + astream_events v2（原逻辑）
    ...
```

### D8. 迭代式 replan：`_replan_check_node`

#### prompt 设计

```python
_REPLAN_SYSTEM_PROMPT = """你是任务拆解专家。以下是已完成的子任务结果：

{completed_findings}

用户原始请求：{user_message}

请判断是否需要追加新任务来完善最终回答。
- 若需要追加，输出新的任务行，格式：[agent:类型][after:N1,N2] 任务描述
  - after 引用已完成任务的索引（上方已列出）
  - 新任务必须依赖至少一个已完成任务（不能是无依赖的根任务）
- 若无需追加，输出：NO_NEW_TASKS
"""
```

#### `_replan_check_node` 实现

```python
async def _replan_check_node(state: TeamState) -> dict:
    """检查是否需要 replan 追加新任务。"""
    writer = get_stream_writer()
    settings = get_settings()
    replan_count = state.get("replan_count", 0)
    max_replans = settings.agent_team_max_replans

    if replan_count >= max_replans:
        logger.info("team replan limit reached", replan_count=replan_count, max=max_replans)
        return {}

    findings = state.get("findings", {})
    plan = state.get("plan", [])
    message = state["message"]
    chat_model = state.get("chat_model")

    try:
        llm = chat_model if chat_model is not None else get_chat_model(
            temperature=settings.llm_temperature_orchestrator, streaming=False
        )
    except ValueError:
        return {}

    # 构造已完成任务摘要
    completed_findings = "\n\n".join(
        f"#{idx} [{plan[idx].agent}] {plan[idx].input[:100]}\n结果: {findings.get(f'{plan[idx].agent}-{idx}', 'N/A')[:500]}"
        for idx in range(len(plan))
        if f"{plan[idx].agent}-{idx}" in findings
    )

    response = await llm.ainvoke([
        {"role": "system", "content": _REPLAN_SYSTEM_PROMPT.format(
            completed_findings=completed_findings,
            user_message=message,
        )},
        {"role": "user", "content": "判断是否需要追加任务。"},
    ])

    text = response.content if hasattr(response, "content") else str(response)
    if isinstance(text, list):
        text = "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)

    if "NO_NEW_TASKS" in text:
        return {}

    # 解析新任务
    new_todos = _parse_todos_from_text(text)
    if not new_todos:
        return {}

    # 新任务的 after 索引基于当前 plan 长度
    current_plan_len = len(plan)
    new_tasks: list[TeamPlanTask] = []
    for todo in new_todos:
        content = todo.get("content", "")
        match = _AGENT_PREFIX_RE.match(content)
        if not match:
            continue
        agent = match.group(1).strip().lower()
        input_text = match.group(3).strip()  # group(3) 是 input
        # after 索引需要加上 current_plan_len 偏移（新任务引用已完成任务的绝对索引）
        deps = todo.get("deps", [])
        # deps 已是绝对索引（指向已完成任务），无需偏移
        # 安全改写 + 校验
        if agent != "deep" and _looks_like_dangerous_task(input_text):
            agent = "deep"
        ok, err = _validate_task(TeamPlanTask(agent=agent, input=input_text, purpose="", deps=deps), settings)
        if ok:
            new_tasks.append(TeamPlanTask(agent=agent, input=input_text, purpose="", deps=deps))

    if not new_tasks:
        return {}

    # 追加到 plan + 计算新的 pending_levels
    updated_plan = list(plan) + new_tasks
    new_levels, _ = _validate_dag(updated_plan)
    # 只保留未执行的层（已完成的任务不在 new_levels 中）
    # 简化：重新计算所有任务的分层，但只派发未完成的
    completed_tasks = set(state.get("completed_tasks", []))
    pending_levels = [
        [idx for idx in level if idx not in completed_tasks]
        for level in new_levels
        if any(idx not in completed_tasks for idx in level)
    ]

    writer(make_sse_event("team_replan", {
        "new_tasks": [{"agent": t.agent, "input": t.input, "deps": t.deps} for t in new_tasks],
        "replan_count": replan_count + 1,
    }))

    return {
        "plan": updated_plan,
        "pending_levels": pending_levels,
        "replan_count": replan_count + 1,
    }
```

#### `_route_after_replan` 条件边

```python
def _route_after_replan(state: TeamState) -> str:
    """条件边路由：有新任务 → dispatch_batch，无 → aggregate。"""
    pending_levels = state.get("pending_levels", [])
    if pending_levels:
        return "dispatch_batch"
    return "aggregate"
```

### D9. 配置校验：team_subagents system_prompt 非空

#### 启动时校验

```python
# config/subagents.py 新增
def validate_team_subagents(settings: Any) -> None:
    """启动时校验所有 enabled 的 team_subagents 必须有非空 system_prompt。"""
    team = getattr(settings, "team_subagents", None) or {}
    for key, cfg in team.items():
        if not getattr(cfg, "enabled", True):
            continue
        if not getattr(cfg, "system_prompt", "").strip():
            raise ValueError(
                f"团队角色 {key} 已启用但 system_prompt 为空。"
                f"请在配置中补充 system_prompt 或设置 enabled=false。"
            )
```

在 `app.main` lifespan 启动时调用 `validate_team_subagents(get_settings())`。

## Alternatives Considered

### A1. 为什么不用 LangGraph 的 `Channel` 机制做依赖编排？

LangGraph 的 `Send` API 是 fan-out，不支持「等某个子任务完成后再发另一个」。
要做依赖编排需要在节点间显式传递信号，而 `Send` 是无状态的。
本设计的 `pending_levels` + `check_next_level` 条件边回流是用 LangGraph 原语实现的 DAG 编排，
不引入新的编排框架。

### A2. 为什么不让每个子任务节点自己决定下一步？

考虑过「子任务完成后自己触发依赖任务」的 peer-to-peer 模式，
但这样会导致：
- 控制流分散在每个节点里，难以全局观测
- 并行子任务同时触发同一依赖任务时需要去重逻辑
- replan 难以实现（没有中心节点决策）

中心化的 `pending_levels` + `check_next_level` 更清晰、可调试。

### A3. 为什么 replan 不用独立的 LLM agent？

考虑过用 `create_deep_agent` + TodoListMiddleware 做 replan，
但 Phase 1 已证明 TodoListMiddleware 的 `write_todos` 99% 失败率。
直接 `llm.ainvoke` + 文本解析更可靠（与 `_plan_node` 一致）。

### A4. 为什么不做条件分支编排（if-else）？

条件分支（如「如果 #0 成功则跑 #1，否则跑 #2」）会显著增加复杂度，
且 LLM 输出条件表达式可靠性低。当前 DAG + replan 已能覆盖 90% 真实场景：
- 顺序依赖 → DAG `[after:]`
- 动态扩 plan → replan
- 失败处理 → 依赖任务读取 errors 决定是否继续（在 input 里写「若 #0 失败则跳过」）

条件分支留待未来需求验证后再引入。

## Risks

### R1. LLM 不输出 `[after:]` 标注

**风险**：Orchestrator LLM 可能不按 prompt 要求输出依赖标注，导致所有任务无 deps = 现有并行行为。

**缓解**：
- prompt 明确要求「涉及顺序的任务必须标注 [after:]」
- 兼容无 deps 场景（等价于现有并行 fan-out，不破坏）
- 评测集加入「顺序敏感任务」用例，验证 LLM 输出 `[after:]` 的命中率

### R2. replan 导致 LLM 调用翻倍

**风险**：max_replans=2 时最多 3 次 plan 调用，token 成本翻倍。

**缓解**：
- `max_replans` 可配置，默认 2
- replan prompt 只含已完成任务的摘要（500 字符截断），不是完整 findings
- replan 在所有子任务完成后才触发，不影响子任务并行度

### R3. StateGraph 条件边回流死锁

**风险**：`check_next_level → dispatch_batch` 回流可能引发 LangGraph 死锁或递归超限。

**缓解**：
- `pending_levels` 是有限列表，每层执行后弹出，不会无限回流
- 设置 `recursion_limit` 兜底（现有 chat.py 已有处理）
- 单元测试覆盖多层场景（5 层以上）

### R4. findings 注入导致 input 过长

**风险**：多依赖任务 input 拼接后超过 LLM context window。

**缓解**：
- 每个依赖 finding 截断到 `agent_team_result_max_chars`（默认 2000）
- 3 依赖 × 2000 = 6000 字符，对 128k context 的现代 LLM 可接受
- 若仍超限，未来可引入「findings 压缩」（summarizer 中间层）

## Testing Strategy

### 单元测试

- `test_team_dag.py`：
  - `_parse_todos_from_text` 解析 `[after:]` 各种格式
  - `_validate_dag` 拓扑排序正确性（线性/菱形/孤岛）
  - `_validate_dag` 循环检测与打破
  - `_inject_dependency_context` findings 注入与截断
  - `_dispatch_batch_node` 分层派发
  - `_check_next_level_node` 条件边路由
  - `_replan_check_node` 追加任务与 `max_replans` 限制

- `test_team_fallback.py`：
  - `_default_node` fallback 到 code runner
  - `_run_team_role_subtask` system_prompt 为空时显式失败
  - `validate_team_subagents` 启动校验

### 集成测试

- 顺序任务端到端：`[code] 读取 → [deep][after:0] 修改` 验证 deep 拿到 code 的 findings
- 并行任务回归：无 `[after:]` 的任务仍并行执行
- 混合任务：`[code] A → [code] B` + `[deep][after:0,1] C` 验证 C 等 A、B 都完成
- replan：第一轮 `[code] 读取` → replan 追加 `[deep][after:0] 修改`
- fallback：`[unknown_agent] 任务` 验证走 code runner
- 显式失败：team_role 缺 system_prompt 验证返回失败结果

## Open Questions

- Q1: `team_replan` SSE 事件是否需要前端特殊处理？当前设计是前端忽略即可（plan 已在 `team_init` 展示，
  replan 追加的任务通过 `delegation` / `todo_update` 自然展示）。Phase 3 的 UX 增强可补「重规划对比」可视化。
- Q2: replan 时新任务的 `after` 索引是绝对索引（指向已完成任务）还是相对索引（指向 replan 新增任务）？
  当前设计是绝对索引，与首次 plan 的 `[after:]` 语义一致。
