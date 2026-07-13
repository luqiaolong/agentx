# Design: AgentTeam v2 全量重构

## Context

### 历史背景

AgentX Team 路径自 2026-07-06 引入，经过 deepagents 迁移、Phase 1 稳定性硬化，
已形成「planner 拆任务 → dispatch fan-out → 6 类节点并行 → aggregator 汇总」的 LangGraph StateGraph 拓扑
（[orchestrator.py:631-650](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L631-L650) `_build_team_graph`）。

但当前架构存在 5 个核心缺陷（详见 [proposal.md](proposal.md#why)）：无依赖编排、无结果传递、
危险任务误判、无并发限流、无结构化输出。同时 `orchestrator.py` 单文件膨胀至约 780 行，
承载 8 节点 + 派发 + 图构建 + 入口流，维护性差。

本 change 一次性完成模块拆分 + 13 项改动，supersede 两个未落地的 prior change
（`agent-team-dag-orchestration` 与 `2026-07-12-agent-team-architecture-cleanup`），
直接推倒重来，删除旧代码。

### 当前数据流（无 DAG、无结果传递）

```
run_team_path
  ├─ _should_downgrade_to_single → 降级
  ├─ _plan_node → llm.ainvoke → _parse_todos_from_text(正则) → _todos_to_team_tasks
  │     └─ tasks: list[TeamPlanTask(agent, input, purpose)]  ← 无 deps 字段
  ├─ _dispatch_node → Send(node_name, state)  一次性 fan-out 全部任务
  │     ├─ _deep_node    ┐
  │     ├─ _code_node    │  全部并行，互不通信，无并发上限
  │     ├─ _builtin_node ├─  findings reducer 归并，但子任务执行时看不到其他 findings
  │     ├─ _team_role_node│  team_role 缺 system_prompt 时静默降级到 coding Expert
  │     ├─ _custom_node  │
  │     └─ _default_node → success=False  ← 无 fallback
  └─ _aggregate_node → _run_aggregator → team_done
```

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
  │           ├─ scheduler.acquire_semaphore  ← 并发限流
  │           ├─ scheduler.run_with_retry    ← 失败重试
  │           ├─ scheduler.register_for_abort  ← asyncio.Task cancel
  │           ├─ _NODE_DISPATCH[task.agent].runner(task, upstream_findings)
  │           │     ├─ code/deep/rag/web/builtin...
  │           │     ├─ team_role (显式失败，不静默降级)
  │           │     └─ default → fallback 到 code (D7)
  │           └─ 写入 findings[key]  ← key = "{agent}:{task_id}:{wave_index}"
  ├─ barrier_node (nodes.py)
  │     ├─ 还有未执行 wave → dispatch_node（下一 wave）
  │     └─ 全部执行完 → aggregate_node
  ├─ aggregate_node (nodes.py)
  │     ├─ aggregator.run_aggregator → 质量门
  │     └─ 质量门通过 → team_done
  └─ replan_node (nodes.py)  ← 质量门失败时
        ├─ replan_count < max_replan_attempts → 重新喂给 planner → plan_node
        └─ replan_count >= max → team_done (失败)
```

## Design Decisions

### D1. 模块拆分后的依赖图

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

依赖方向（自底向上）：

- **底层（无依赖）**：`state.py`、`blackboard.py`、`classifier.py`
- **中间层（依赖底层）**：`planner.py`（依赖 state）、`dispatcher.py`（依赖 state）、
  `aggregator.py`（依赖 state）、`scheduler.py`（依赖 state + blackboard）
- **上层（依赖中间层）**：`nodes.py`（依赖 planner/dispatcher/aggregator/scheduler/classifier）、
  `graph_builder.py`（依赖 nodes）
- **入口层**：`runner.py`（依赖 graph_builder）、`__init__.py`（导出 runner + 公共类型）

### D2. `TeamPlan` / `TeamTask` Pydantic schema 完整定义

```python
# state.py
from typing import Optional
from pydantic import BaseModel, Field


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

    tasks: list[TeamTask] = Field(description="子任务列表")
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

#### Fallback：正则解析

当 LLM provider 不支持 `with_structured_output` 时，回退到正则解析（保留旧 `_AGENT_PREFIX_RE`）：

```python
# planner.py
_AGENT_PREFIX_RE = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])?\s*\[agent:\s*([a-zA-Z0-9_\-]+)\]"
    r"(?:\[after:\s*([\w,\s]+)\])?"  # 可选 [after:t1,t2] (task id)
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

### D3. DAG 多波次派发的 StateGraph 拓扑

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
                              _route_after_aggregate
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

#### `_build_team_graph` 实现

```python
# graph_builder.py
from langgraph.graph import StateGraph, START, END


def _build_team_graph() -> Any:
    graph: Any = StateGraph(TeamState)

    graph.add_node("plan", plan_node)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("execute", execute_node)         # 统一节点，替代 6 个分类节点
    graph.add_node("barrier", barrier_node)
    graph.add_node("aggregate", aggregate_node)
    graph.add_node("replan", replan_node)

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "dispatch")
    # dispatch 返回 list[Send("execute", {...})]
    graph.add_conditional_edges("dispatch", _dispatch_router)
    graph.add_edge("execute", "barrier")
    # barrier 条件边：有 wave → dispatch；无 → aggregate
    graph.add_conditional_edges("barrier", _route_after_barrier)
    # aggregate 条件边：质量门失败 → replan；通过 → END
    graph.add_conditional_edges("aggregate", _route_after_aggregate)
    # replan 条件边：replan_count < max → plan；否则 → END (失败)
    graph.add_conditional_edges("replan", _route_after_replan)
    return graph.compile()
