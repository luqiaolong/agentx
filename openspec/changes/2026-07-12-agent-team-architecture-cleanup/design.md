# Design: AgentTeam 架构清理（Phase 2 — 清理技术债）

## Context

### 历史背景

AgentX Team 路径自 2026-07-06 引入（[archive/2026-07-06-agent-team](../archive/2026-07-06-agent-team/proposal.md)），
经过 2026-07-08 deepagents 迁移、2026-07-09 write_todos 统一、Phase 1 稳定性硬化，已形成
「planner 拆任务 → dispatch fan-out → 6 类节点并行 → aggregator 汇总」的 LangGraph StateGraph 拓扑。

但演进过程中沉淀了 5 类技术债：节点函数复制粘贴、模块级可变全局、死字段、孤儿 checkpoint、
静默异常吞。Phase 1 只补稳定性（超时/并发/终态），未触碰结构本身。Phase 2 专门清理这些债，
为 Phase 3（UX 增强）扫清地基。

### 当前数据流（待清理）

```
run_team_path
  ├─ _should_downgrade_to_single → 降级: token + team_done(无 team_init)  ← R6
  ├─ _plan_node → write_todos → _todos_to_team_tasks → clean_todos/tasks 双列表  ← A4
  ├─ _dispatch_node → Send(agent_name, state)  6 个目标节点
  │     ├─ _deep_node    (L353) ┐
  │     ├─ _code_node    (L401) │
  │     ├─ _builtin_node (L443) ├─ 90% 相同: abort/delegation/todo/runner/Cancelled  ← A1
  │     ├─ _team_role_node(L480)│
  │     ├─ _custom_node  (L518) ┘
  │     └─ _default_node  (L556) ─ 不发 delegation  ← R7
  │     每个写 _in_progress_tasks 全局  ← A2
  │     child_thread_id = {parent}-team-{agent}-{idx}  ← A5
  ├─ _run_subtask_stream
  │     ├─ _inherit_workspace: except Exception 吞  ← R3
  │     ├─ _route_event_for_node: except Exception pass  ← R2
  │     └─ astream_events(version="v2") + abort 仅事件间检查  ← D2/R4
  ├─ _aggregate_node
  │     ├─ 构造 Blackboard(findings, errors, meta)  ← A4 死字段
  │     ├─ _quality_gate (已删 identical 检查)  ← R5
  │     └─ _in_progress_tasks.pop  ← A2 泄漏点
  └─ values-mode: _in_progress_tasks 覆盖 overlay (L759-770)  ← A2
```

### 目标数据流（清理后）

```
run_team_path
  ├─ _should_downgrade_to_single → 降级: token + done (无 team_done)  ← R6 修正
  ├─ _plan_node → tasks 单一来源 → tasks_to_display_todos(tasks) 派生  ← A4
  ├─ _dispatch_node → Send("subtask", {task, config})  单一目标  ← A1
  │     └─ _run_subtask_node(state, config)
  │           abort → delegation → todo in_progress → runner → update → Cancelled
  │           child_thread_id = {parent}-team-{uuid4()}  ← A5
  ├─ _run_subtask_stream
  │     ├─ _inherit_workspace: except (SandboxError, ValueError) → 失败结果  ← R3
  │     ├─ _route_event_for_node: except (JSONDecodeError, KeyError) → warn  ← R2
  │     └─ astream_events(version=v?) + wait_for/abort 竞速  ← D2/R4
  ├─ _aggregate_node
  │     ├─ _run_aggregator(findings, errors, ...) 直接入参  ← A4 内联
  │     ├─ _quality_gate (恢复 identical 检查)  ← R5
  │     └─ (无全局 pop)
  └─ values-mode: _merge_todos 粘性 in_progress (无 overlay)  ← A2
```

## Goals / Non-Goals

**Goals:**

- G1: Team 子任务节点从 6 个函数收敛为 1 个 `_run_subtask_node` + 派发表（A1/R7）
- G2: 删除 `_in_progress_tasks` 模块级全局，`in_progress` 由 `_merge_todos` reducer 粘性持有（A2）
- G3: 删除全部死字段（`subtask_results` / `team_blackboard` / `Blackboard.meta`）与 `Blackboard` dataclass（A4）
- G4: /reset 清理孤儿 child checkpoint + child_thread_id UUID 化（A5）
- G5: 收窄 9+ 处静默异常为窄异常 + 可观测日志（R1/R2/R3）
- G6: abort 可中断 LLM 长调用（R4）
- G7: 恢复 identical-findings 质量门 + 修正降级路径 `team_done`（R5/R6）
- G8: `_looks_like_dangerous_task` 词边界正则 + 共享关键词模式（D3）

**Non-Goals:**

- 不新增 SSE 事件类型（`team_node_done`/`team_node_error` 属 Phase 3）
- 不动前端渲染层（O(n²)→O(n)、进度条、单子代理取消属 Phase 3）
- 不引入灰度开关 / 版本兼容层 / `@Deprecated`（开发阶段推倒重来）
- 不改 planner token 流 / re-plan diff 可视化（Phase 3）
- 不动 profile_auto_extract fire-and-forget 语义
- 不改 write_file/edit_file 的 interrupt_before=["tools"] 审批流

## Decisions

### D1: 子任务节点统一为 `_run_subtask_node` + 派发表

**决策**：删除 `_deep_node` / `_code_node` / `_builtin_node` / `_team_role_node` / `_custom_node` /
`_default_node` 共 6 个函数，替换为单一 `_run_subtask_node(state, config)` + `_NODE_DISPATCH`
派发表。`_dispatch_node` 统一 `Send("subtask", {"task": t, "config": _NODE_DISPATCH.get(t.agent, _DEFAULT_CONFIG)})`。

**理由**：

- 5 个节点函数 ~200 行代码 ~90% 相同（abort 检查 / `_emit_delegation` /
  `_emit_todo_in_progress` / `_run_subtask_stream` / `try/except CancelledError`），
  每次行为修复要改 5 处，漂移风险高（`_default_node` 已漂移——不发 delegation）
- 差异点仅 3 处：runner 选择（`_get_runner("deep"|"code"|"custom")` vs `_run_team_role_subtask`）、
  runner_args 构造、是否调用 `_inherit_workspace`（`deep`/`code` 调，`builtin`/`team_role`/`custom` 不调）
- 这些差异完全可用 `SubtaskConfig` 数据结构描述，无需函数级分叉

**替代方案**：

- A1-alt1: 保留 6 函数但抽公共 `_run_subtask_common(state, *, runner, runner_args, inherit_ws)`。
  否决：仍需 6 个薄包装，新增 agent 类型要加新函数；派发表扩展性更好
- A1-alt2: 用 `functools.partial` 绑定差异。
  否决：partial 难以表达「是否 inherit_workspace」布尔标志，且 runner_args 构造依赖 state，
  partial 在定义期无法拿到 state；`SubtaskConfig` + 工厂函数更清晰
- A1-alt3: 把节点逻辑塞进 `_run_subtask_stream`。
  否决：scheduler 是「跑 runner」层，节点是「编排」层（emit delegation/todo/Cancelled 处理），
  职责混淆；保持分层

### D2: `in_progress` 由 reducer 粘性持有，删除模块级全局

**决策**：扩展 `_merge_todos`（blackboard.py:80-107）实现粘性语义——当 todo 在 left 或 right
任一侧为 `in_progress` 时，合并后保持 `in_progress`，不降级为 `pending`。删除
`_in_progress_tasks`（orchestrator.py:91）及其所有读写点（L342/572/723/763）。

**理由**：

- `_in_progress_tasks` 存在的唯一原因是 LangGraph 默认 reducer 无法表达「transient」状态——
  values-mode 推送的 state.todos 经 reducer 归并后，`in_progress` 会被「子任务还没返回 completed」
  的 `pending` 覆盖回退
- 粘性 reducer 直接在数据层解决：`in_progress` 一旦设置，只能由显式 `completed`/`error` 覆盖
- 全局 dict 在 graph 崩溃时泄漏（`_aggregate_node` 未执行则残留），且违反「无模块级可变状态」原则

**替代方案**：

- D2-alt1: 自定义 LangGraph reducer 用 `Annotated[dict, _in_progress_reducer]` 单独维护。
  否决：增加一个 state 字段，仍需手动清理；粘性语义内联进现有 `_merge_todos` 更内聚
- D2-alt2: 子任务节点返回 `{"todos": [{"_index": idx, "status": "in_progress"}]}` 后，
  依赖 values-mode diff 不覆盖。否决：reducer 是 LangGraph 并行归并的真相源，
  values-mode 只是读取视图，不解决归并竞态
- D2-alt3: 保留全局但加 `finally` 清理。否决：治标不治本，崩溃路径仍可能跳过 finally