```

### D4. `SubtaskState` 扩展字段

```python
# state.py (TypedDict + Pydantic 双重定义)
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
    warnings: list[str]                      # 新增：warning channel
    completed_task_ids: set[str]             # 新增：已完成的 task id 集合
    replan_count: int                        # 新增：replan 次数
    thread_id: str
    chat_model: Any
    # ... 其他透传字段 ...
```

### D5. `_resolve_waves` Kahn 算法伪代码

```python
# dispatcher.py
def resolve_waves(tasks: list[TeamTask]) -> list[list[TeamTask]]:
    """Kahn 算法按依赖分层。

    Args:
        tasks: TeamPlan.tasks 列表

    Returns:
        waves: 每层一组可并行执行的 TeamTask（依赖已全部在前置 wave 完成）
    """
    # 构建 task_id -> index 映射
    id_to_idx = {t.id: i for i, t in enumerate(tasks)}

    # 构建邻接表 + 入度表
    n = len(tasks)
    in_degree = [0] * n
    dependents: list[list[int]] = [[] for _ in range(n)]  # dependents[i] = 依赖 task[i] 的任务索引列表
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

    # Kahn 分层
    waves: list[list[TeamTask]] = []
    completed = set()
    remaining = set(range(n))

    while remaining:
        # 当前 wave：入度为 0 的节点
        current = [i for i in remaining if in_degree[i] == 0]
        if not current:
            # 循环：把剩余节点中入度最小的强加进当前 wave（打破循环）
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

#### 边界情况

| 情况 | waves 结果 |
|---|---|
| 所有任务无 `depends_on` | `[[t1, t2, t3, ...]]` 单层，等价于现有并行 fan-out |
| 单任务 | `[[t1]]` |
| 线性链 t1→t2→t3 | `[[t1], [t2], [t3]]` 三层串行 |
| 菱形 t1→t2, t1→t3, t2→t4, t3→t4 | `[[t1], [t2, t3], [t4]]` |
| 自环 t1.depends_on=[t1] | 自环边丢弃，`[[t1]]` |
| 越界 t1.depends_on=[t99] | 越界边丢弃，`[[t1]]` |
| 循环 t1→t2→t1 | 强制打破，`[[t1], [t2]]` 或 `[[t2], [t1]]`（取入度最小者） |

### D6. `_inject_upstream_findings` 伪代码

```python
# dispatcher.py
def _inject_upstream_findings(task: TeamTask, findings: dict[str, Finding]) -> dict[str, Finding]:
    """根据 task.depends_on 从 state.findings 提取上游结果。

    Args:
        task: 当前要执行的子任务
        findings: 已完成任务的 findings dict（key = "{agent}:{task_id}:{wave_index}"）

    Returns:
        upstream_findings: 仅包含 task.depends_on 对应的 findings 子集
    """
    upstream: dict[str, Finding] = {}
    for dep_id in task.depends_on:
        # 查找 key 中 task_id == dep_id 的 finding
        for key, finding in findings.items():
            if finding.task_id == dep_id:
                upstream[key] = finding
                break
        else:
            # 依赖未完成（理论上不应发生，barrier 保证），写入占位 warning
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

### D7. Semaphore 限流实现伪代码

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
        _running_tasks.setdefault(thread_id, []).append(task_obj)

        try:
            # abort 监听：abort_event 触发则 cancel
            abort_waiter = asyncio.create_task(abort_event.wait())
            done, pending = await asyncio.wait(
                {task_obj, abort_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if abort_waiter in done:
                # abort 触发
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

### D8. `DangerousTaskClassifier` 接口定义

```python
# classifier.py
import hashlib
from functools import lru_cache
from typing import Any