### D3: `Blackboard` dataclass 内联进 `_run_aggregator`，删除死字段

**决策**：

- 删除 `TeamState.subtask_results`（写而永不读）
- 删除 `RouterState.team_blackboard`（永不填充）
- 删除 `Blackboard.meta`（永不填充）
- 删除 `Blackboard` dataclass，`_run_aggregator` 改为直接接收 `findings: dict[str,str]` + `errors: dict[str,str]`
- `_serialize_blackboard` 若简化 aggregator prompt 构造则保留为薄 helper，否则删除
- `clean_todos`/`tasks` 双列表合并为单一 `tasks`，新增纯函数 `tasks_to_display_todos(tasks)` 派生展示用 todos

**理由**：

- `subtask_results` 由 `_make_subtask_state_update`（orchestrator.py:265,276）写入，
  但全仓库 grep 无任何读取点——纯死字段，删除无行为影响
- `RouterState.team_blackboard` 在 `run_coding_team` 中从未赋值，是早期设计的残留
- `Blackboard.meta` 同理从未写入；`Blackboard` dataclass 仅在 `_aggregate_node` 构造后传给
  `_run_aggregator`，是「为向后兼容保留」的过渡层（注释自承认）
- `clean_todos`/`tasks` 双列表在 `_plan_node` 同时产出，信息冗余，派生函数更清晰

**替代方案**：

- D3-alt1: 保留 `Blackboard` 但删除 `meta`。
  否决：`Blackboard` 只剩 `findings`/`errors` 两字段，dataclass 包装无价值，直接传 dict
- D3-alt2: 保留 `subtask_results` 备用。
  否决：违反「不预先抽象」原则，无消费方即删
- D3-alt3: `tasks_to_display_todos` 放进 `TeamPlanTask` 方法。
  否决：纯函数更易测试，dataclass 方法引入隐式 this 依赖

### D4: /reset 清理孤儿 child checkpoint + child_thread_id UUID 化

**决策**：

- child_thread_id 从 `{parent}-team-{agent}-{idx}` 改为 `{parent}-team-{uuid4()}`，retry 不再撞 stale
- /reset 端点（chat.py）枚举 `{thread_id}-team-*` 前缀的 child thread，调用 `checkpointer.adelete_thread(child_id)` 批量清理
- 新增 `_enumerate_child_thread_ids(parent_thread_id)`：优先用 `AsyncSqliteSaver.alist(configs)`
  过滤前缀；若无 `alist`，fallback 直接 SQL `DELETE FROM checkpoints WHERE thread_id LIKE ?`

**理由**：

- 当前 child_thread_id 含 `{idx}`，retry 时新子任务可能复用旧 idx → 撞 stale checkpoint
- UUID 化让每次 team 任务生成全新 child_id，根本消除碰撞
- 但旧 `{parent}-team-{agent}-{idx}` 格式的孤儿 checkpoint 仍累积在 `data/agentx.db`，需 /reset 主动清理
- 双保险：UUID 防未来碰撞 + 清理防历史累积

**替代方案**：

- D4-alt1: 仅 UUID 化不清理。
  否决：`data/agentx.db` 持续膨胀，长期占用磁盘；且旧 stale state 在极端情况下可能被
  `alist` 枚举时误读
- D4-alt2: 仅清理不 UUID 化。
  否决：retry 仍可能在新清理前撞上未清理的同 idx child
- D4-alt3: 在 `_aggregate_node` 末尾清理本任务的 child checkpoint。
  否决：崩溃路径跳过 aggregate 则不清理；/reset 是用户显式「重置」语义，更合适的清理时机
- D4-alt4: 给 `AsyncSqliteSaver` 加 `adelete_thread_by_prefix`。
  否决：上游 API 改动成本高；`alist` + 循环 `adelete_thread` 已够用

### D5: 异常收窄策略（R1/R2/R3）

**决策**：

- R1：`approval_runner.py` 9 处 `except Exception: pass` →
  `except <LangGraph 中断专用异常>: pass`；顶层补
  `except Exception as exc: logger.warning(f"unexpected resume exception: {exc}")`。
  具体异常类型通过以下步骤确定：(1) T6.1 在虚拟环境运行
  `python -c "import langgraph.errors; print([n for n in dir(langgraph.errors) if 'Interrupt' in n])"`
  列出候选类型；(2) 若存在 `langgraph.errors.InterruptException` 或 `langgraph.types.Interrupt`，使用之；
  (3) 若均不存在，fallback 为 `except Exception as exc: logger.warning(f"unexpected resume exception: {exc!r}")`
  （保持宽泛但可观测，禁止 `pass`）。tasks.md T6.1 验证此步骤。