class ClassificationResult(BaseModel):
    is_dangerous: bool
    reason: str
    suggested_agent: str  # code/deep/rag/...


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

        # 3. 关键词降级（带词边界 \b）
        return self._keyword_fallback(task)

    def _keyword_fallback(self, task: TeamTask) -> ClassificationResult:
        """改进的关键词匹配：ASCII 用 \b 词边界，CJK 用子串。"""
        # 注意：旧实现 _looks_like_dangerous_task 用 "修改" 子串会误命中 "查看修改历史"
        # 新实现：对 ASCII 关键词加 \b 词边界，CJK 关键词要求前后非汉字字符
        text = task.description.lower()
        dangerous_cjk = ["删除文件", "重置", "执行命令"]
        dangerous_ascii = [r"\bdelete\b", r"\breset\b", r"\brm\s+-rf\b", r"\bformat\b"]

        for kw in dangerous_cjk:
            if kw in text:
                return ClassificationResult(
                    is_dangerous=True,
                    reason=f"匹配危险关键词: {kw}",
                    suggested_agent="deep",
                )
        for pat in dangerous_ascii:
            if re.search(pat, text):
                return ClassificationResult(
                    is_dangerous=True,
                    reason=f"匹配危险关键词: {pat}",
                    suggested_agent="deep",
                )
        return ClassificationResult(
            is_dangerous=False,
            reason="无危险关键词命中",
            suggested_agent=task.agent,
        )

    @staticmethod
    def _cache_key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    # _cache_get / _cache_put 用进程内 dict + TTL（可选）
```

### D9. `asyncio.Task` abort 实现伪代码

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

### D10. Retry 策略表

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

### D11. `replan_node` 流程伪代码

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
        # 直接结束（失败）
        return {"_route": "end_failure"}

    findings = state.get("findings", {})
    errors = state.get("errors", [])
    plan = state.get("plan", [])
    message = state["message"]

    # 把 findings + errors 喂给 planner，让 LLM 重新拆解
    planner = Planner(state.get("chat_model"))
    new_plan = await planner.replan(
        original_message=message,
        previous_plan=plan,
        findings=findings,
        errors=errors,
        hint=plan[0].is_dangerous_hint if plan else False,
    )

    if not new_plan.tasks:
        # LLM 判定无需追加
        return {"_route": "end_failure"}

    # 重新解析 waves
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
        "findings": {},  # 清空，重新跑（或保留旧 findings，取决于策略）
        "errors": [],
    }


def _route_after_replan(state: TeamState) -> str:
    """replan 条件边路由。"""
    pending_waves = state.get("pending_waves", [])
    if pending_waves:
        return "dispatch"  # 有新任务，重新派发
    return "end_failure"  # 无新任务，结束（失败）
```

### D12. Findings Key 命名规范

**新格式**：`{agent}:{task_id}:{wave_index}`

示例：
- `code:t1:0` —— code agent 在 wave 0 执行 task t1 的结果
- `rag:t2:1` —— rag agent 在 wave 1 执行 task t2 的结果
- `deep:t3:2` —— deep agent 在 wave 2 执行 task t3 的结果

**旧格式（删除）**：`{agent}-{task_index}`
- 问题：同类型多任务在多波次场景下会覆盖（如两个 code 任务索引都是 `code-0`）

**实现**：

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

### D13. 与现有 6 个分类节点 + `_default_node` 的迁移映射表

| 旧节点（删除） | 新统一节点 | 迁移策略 |
|---|---|---|
| `_deep_node` | `execute_node`（agent="deep"） | `_NODE_DISPATCH["deep"].runner = _get_deep_runner` |
| `_code_node` | `execute_node`（agent="code"） | `_NODE_DISPATCH["code"].runner = _get_code_runner` |
| `_builtin_node` | `execute_node`（agent="builtin"） | `_NODE_DISPATCH["builtin"].runner = _get_builtin_runner` |
| `_team_role_node` | `execute_node`（agent=team_role 名） | `_NODE_DISPATCH[role].runner = _build_custom_agent_runner`；配置缺失时显式失败（D11） |
| `_custom_node` | `execute_node`（agent=custom 名） | `_NODE_DISPATCH[custom].runner = _get_custom_runner` |
| `_default_node` | `execute_node`（default 分支） | `_NODE_DISPATCH` 查不到时走 `_FALLBACK_CONFIG.runner = _get_code_runner`（D7） |