- R2：`_route_event_for_node` 的 2 处 `except Exception: pass` →
  `except (json.JSONDecodeError, KeyError) as exc: logger.warning(f"dropped malformed event: {event_type}: {exc}")`，
  保留 drop 行为
- R3：`_inherit_workspace` 的 `except Exception` → `except (SandboxError, ValueError)`；
  授权失败返回 `TeamSubtaskResult(success=False, payload=f"workspace authorization failed: {exc}")`，
  子任务以失败结果收尾而非继续未授权执行

**理由**：

- 静默吞异常是「让代码跑下去」的最快方式，但牺牲可观测性——resume 期间的非中断异常
  （如网络错误、序列化错误）被吞后循环空转，用户只看到「卡住」无任何线索
- LangGraph 的 interrupt resume 在「无中断待恢复」时会抛特定异常，这是合法的「正常退出」，
  应窄捕获；其他异常应冒泡或至少 log
- `_inherit_workspace` 授权失败仍继续 → 子代理首次写文件触发 `directory_extension` 审批，
  用户审批后仍可能失败（workspace 未真正授权），形成死锁；应直接失败结果

**替代方案**：

- D5-alt1: 全部改为 `except Exception as exc: logger.warning(...)` 不窄化。
  否决：保留了「吞所有异常」语义，只是加了日志；不解决「resume 死循环」根因
- D5-alt2: R3 授权失败时 raise 让子任务节点捕获 CancelledError。
  否决：授权失败不是取消，语义混淆；返回失败结果更准确
- D5-alt3: R1 用 `langgraph.types.interrupt` 返回值判断而非异常。
  否决：当前代码已用 try/except 模式，改返回值判断改动面大；先窄化异常

### D6: abort 可中断 LLM 长调用（R4）

**决策**：在 `_run_subtask_stream` 的 `async for event in agent.astream_events(...)` 循环中，
将 `agent.astream_events(...)` 转为 `asyncio.wait_for(runner.__anext__(), timeout=5)` 与
`abort_event.wait()` 竞速：

```python
done, pending = await asyncio.wait(
    {asyncio.create_task(runner.__anext__()), asyncio.create_task(abort_event.wait())},
    return_when=asyncio.FIRST_COMPLETED,
)
if abort_task in done and abort_event.is_set():
    runner_task.cancel()
    return _done(False, "用户中止")
# 否则取 runner_task 结果继续
```

**理由**：

- 当前 abort 仅在 `async for` 事件迭代间检查（scheduler.py:312），LLM 单次长调用（如 60s 流式）
  期间无法响应中止
- `asyncio.wait` 竞速让 abort_event 一旦 set 即在 5s 内（runner 无新事件时）触发返回
- runner 无新事件但 abort 未触发时，wait_for 超时后继续等下一轮（runner 仍在跑）

**替代方案**：

- D6-alt1: 信任 LangGraph 外层 `asyncio.create_task` 取消。
  否决：子任务节点 catch `CancelledError`（H4 fix）返回部分状态，abort 生效但不干净；
  竞速方案让 abort 主动检查，行为更可控
- D6-alt2: 用 `asyncio.wait_for(runner.__anext__(), timeout=0.5)` 高频轮询。
  否决：timeout 太短导致正常 LLM 流式（每 token ~50ms）频繁超时重试，性能损耗；
  与 abort_event 竞速更优雅
- D6-alt3: 给 LLM 调用设 `request_timeout`。
  否决：不解决「abort 信号传递」问题，只解决「网络卡死」；两者正交

### D7: `_looks_like_dangerous_task` 词边界 + 共享关键词模式（D3）

**决策**：