#### `_NODE_DISPATCH` 派发表

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
    pre_run_hook=_log_unknown_agent_fallback,  # logger.warning + 写入 state.warnings
)
```

#### `execute_node` 统一节点

```python
# nodes.py
async def execute_node(state: SubtaskState) -> dict:
    """统一执行节点，替代 6 个分类节点。"""
    task = state["task"]
    upstream_findings = state.get("upstream_findings", {})
    wave_index = state.get("wave_index", 0)
    thread_id = state["parent_thread_id"]
    abort_event = state.get("abort_event")

    config = _NODE_DISPATCH.get(task.agent, _FALLBACK_CONFIG)
    if config.pre_run_hook:
        config.pre_run_hook(task, state)

    # 危险任务分类（D6）
    classifier = DangerousTaskClassifier(state.get("chat_model"))
    classification = await classifier.classify(task, available_tools=_get_available_tools())
    if classification.is_dangerous and task.agent != classification.suggested_agent:
        # 改写到 suggested_agent
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
    result = await acquire_and_run(
        task=task,
        runner_coro=config.runner(task, injected_input, state),
        thread_id=thread_id,
        abort_event=abort_event,
    )

    # 用 retry 包装
    result = await run_with_retry(
        task=task,
        runner_factory=lambda: config.runner(task, injected_input, state),
    )

    return _make_subtask_state_update(result, task, wave_index)
```

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
- `max_replan_attempts=1`（可配置，默认保守）
- replan_count >= max 时直接结束（失败）
- 发射 `replan` SSE 事件便于前端观测

### R7. 危险分类器延迟

**风险**：LLM 分类延迟过高（>2s），拖慢整体 plan。

**缓解**：
- 缓存按 `task.description` hash，相同描述不重复分类
- LLM 不可用时降级到关键词匹配（毫秒级）
- 可配置超时（`team_classifier_timeout: float = 2.0`）

## Testing Strategy

### 单元测试

- `test_team_v2.py`：模块拆分后的核心流程（plan / dispatch / execute / aggregate 端到端）
- `test_team_v2_dag.py`：
  - `resolve_waves` 拓扑排序正确性（线性/菱形/孤岛/循环打破）
  - `_inject_upstream_findings` findings 注入与截断
  - `dispatch_node` 分 wave 派发
  - `barrier_node` 条件边路由
  - `execute_node` upstream_findings 拼入 context
- `test_team_v2_fallback.py`：
  - 未知 agent fallback 到 code runner
  - team_role 缺 system_prompt 显式失败
  - `validate_team_subagents` 启动校验
- `test_team_v2_concurrency.py`：
  - Semaphore 限流（10 子任务 + max_concurrency=5 验证最多 5 并发）
  - abort cancel（abort_event 触发后子任务返回 "用户中止"）
  - retry 策略（瞬态错误重试、逻辑错误不重试、退避间隔）
- `test_team_v2_classifier.py`：
  - DangerousTaskClassifier LLM 路径
  - 关键词降级路径
  - 词边界 `\b` 防止「修改」误命中「查看修改历史」
  - 缓存命中
- `test_team_v2_replan.py`：
  - `replan_node` 质量门失败时追加任务
  - `max_replan_attempts` 限制
  - findings key 复合格式 `{agent}:{task_id}:{wave_index}`
  - token 事件 schema 统一 `{agent, content}`

### 集成测试

- 顺序任务端到端：`[code] 读取 → [deep][after:t1] 修改` 验证 deep 拿到 code 的 findings
- 并行任务回归：无 `depends_on` 的任务仍并行执行
- 混合任务：`[code] t1 → [code] t2` + `[deep][after:t1,t2] t3` 验证 t3 等 t1、t2 都完成
- replan：第一轮 `[code] 读取` → replan 追加 `[deep][after:t1] 修改`
- fallback：`[unknown_agent] 任务` 验证走 code runner
- abort：子任务执行中 abort 验证返回 "用户中止"
- retry：mock runner 抛 `TimeoutError` 验证重试 3 次
- 显式失败：team_role 缺 system_prompt 验证返回失败结果