- [planner.py:272-276](../../../backend/app/team/planner.py#L272-L276) 子串匹配 → 编译正则
- ASCII 关键词用 `\b` 词边界（`"write"` 不命中 `"read the writeup"`）
- CJK 关键词用子串（CJK 无词边界概念）
- 抽取到 [utils/text.py](../../../backend/app/utils/text.py) 共享 `compile_keyword_patterns(keywords: list[str])`，
  aggregator 的 `_KEYWORD_PATTERNS`（aggregator.py:185-189）与 planner 共用

**理由**：

- aggregator 已正确用词边界（aggregator.py:185-189），planner 仍用子串——两处逻辑分叉
- 子串误命中导致安全降级失真：`"read the writeup"` 被判危险 → 强制改 `deep` agent，
  本应 `rag` 的检索任务变成写文件任务
- 共享 helper 消除分叉，未来新增关键词一处改即可

**替代方案**：

- D7-alt1: planner 直接复制 aggregator 的正则模式。
  否决：两处维护，再次分叉
- D7-alt2: 全部用子串但加白名单。
  否决：白名单维护成本高，词边界是更通用的解法
- D7-alt3: 用 LLM 判断危险任务。
  否决：planner 已有一次 LLM 调用，再加一次延迟翻倍；启发式足够

## Detailed Design

### D1 详细：`_run_subtask_node` + `SubtaskConfig` + 派发表

```python
# orchestrator.py

@dataclass(frozen=True)
class SubtaskConfig:
    """子任务节点配置：描述 agent 类型、runner、runner_args 工厂、workspace 继承标志。"""
    agent_kind: str            # "deep" | "code" | "builtin" | "team_role" | "custom"
    inherit_workspace: bool    # 是否调用 _inherit_workspace（deep/code 调）
    runner_factory: Callable[..., Any]  # 从 state 取 runner 的工厂


class TeamRoleRunnerAdapter:
    """将 _run_team_role_subtask 的 distinct signature 适配为统一 runner_factory 返回的 callable。

    _run_team_role_subtask 原始签名为 (state, task, child_id, parent_thread_id, writer, abort_event)，
    经 adapter 包装后由 _run_subtask_stream 以统一 (runner, runner_args, runner_kwargs) 签名调用。
    _build_runner_args 对 team_role 类构造的 args/kwargs 与 adapter __call__ 参数对齐。
    """

    def __init__(self, agent_name: str):
        self.agent_name = agent_name

    async def __call__(self, *args, **kwargs):
        return await _run_team_role_subtask(agent_name=self.agent_name, *args, **kwargs)


_NODE_DISPATCH: dict[str, SubtaskConfig] = {
    "deep":  SubtaskConfig("deep",  inherit_workspace=True,  runner_factory=lambda s: _get_runner("deep",  s.get("subtask_runners"))),
    "code":  SubtaskConfig("code",  inherit_workspace=True,  runner_factory=lambda s: _get_runner("code",  s.get("subtask_runners"))),
    "rag":   SubtaskConfig("builtin", inherit_workspace=False, runner_factory=lambda s: _get_runner("rag",   s.get("subtask_runners"))),
    "web":   SubtaskConfig("builtin", inherit_workspace=False, runner_factory=lambda s: _get_runner("web",   s.get("subtask_runners"))),
    "custom":SubtaskConfig("custom", inherit_workspace=False, runner_factory=lambda s: _get_runner("custom", s.get("subtask_runners"))),
    # team_role 类 agent 显式派发到 agent_kind="team_role" 分支，由 _run_team_role_subtask 执行
    "frontend_dev":    SubtaskConfig("team_role", inherit_workspace=False, runner_factory=lambda s: TeamRoleRunnerAdapter("frontend_dev")),
    "backend_dev":     SubtaskConfig("team_role", inherit_workspace=False, runner_factory=lambda s: TeamRoleRunnerAdapter("backend_dev")),
    "tester":          SubtaskConfig("team_role", inherit_workspace=False, runner_factory=lambda s: TeamRoleRunnerAdapter("tester")),
    "architect":       SubtaskConfig("team_role", inherit_workspace=False, runner_factory=lambda s: TeamRoleRunnerAdapter("architect")),
    "devops":          SubtaskConfig("team_role", inherit_workspace=False, runner_factory=lambda s: TeamRoleRunnerAdapter("devops")),
    "ui_designer":     SubtaskConfig("team_role", inherit_workspace=False, runner_factory=lambda s: TeamRoleRunnerAdapter("ui_designer")),
    "product_manager": SubtaskConfig("team_role", inherit_workspace=False, runner_factory=lambda s: TeamRoleRunnerAdapter("product_manager")),
}

_DEFAULT_CONFIG = SubtaskConfig("default", inherit_workspace=False, runner_factory=None)
# team_role 类 agent 经 _NODE_DISPATCH 显式派发到 agent_kind="team_role" 分支，由 _run_team_role_subtask
# 执行；_DEFAULT_CONFIG 仅用于真正未知的 agent 名（plan 中不应出现）。


async def _run_subtask_node(state: SubtaskState) -> dict:
    """统一子任务节点：按 config 派发执行。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    config: SubtaskConfig = state["config"]  # 由 _dispatch_node 注入
    parent_thread_id = state["parent_thread_id"]
    task_index = state["task_index"]
    todos = state.get("todos", [])

    abort_event = await get_abort_event(parent_thread_id)
    if abort_event.is_set():
        return _make_subtask_state_update(
            TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止"), task_index)

    _emit_delegation(writer, task.agent, task.purpose)
    _emit_todo_in_progress(writer, todos, task_index, task.agent)  # 仅 emit，不写全局

    if config.agent_kind == "default":
        return _make_subtask_state_update(
            TeamSubtaskResult(agent=task.agent, success=False, payload=f"未知 agent: {task.agent}"),
            task_index)

    child_id = f"{parent_thread_id}-team-{uuid4()}"  # A5: UUID 化
    # Phase 1 稳定性保护：semaphore 限流 + subtask_timeout 均需在统一节点中保留
    semaphore: asyncio.Semaphore = state["team_semaphore"]  # Phase 1 注入，max_parallel 限流
    async with semaphore:  # 节点级限流：max_parallel 由 Phase 1 注入 state["team_semaphore"]
        if config.inherit_workspace:
            ws_result = await _inherit_workspace(child_id, state.get("workspace_path"))
            if isinstance(ws_result, TeamSubtaskResult):  # R3: 授权失败返回失败结果
                return _make_subtask_state_update(ws_result, task_index)

        runner_args, runner_kwargs = _build_runner_args(config, state, task, child_id, parent_thread_id)
        try:
            # _run_subtask_stream 内部（Phase 1）已包裹 asyncio.wait_for(timeout=subtask_timeout)
            result = await _run_subtask_stream(
                config.runner_factory(state) if config.runner_factory else None,
                runner_args=runner_args, runner_kwargs=runner_kwargs,
                agent_name=task.agent, abort_event=abort_event, writer=writer)
        except asyncio.CancelledError:  # noqa: B904 — H4: 返回部分状态以便聚合
            logger.info("team subtask cancelled", agent=task.agent, task_index=task_index)
            return _make_subtask_state_update(
                TeamSubtaskResult(agent=task.agent, success=False, payload="子任务已取消"), task_index)
    return _make_subtask_state_update(result, task_index)


def _dispatch_node(state: TeamState) -> list[Send]:
    return [
        Send("subtask", {
            **state,
            "task": {"agent": t.agent, "input": t.input, "purpose": t.purpose},
            "config": _NODE_DISPATCH.get(t.agent, _DEFAULT_CONFIG),
            "task_index": idx,
        })
        for idx, t in enumerate(state["plan"])
    ]
```

StateGraph 中 `add_node("subtask", _run_subtask_node)` 单一节点，`_dispatch_node` fan-out 到它。

> **Phase 1 稳定性保护保留说明**：Phase 1 在 `_run_subtask_stream` 内部已包裹
> `asyncio.wait_for(timeout=subtask_timeout)`；Phase 2 的 `_run_subtask_node` 调用
> `_run_subtask_stream` 时该保护自动生效。Semaphore 仍由 `_run_subtask_node` 持有，因为限流是节点级语义。

### D2 详细：`_merge_todos` 粘性 in_progress

```python
# blackboard.py

def _merge_todos(left: list[dict], right: list[dict]) -> list[dict]:
    """按 _index 合并，in_progress 粘性：任一侧 in_progress 即保持，不降级为 pending。

    不变量：in_progress 一旦设置，只能由显式 completed/error 覆盖。
    """
    result: list[dict] = [dict(t) for t in (left or [])]
    for item in (right or []):
        idx = item.get("_index")
        if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(result):
            cleaned = {k: v for k, v in item.items() if k != "_index"}
            result.append(cleaned)
            continue
        target = dict(result[idx])
        existing_status = target.get("status")
        incoming_status = item.get("status")
        # 粘性：任一侧 in_progress 且新状态是 pending（降级）→ 保持 in_progress
        if (existing_status == "in_progress" or incoming_status == "in_progress") \
           and incoming_status not in ("completed", "error"):
            target["status"] = "in_progress"
        elif incoming_status is not None:
            target["status"] = incoming_status
        for k, v in item.items():
            if k not in ("_index", "status"):
                target[k] = v
        result[idx] = target
    return result
```

`_emit_todo_in_progress` 改为只 emit SSE，不再写 `_in_progress_tasks`：

```python
def _emit_todo_in_progress(writer, todos, task_index, agent):
    """emit todo_update SSE (status=in_progress)，不写全局。reducer 已保证粘性。"""
    writer(make_todo_update_event([...]))  # 与 reducer 语义对齐
```

`run_team_path` 的 values-mode 分支删除 L759-770 overlay，直接 `yield make_todo_update_event(current_todos, ...)`。

### D3 详细：删除死字段 + `Blackboard` 内联

```python
# blackboard.py — 删除
# class Blackboard: ... (整个 dataclass 删除)
# TeamState.subtask_results 字段删除

# orchestrator.py _make_subtask_state_update — 删除 subtask_results 写入
def _make_subtask_state_update(result, task_index):
    key = f"{result.agent}-{task_index}"
    if result.success:
        return {"findings": {key: result.payload}, "todos": [...]}
    return {"errors": {key: result.payload}, "todos": [...]}

# aggregator.py _run_aggregator — 直接接收 dict
async def _run_aggregator(user_message, findings, errors, chat_model=None, abort_event=None):
    ...
```

`tasks_to_display_todos` 纯函数：

```python
# planner.py 或 utils
def tasks_to_display_todos(tasks: list[TeamPlanTask]) -> list[dict]:
    """从 TeamPlanTask 列表派生展示用 todos（content/status）。"""
    return [{"content": f"[agent:{t.agent}] {t.input}", "status": "pending"} for t in tasks]
```

### D4 详细：/reset 清理 + UUID 化

```python
# chat.py reset 端点
async def _reset_thread(thread_id, checkpointer):
    await checkpointer.adelete_thread(thread_id)
    # 清理孤儿 child checkpoint
    for child_id in await _enumerate_child_thread_ids(thread_id, checkpointer):
        await checkpointer.adelete_thread(child_id)

async def _enumerate_child_thread_ids(parent_thread_id, checkpointer) -> list[str]:
    """枚举 {parent}-team-* 前缀的 child thread。"""
    prefix = f"{parent_thread_id}-team-"
    if hasattr(checkpointer, "alist"):
        configs = await checkpointer.alist({})
        return [c["configurable"]["thread_id"] for c in configs
                if c["configurable"]["thread_id"].startswith(prefix)]
    # fallback: SQL（仅 AsyncSqliteSaver）
    rows = await checkpointer.conn.execute(
        "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE ?", (f"{prefix}%",))
    return [r[0] for r in await rows.fetchall()]
```

child_thread_id UUID 化见 D1 详细设计。

### D5 详细：异常收窄

```python
# approval_runner.py — 每处 except Exception: pass 改为：
try:
    ...
except _INTERRUPT_RESUME_EXCEPTION:  # langgraph 中断专用
    pass
except Exception as exc:  # 顶层兜底
    logger.warning(f"unexpected resume exception: {exc}")

# scheduler.py _route_event_for_node
except (json.JSONDecodeError, KeyError) as exc:
    logger.warning(f"dropped malformed event: {event_type}: {exc}")

# scheduler.py _inherit_workspace — 返回失败结果而非继续
try:
    await sandbox.authorize(...)
except (SandboxError, ValueError) as exc:
    logger.warning(...)
    return TeamSubtaskResult(agent="", success=False, payload=f"workspace authorization failed: {exc}")
```

### D6 详细：abort 竞速

```python
# scheduler.py _run_subtask_stream
runner = agent_obj.astream_events(inputs, version=_STREAM_VERSION, config=config)
runner_iter = runner.__aiter__()
while True:
    if abort_event.is_set():
        return _done(False, "用户中止")
    next_event_task = asyncio.create_task(runner_iter.__anext__())
    abort_task = asyncio.create_task(abort_event.wait())
    done, pending = await asyncio.wait(
        {next_event_task, abort_task}, return_when=asyncio.FIRST_COMPLETED, timeout=5.0)
    if abort_task in done and abort_event.is_set():
        next_event_task.cancel()
        return _done(False, "用户中止")
    if next_event_task in done:
        abort_task.cancel()
        event = next_event_task.result()  # 可能 raise StopAsyncIteration
        ...  # 处理 event
    else:
        # timeout，runner 仍在跑，abort 未触发，继续下一轮
        abort_task.cancel()
```

### D7 详细：共享关键词模式

```python
# utils/text.py
import re

def compile_keyword_patterns(keywords: list[str]) -> tuple[re.Pattern, ...]:
    """编译关键词模式：ASCII 用词边界，CJK 用子串。"""
    return tuple(
        re.compile(rf"\b{re.escape(kw)}\b") if all(ord(c) < 128 for c in kw)
        else re.compile(re.escape(kw))
        for kw in keywords
    )

def matches_any(text: str, patterns: tuple[re.Pattern, ...]) -> bool:
    lower = text.lower()
    return any(p.search(lower) for p in patterns)

# planner.py
from app.utils.text import compile_keyword_patterns, matches_any
_DANGEROUS_KEYWORDS = ["写入", "写文件", "write", "编辑", "修改", "edit", "执行命令", "shell", "运行脚本"]
_DANGEROUS_PATTERNS = compile_keyword_patterns(_DANGEROUS_KEYWORDS)

def _looks_like_dangerous_task(input_text: str) -> bool:
    return matches_any(input_text, _DANGEROUS_PATTERNS)

# aggregator.py — 同样用 compile_keyword_patterns
```

## Risk & Mitigation

| 风险 | 等级 | 缓解措施 |
|---|---|---|
| `_run_subtask_node` 统一节点替换 6 函数，遗漏某个 agent 类型的特殊参数构造 | 高 | `_NODE_DISPATCH` 派发表 + `_build_runner_args` 工厂集中所有差异；为每个 agent 类型写单测验证 runner_args 等价于原函数 |
| `_merge_todos` 粘性语义变更，`in_progress` 卡住不回退 | 高 | 粘性仅对 `pending` 降级生效，`completed`/`error` 显式覆盖仍生效；写测试覆盖「in_progress → completed」「in_progress → pending(保持)」「completed → pending(覆盖)」三场景 |
| `InterruptException` 类型在当前 LangChain 版本不存在或名称不同 | 中 | 实现前先 `grep` site-packages 确认类型名；若不存在，fallback 为 `except Exception as exc: logger.warning(...)` 保留可观测性 |
| `/reset` 枚举 child checkpoint 的 `alist` API 在 AsyncSqliteSaver 不可用 | 中 | fallback SQL `DELETE ... WHERE thread_id LIKE ?`；UUID 化独立生效不依赖清理逻辑 |
| `astream_events(version=v3)` 在当前 LangChain 版本未提供 | 低 | 先查 pyproject.toml 版本，v3 不可用则保留 v2 并文档化迁移路径（不阻塞 Phase 2） |
| 删除 `Blackboard` 后 `_serialize_blackboard` 调用点报错 | 低 | grep 所有 `_serialize_blackboard` 调用点逐一改；保留为薄 helper 接收 dict 而非 dataclass |
| `_inherit_workspace` 返回失败结果后，子任务以失败收尾但前端无 `delegation` 之外的信号 | 低 | `_run_subtask_node` 在 `_inherit_workspace` 之前已 emit `delegation`，前端 SubAgentGroup 容器已创建，失败结果经 aggregator 汇总展示 |
| abort 竞速引入 `asyncio.create_task` 开销 | 低 | 5s timeout + FIRST_COMPLETED，正常 LLM 流式每 token ~50ms，竞速仅在一次 wait 超时且 abort 未触发时重建 task，开销可忽略 |
| planner/aggregator 关键词模式合并后，新增关键词影响两处行为 | 低 | 共享 helper 后行为一致是收益而非风险；单测验证两处对同一关键词集的判定相同 |
| GitNexus 索引过期导致 impact analysis 不准 | 中 | 编辑前确认索引新鲜度（`gitnexus://repo/agentx/context`），过期则先 `npx gitnexus analyze` |

### 验证策略

- **单元测试**：每个 Decision 配套单测（`_merge_todos` 粘性、`_run_subtask_node` 派发、
  `tasks_to_display_todos`、`_enumerate_child_thread_ids`、异常收窄、abort 竞速、关键词模式）
- **回归测试**：现有 Team 路径集成测试全量通过（不新增集成测试，确保不破坏现有行为）
- **端到端冒烟**：/reset → team 任务 → 中止 → retry → /reset 验证清理生效
- **GitNexus impact analysis**：编辑 `_merge_todos` / `_run_subtask_node` / `_run_aggregator` 前
  跑 `gitnexus_impact`，HIGH/CRITICAL 风险需用户确认
- **ruff / typecheck**：`uv run ruff check backend/` + `uv run pytest tests/python/unit -m "not integration"` 通过
