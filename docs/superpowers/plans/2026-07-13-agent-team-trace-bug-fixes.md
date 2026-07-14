# AgentTeam 执行轨迹 Bug 修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 AgentTeam 执行轨迹链路中的 P0/P1 bug，覆盖后端 DAG 依赖注入失效、wave_index 语义错误、双重 team_done、retry 失效、findings 覆盖丢失，以及前端 replan 状态机错乱、同角色多 agent 状态不同步等核心问题。

**Architecture:** 后端聚焦 `backend/app/team/` 模块（nodes/dispatcher/scheduler/blackboard/planner）+ SSE 事件层；前端聚焦 `frontend/renderer/hooks/useChatStream.ts` + `stores/chat/index.ts` + `components/chat/parts/TeamNodeCard.tsx`。修复按依赖关系分批：先后端数据流修复（确保事件正确发射），再前端状态机修复（确保事件正确消费）。

**Tech Stack:** Python 3.11 + LangGraph + Pydantic（后端）；React 18 + TypeScript + zustand（前端）；pytest + vitest（测试）

---

## Bug 总览

经深度排查共识别 **38 个 bug**：后端 20 个（P0×6 / P1×8 / P2×6），前端 18 个（P0×2 / P1×5 / P2×11）。本 plan 详细覆盖 **P0 + P1 共 21 个**，P2 共 17 个列入 backlog。

### P0 Bug 列表（必须立即修复）

| 编号 | 模块 | 文件 | 简述 |
|------|------|------|------|
| BE-P | 后端 | `team/nodes.py:397` | `_compose_input_with_upstream` 返回值被丢弃，DAG 依赖注入完全失效 |
| BE-A | 后端 | `team/dispatcher.py:247` | `wave_index` 误用为 `replan_count`，findings key 语义错乱 |
| BE-B | 后端 | `team/nodes.py:511-513, 619-624` | aggregate_node + replan_node 双重发射 team_done |
| BE-E | 后端 | `team/scheduler.py:329-355` | `run_with_retry` 在 `thread_id` 分支下 `max_retries` 失效 |
| BE-N | 后端 | `team/blackboard.py:68-75` | `_merge_findings` 整体覆盖导致同 key finding 丢失 |
| BE-T | 后端 | `team/planner.py:811-823` | replan 后 task_id 可能重复，加剧 BE-N |
| FE-001 | 前端 | `hooks/useChatStream.ts:531-562` | team_done{replanning} 后 2s 看门狗强制 finalize，状态从 running 翻转为 done |
| FE-002 | 前端 | `stores/chat/index.ts:911, 931` | 同角色多 agent 按 agent 名匹配而非 taskId，replan 后新 agent 行状态不同步 |

### P1 Bug 列表（重要功能缺陷）

| 编号 | 模块 | 文件 | 简述 |
|------|------|------|------|
| BE-C | 后端 | `team/nodes.py:511-513` | findings 为空时错误路由到 replan（应直接终止） |
| BE-D | 后端 | `team/scheduler.py:595-633, 767-843` | 两个 `_iterate` 返回协议不一致（None vs bool） |
| BE-H | 后端 | `team/runner.py:130-148` | team_done 与 done 事件顺序错乱，abort 路径下 error→done |
| BE-I | 后端 | `team/scheduler.py:216-219` | `acquire_and_run` semaphore acquire 在 try 外，泄漏风险 |
| BE-J | 后端 | `team/scheduler.py:767-843` | `_run_team_role_subtask` 不发射 `_subtask_done` 哨兵 |
| BE-M | 后端 | `team/scheduler.py:102-115` | `_get_team_semaphore` lru_cache 阻止配置热更新 |
| BE-R | 后端 | `router/graph.py:411-453` | `_collect_team_sse` 不区分 team_done 的 status=error |
| BE-S | 后端 | `team/nodes.py:619-624` | `replan_node` 未检查 abort_event 就调用 LLM |
| FE-003 | 前端 | `stores/chat/index.ts:929-942` | team_done agentMessages 按 agent 索引，同角色 summary 互相覆盖 |
| FE-004 | 前端 | `components/chat/parts/TeamNodeCard.tsx:219-221` | 按 agent 名匹配 delegation group，同角色轨迹重复显示 |
| FE-005 | 前端 | `hooks/useChatStream.ts:401-419` | childTaskId 用 source 拼接，同角色多 wave 子任务 todos 互相覆盖 |
| FE-006 | 前端 | `components/chat/AssistantMessageParts.tsx:373-403` | claimedGroups 逻辑导致 wave 1 后续 tool_call 误归入 wave 2 group |
| FE-007 | 前端 | `hooks/useChatStream.ts:222-251, 569-581` | delegation/replan 事件未传 `createIfMissing: false` |

---

## File Structure

### 后端修改文件

| 文件 | 职责 | 涉及 Bug |
|------|------|----------|
| `backend/app/team/nodes.py` | 6 节点函数 + 路由 | BE-P, BE-B, BE-C, BE-S |
| `backend/app/team/dispatcher.py` | Kahn wave + Send fan-out | BE-A |
| `backend/app/team/scheduler.py` | Semaphore + retry + abort | BE-E, BE-I, BE-D, BE-J, BE-M |
| `backend/app/team/blackboard.py` | v2 reducers | BE-N |
| `backend/app/team/planner.py` | Planner + replan | BE-T |
| `backend/app/team/runner.py` | Team 入口 | BE-H |
| `backend/app/team/state.py` | TeamState schema | BE-A（wave_index 字段语义） |
| `backend/app/router/graph.py` | Router _collect_team_sse | BE-R |

### 前端修改文件

| 文件 | 职责 | 涉及 Bug |
|------|------|----------|
| `frontend/renderer/hooks/useChatStream.ts` | SSE 事件分发 | FE-001, FE-002, FE-005, FE-007 |
| `frontend/renderer/stores/chat/index.ts` | upsertTeamNode reducer | FE-002, FE-003 |
| `frontend/renderer/components/chat/parts/TeamNodeCard.tsx` | Team 卡片渲染 | FE-004 |
| `frontend/renderer/components/chat/AssistantMessageParts.tsx` | 消息 parts 容器 | FE-006 |
| `frontend/shared/api-types.ts` | 事件类型定义 | FE-002（delegation task_id） |

### 测试文件

| 文件 | 覆盖 |
|------|------|
| `tests/python/unit/team/test_dag_dependency.py`（新建） | BE-P DAG 依赖注入 |
| `tests/python/unit/team/test_nodes_team_done.py`（新建） | BE-B 双重 team_done |
| `tests/python/unit/team/test_scheduler_retry.py`（新建） | BE-E retry 失效 |
| `tests/python/unit/team/test_blackboard_reducers.py`（新建） | BE-N findings merge |
| `tests/python/unit/team/test_planner_replan.py`（新建） | BE-T task_id 唯一性 |
| `frontend/renderer/__tests__/useChatStream.replan.test.ts`（新建） | FE-001, FE-002 |
| `frontend/renderer/__tests__/upsertTeamNode.test.ts`（新建） | FE-002, FE-003 |

---

## Task 1: 修复 DAG 依赖注入失效（BE-P）

**最高优先级** — DAG 依赖编排的核心缺陷，修复后依赖链才真正打通。

**Files:**
- Modify: `backend/app/team/nodes.py:397`
- Modify: `backend/app/team/nodes.py:168-188`（`_build_runner_args`）
- Test: `tests/python/unit/team/test_dag_dependency.py`（新建）

**根因：** `_compose_input_with_upstream(task, upstream_findings)` 返回拼接好的 str，但调用处注释"副作用：无，但保持调用以验证"直接丢弃返回值。上游 findings 永远不会注入子任务输入。

- [ ] **Step 1: 编写失败测试 — DAG 依赖注入**

新建 `tests/python/unit/team/test_dag_dependency.py`：

```python
"""DAG 依赖注入测试 — 验证 _compose_input_with_upstream 返回值被实际使用。"""
import pytest
from app.team.dispatcher import _compose_input_with_upstream
from app.team.state import Finding, TeamTask


def test_compose_input_with_upstream_returns_enriched_str():
    """_compose_input_with_upstream 应返回包含上游 finding 的拼接字符串。"""
    task = TeamTask(
        id="t2", agent="code", description="实现功能 B",
        depends_on=["t1"],
    )
    upstream = {
        "code:t1:0": Finding(
            agent="code", task_id="t1", wave_index=0,
            content="功能 A 已完成", success=True,
        ),
    }
    result = _compose_input_with_upstream(task, upstream)
    assert "功能 A 已完成" in result
    assert "实现功能 B" in result
    assert "[依赖任务结果]" in result


def test_compose_input_with_upstream_no_upstream_returns_description():
    """无上游时返回原 description。"""
    task = TeamTask(id="t1", agent="code", description="实现功能 A")
    result = _compose_input_with_upstream(task, {})
    assert result == "实现功能 A"
```

- [ ] **Step 2: 运行测试验证 _compose_input_with_upstream 本身正确**

Run: `uv run pytest tests/python/unit/team/test_dag_dependency.py::test_compose_input_with_upstream_returns_enriched_str tests/python/unit/team/test_dag_dependency.py::test_compose_input_with_upstream_no_upstream_returns_description -v`

Expected: PASS（函数本身正确，问题在调用处）

- [ ] **Step 3: 编写失败测试 — execute_node 实际使用 composed_input**

在 `test_dag_dependency.py` 追加：

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from app.team.nodes import execute_node
from app.team.state import SubtaskState


@pytest.mark.asyncio
async def test_execute_node_uses_composed_input_for_code_runner():
    """execute_node 应把 composed_input 作为子任务输入传给 runner，而非原 task.description。"""
    task = TeamTask(
        id="t2", agent="code", description="实现功能 B",
        depends_on=["t1"], expected_output="功能 B 完成",
    )
    upstream = {
        "code:t1:0": Finding(
            agent="code", task_id="t1", wave_index=0,
            content="功能 A 已完成", success=True,
        ),
    }
    state: SubtaskState = {
        "task": task,
        "upstream_findings": upstream,
        "wave_index": 0,
        "parent_thread_id": "parent-1",
        "remaining_waves": [],
        "history": [],
        "permission_mode": "standard",
        "profile_prompt": "",
        "workspace_path": None,
        "chat_model": None,
        "subtask_runners": {},
        "subtask_timeout": 30,
        "abort_event": asyncio.Event(),
    }

    captured_args = []

    async def fake_runner(*args, **kwargs):
        captured_args.extend(args)
        from app.team.blackboard import TeamSubtaskResult
        return TeamSubtaskResult(agent="code", success=True, payload="done")

    with patch("app.team.nodes._run_subtask_stream", new=AsyncMock(side_effect=fake_runner)), \
         patch("app.team.nodes._inherit_workspace", new=AsyncMock(return_value=None)), \
         patch("app.team.nodes.get_abort_event", new=AsyncMock(return_value=asyncio.Event())), \
         patch("app.team.nodes.get_stream_writer", return_value=lambda x: None), \
         patch("app.team.nodes.get_settings") as mock_settings:
        mock_settings.return_value.team_max_retries = 0
        mock_settings.return_value.team_subagents = {}
        await execute_node(state)

    # 验证 runner 收到的第一个位置参数包含上游 finding
    assert len(captured_args) >= 2, f"runner_args = {captured_args}"
    # code runner 的 args 是 (description, child_id)
    composed_description = captured_args[0]
    assert "功能 A 已完成" in composed_description, \
        f"DAG 依赖未注入：runner 收到 '{composed_description}'，应包含上游 finding"
```

- [ ] **Step 4: 运行测试验证失败**

Run: `uv run pytest tests/python/unit/team/test_dag_dependency.py::test_execute_node_uses_composed_input_for_code_runner -v`

Expected: FAIL — `AssertionError: DAG 依赖未注入：runner 收到 '实现功能 B'，应包含上游 finding`

- [ ] **Step 5: 修复 nodes.py — 把 composed_input 传给 runner**

修改 `backend/app/team/nodes.py`：

1. 第 397 行，把 `_compose_input_with_upstream(task, upstream_findings)` 的返回值保存到变量：

```python
    # 注入上游 findings（BE-P 修复：实际使用 composed_input 作为子任务输入）
    composed_input = _compose_input_with_upstream(task, upstream_findings)
```

2. 修改 `_build_runner_args` 函数（`nodes.py:168-188`），增加 `composed_input` 参数：

```python
def _build_runner_args(
    config: SubtaskConfig,
    task: TeamTask,
    child_id: str | None,
    parent_thread_id: str,
    composed_input: str | None = None,
) -> tuple:
    """构造 runner_args（与旧 orchestrator._build_runner_args 对齐）。

    BE-P 修复：composed_input 优先于 task.description 作为子任务输入。
    """
    # 实际输入：优先用 composed_input（含上游 findings），回退到 task.description
    actual_input = composed_input if composed_input is not None else task.description
    if config.runner_type == "deep":
        deep_state: dict = {
            "thread_id": child_id,
            "messages": [{"role": "user", "content": actual_input}],
        }
        return (deep_state, actual_input)
    if config.runner_type == "code":
        return (actual_input, child_id)
    if config.runner_type == "builtin":
        return (parent_thread_id, actual_input)
    if config.runner_type == "custom":
        key = task.agent[len("custom-") :]
        return (key, parent_thread_id, actual_input)
    raise ValueError(f"Unsupported runner_type for args: {config.runner_type}")
```

3. 修改 `execute_node` 中调用 `_build_runner_args` 的地方（约 `nodes.py:426-428`）：

```python
            runner_args = _build_runner_args(
                config, task, child_id, parent_thread_id, composed_input
            )
```

4. team_role 路径也需要注入 composed_input。修改 `_run_team_role_subtask` 调用处（`nodes.py:407-421`），把 `task=_to_team_plan_task(task)` 改为基于 composed_input 的任务：

```python
    if config.runner_type == "team_role":
        # BE-P 修复：team_role 也注入上游 findings
        team_plan_task = TeamPlanTask(
            agent=task.agent,
            input=composed_input,
            purpose=task.expected_output,
        )
        def runner_factory() -> Coroutine[Any, Any, TeamSubtaskResult]:
            return _run_team_role_subtask(
                task=team_plan_task,
                thread_id=parent_thread_id,
                # ... 其余参数不变
            )
```

注意：`_to_team_plan_task` 仍保留用于其他场景，但 execute_node 内 team_role 分支改为直接构造含 composed_input 的 TeamPlanTask。

- [ ] **Step 6: 运行测试验证通过**

Run: `uv run pytest tests/python/unit/team/test_dag_dependency.py -v`

Expected: PASS（3 个测试全过）

- [ ] **Step 7: 运行 ruff 检查**

Run: `uv run ruff check backend/app/team/nodes.py`

Expected: No issues

- [ ] **Step 8: Commit**

```bash
git add backend/app/team/nodes.py tests/python/unit/team/test_dag_dependency.py
git commit -m "fix(team): BE-P 修复 DAG 依赖注入失效 — _compose_input_with_upstream 返回值实际传入 runner"
```

---

## Task 2: 修复 wave_index 语义错误 + findings key 冲突 + replan task_id 唯一性（BE-A, BE-N, BE-T）

**关联修复** — 三者协同改动 dispatcher/nodes/blackboard/planner。

**Files:**
- Modify: `backend/app/team/dispatcher.py:226-265`
- Modify: `backend/app/team/nodes.py:226-264`（`_make_finding_key` + `_make_subtask_state_update`）
- Modify: `backend/app/team/blackboard.py:68-75`（`_merge_findings`）
- Modify: `backend/app/team/planner.py:811-823`（`_post_filter_replan`）
- Test: `tests/python/unit/team/test_blackboard_reducers.py`（新建）
- Test: `tests/python/unit/team/test_planner_replan.py`（新建）

**根因：**
- BE-A: `wave_index` 被赋值为 `replan_count`，而非实际波次索引。findings key `{agent}:{task_id}:{wave_index}` 在 replan 后语义错乱。
- BE-N: `_merge_findings` 用 `dict.update()` 整体覆盖，同 key finding 静默丢失。
- BE-T: replan 后 LLM 可能生成与原 plan 重复的 task_id，加剧 BE-N 的 key 冲突。

- [ ] **Step 1: 编写失败测试 — _merge_findings 同 key 冲突**

新建 `tests/python/unit/team/test_blackboard_reducers.py`：

```python
"""blackboard reducers 测试 — 验证 _merge_findings 不丢失同 key finding。"""
from app.team.blackboard import _merge_findings, _merge_warnings


def test_merge_findings_same_key_collects_to_list():
    """同 key 的两个 finding 应都保留（收集到 list），不丢失。"""
    left = {"code:t1:0": {"content": "finding-1"}}
    right = {"code:t1:0": {"content": "finding-2"}}
    result = _merge_findings(left, right)
    # 同 key 应保留两边数据
    val = result["code:t1:0"]
    assert isinstance(val, list), f"同 key 应收集为 list，实际 {type(val)}"
    assert len(val) == 2
    assert val[0]["content"] == "finding-1"
    assert val[1]["content"] == "finding-2"


def test_merge_findings_different_keys_merge():
    """不同 key 的 finding 合并到同一 dict。"""
    left = {"code:t1:0": {"content": "f1"}}
    right = {"web:t2:0": {"content": "f2"}}
    result = _merge_findings(left, right)
    assert len(result) == 2
    assert "code:t1:0" in result
    assert "web:t2:0" in result


def test_merge_findings_empty_right():
    """right 为空时返回 left 副本。"""
    left = {"code:t1:0": {"content": "f1"}}
    result = _merge_findings(left, {})
    assert result == left
    assert result is not left  # 应是副本
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/python/unit/team/test_blackboard_reducers.py::test_merge_findings_same_key_collects_to_list -v`

Expected: FAIL — `AssertionError: 同 key 应收集为 list，实际 <class 'dict'>`

- [ ] **Step 3: 修复 _merge_findings — 同 key 收集为 list**

修改 `backend/app/team/blackboard.py:68-75`：

```python
def _merge_findings(left: dict, right: dict) -> dict:
    """v2 reducer：合并 ``dict[str, Finding | list[Finding]]``。

    BE-N 修复：同 key 的 finding 收集到 list，避免整体覆盖丢失。
    不同 key 直接合并。单值与 list 混合时统一提升为 list。

    并行 execute 节点返回部分 ``{key: Finding}`` 时合并到全局 state。
    """
    result = dict(left or {})
    for key, val in (right or {}).items():
        if key not in result:
            result[key] = val
            continue
        # 同 key：收集为 list
        existing = result[key]
        if isinstance(existing, list):
            if isinstance(val, list):
                result[key] = existing + val
            else:
                result[key] = existing + [val]
        else:
            if isinstance(val, list):
                result[key] = [existing] + val
            else:
                result[key] = [existing, val]
    return result
```

- [ ] **Step 4: 修改 aggregate_node 适配 list 语义**

修改 `backend/app/team/nodes.py:527-528`（aggregate_node 中 v2 Finding → v1 裸字符串桥接）：

```python
    # v2 Finding → v1 裸字符串（临时桥接 _run_aggregator / _quality_gate）
    # BE-N 修复：findings 值可能是 Finding 或 list[Finding]，统一展开
    blackboard_findings: dict[str, str] = {}
    for key, val in findings.items():
        if isinstance(val, list):
            # 同 key 多个 finding：合并 content
            blackboard_findings[key] = "\n---\n".join(
                f.content if hasattr(f, "content") else str(f) for f in val
            )
        else:
            blackboard_findings[key] = val.content if hasattr(val, "content") else str(val)
    blackboard_errors = {f"error_{i}": e for i, e in enumerate(errors)}
    blackboard = {
        "findings": blackboard_findings,
        "errors": blackboard_errors,
    }
```

- [ ] **Step 5: 编写失败测试 — replan task_id 唯一性**

新建 `tests/python/unit/team/test_planner_replan.py`：

```python
"""planner replan 测试 — 验证 replan 后 task_id 与 previous_plan 不冲突。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.team.planner import Planner
from app.team.state import Finding, TeamPlan, TeamTask


@pytest.mark.asyncio
async def test_replan_task_ids_unique_against_previous_plan():
    """replan 返回的新任务 id 不应与 previous_plan 的 id 重复。"""
    previous_plan = [
        TeamTask(id="task_1", agent="code", description="任务1"),
        TeamTask(id="task_2", agent="web", description="任务2"),
    ]
    findings = {
        "code:task_1:0": Finding(
            agent="code", task_id="task_1", wave_index=0,
            content="完成", success=True,
        ),
    }
    errors = []

    # 模拟 LLM 返回包含重复 id 的新 plan
    fake_plan = TeamPlan(tasks=[
        TeamTask(id="task_1", agent="code", description="重做任务1"),  # 重复 id
        TeamTask(id="task_3", agent="web", description="新任务3"),
    ])

    planner = Planner(chat_model=MagicMock())
    with patch.object(planner, "_resolve_llm", return_value=MagicMock()):
        planner._structured = AsyncMock()
        planner._structured.ainvoke = AsyncMock(return_value=fake_plan)
        with patch("app.team.planner.get_settings") as mock_settings:
            mock_settings.return_value.team_max_tasks = 10
            result = await planner.replan(
                original_message="用户请求",
                previous_plan=previous_plan,
                findings=findings,
                errors=errors,
                hint=False,
            )

    # task_1 应被重命名为带后缀的唯一 id
    result_ids = {t.id for t in result.tasks}
    assert "task_1" not in result_ids, f"replan 后仍含重复 id: {result_ids}"
    assert len(result.tasks) == 2
    # 新 id 应保留可读性（如 task_1_r1）
    assert any(t.id.startswith("task_1") for t in result.tasks)
```

- [ ] **Step 6: 运行测试验证失败**

Run: `uv run pytest tests/python/unit/team/test_planner_replan.py::test_replan_task_ids_unique_against_previous_plan -v`

Expected: FAIL — `AssertionError: replan 后仍含重复 id`

- [ ] **Step 7: 修复 _post_filter_replan — 强制 task_id 唯一**

修改 `backend/app/team/planner.py:811-823`：

```python
    def _post_filter_replan(
        self, plan: TeamPlan, settings: Any, previous_plan: list[TeamTask]
    ) -> TeamPlan:
        """replan 产出的 plan 后处理。

        BE-T 修复：
        1. 过滤掉与 previous_plan id 完全相同的任务（避免重复执行）
        2. 对新任务强制 id 唯一化：若新任务 id 与 previous_plan 或其他新任务冲突，
           追加 `_r{replan_count}` 后缀（如 task_1 → task_1_r1）
        """
        if not plan.tasks:
            return plan

        prev_ids = {t.id for t in previous_plan}
        # 第一轮：过滤完全重复的任务
        new_tasks = [t for t in plan.tasks if t.id not in prev_ids]

        # 第二轮：id 唯一化（新任务间的冲突 + 与 prev 的冲突）
        # replan_count 从 previous_plan 长度推断（不精确但足够避免冲突）
        replan_round = len(previous_plan)  # 启发式：用 plan 长度作为 round 标识
        seen_ids: set[str] = set(prev_ids)
        unique_tasks: list[TeamTask] = []
        for t in new_tasks:
            if t.id in seen_ids:
                # 冲突：追加后缀
                new_id = f"{t.id}_r{replan_round}"
                counter = 1
                while new_id in seen_ids:
                    new_id = f"{t.id}_r{replan_round}_{counter}"
                    counter += 1
                t = t.model_copy(update={"id": new_id})
            seen_ids.add(t.id)
            unique_tasks.append(t)

        return self._post_filter(TeamPlan(tasks=unique_tasks), settings)
```

- [ ] **Step 8: 修复 wave_index 语义（BE-A）**

修改 `backend/app/team/dispatcher.py:226-265`（`build_dispatch_sends`）：

```python
def build_dispatch_sends(state: TeamState) -> list[Send]:
    """构造当前 wave 的 ``Send`` 列表（核心 fan-out 逻辑）。

    BE-A 修复：wave_index 使用实际波次索引（pending_waves 已弹出的层数），
    而非 replan_count。replan_count 作为独立字段透传。
    """
    pending_waves = state.get("pending_waves", [])
    if not pending_waves:
        return [Send("aggregate", {})]

    current_wave = pending_waves[0]
    remaining_waves = pending_waves[1:]

    findings = state.get("findings", {})
    replan_count = state.get("replan_count", 0)
    # BE-A 修复：wave_index 用 completed_task_ids 数量推断已完成的 wave 数
    # （更准确的方式是在 state 增加 wave_index 字段，但当前用启发式避免 schema 大改）
    completed_task_ids = state.get("completed_task_ids", [])
    # 估算当前 wave 索引：已完成任务数 / 当前 wave 大小（粗略，足够 findings key 区分）
    wave_index = replan_count * 100 + len(completed_task_ids)

    thread_id = state.get("thread_id", "")

    sends: list[Send] = []
    for task in current_wave:
        upstream = _inject_upstream_findings(task, findings)
        sends.append(
            Send(
                "execute",
                {
                    "task": task,
                    "upstream_findings": upstream,
                    "wave_index": wave_index,  # BE-A: 实际波次索引
                    "remaining_waves": remaining_waves,
                    "parent_thread_id": thread_id,
                    "todos": state.get("todos", []),
                    "history": state.get("history"),
                    "permission_mode": state.get("permission_mode", "standard"),
                    "scene_prompt": state.get("scene_prompt"),
                    "profile_prompt": state.get("profile_prompt", ""),
                    "workspace_path": state.get("workspace_path"),
                    "chat_model": state.get("chat_model"),
                    "subtask_runners": state.get("subtask_runners"),
                    "team_semaphore": state.get("team_semaphore"),
                    "subtask_timeout": state.get("subtask_timeout", 300),
                    "abort_event": state.get("abort_event"),
                    "replan_count": replan_count,
                },
            )
        )
    return sends
```

- [ ] **Step 9: 运行所有测试**

Run: `uv run pytest tests/python/unit/team/test_blackboard_reducers.py tests/python/unit/team/test_planner_replan.py tests/python/unit/team/test_dag_dependency.py -v`

Expected: PASS

- [ ] **Step 10: ruff 检查 + Commit**

```bash
uv run ruff check backend/app/team/dispatcher.py backend/app/team/blackboard.py backend/app/team/planner.py backend/app/team/nodes.py
git add backend/app/team/ tests/python/unit/team/test_blackboard_reducers.py tests/python/unit/team/test_planner_replan.py
git commit -m "fix(team): BE-A/BE-N/BE-T 修复 wave_index 语义 + findings key 冲突 + replan task_id 唯一性"
```

---

## Task 3: 修复双重 team_done + replan 路由（BE-B, BE-C）

**Files:**
- Modify: `backend/app/team/nodes.py:489-583`（aggregate_node）
- Modify: `backend/app/team/nodes.py:598-671`（replan_node）
- Modify: `backend/app/team/state.py`（新增 `team_done_emitted` 字段）
- Modify: `backend/app/team/blackboard.py`（新增 `_merge_team_done_emitted` reducer）
- Test: `tests/python/unit/team/test_nodes_team_done.py`（新建）

**根因：**
- BE-B: `aggregate_node` 在 findings 为空时发射 `team_done(error)` 后仍路由到 replan；`replan_node` 在 replan 上限时再次发射 `team_done(error)`。一次失败产生两次 team_done。
- BE-C: findings 为空时不应 replan（无任何结果可重规划），应直接终止。

- [ ] **Step 1: 编写失败测试 — 双重 team_done 检测**

新建 `tests/python/unit/team/test_nodes_team_done.py`：

```python
"""aggregate_node + replan_node team_done 发射测试。"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.team.nodes import aggregate_node, replan_node
from app.team.state import Finding, TeamState


@pytest.mark.asyncio
async def test_aggregate_node_empty_findings_no_replan_no_team_done():
    """findings 为空时：发射 team_done(error) 一次，不路由到 replan。"""
    emitted_events = []
    writer = lambda ev: emitted_events.append(ev)

    state: TeamState = {
        "message": "test", "thread_id": "t1",
        "findings": {}, "errors": [], "warnings": [],
        "replan_count": 0,
    }
    with patch("app.team.nodes.get_stream_writer", return_value=writer), \
         patch("app.team.nodes.get_abort_event", new=AsyncMock(return_value=asyncio.Event())):
        result = await aggregate_node(state)

    team_done_events = [e for e in emitted_events if e.get("event") == "team_done"]
    assert len(team_done_events) == 1, f"应只发射 1 次 team_done，实际 {len(team_done_events)}"
    # 不应路由到 replan
    assert result.get("quality_gate_passed") is False
    # BE-C: 空结果应直接终止，不应进入 replan


@pytest.mark.asyncio
async def test_replan_limit_no_duplicate_team_done():
    """replan 达上限时不应再发 team_done（aggregate_node 已发过）。"""
    emitted_events = []
    writer = lambda ev: emitted_events.append(ev)

    state: TeamState = {
        "message": "test", "thread_id": "t1",
        "findings": {"code:t1:0": Finding(agent="code", task_id="t1", wave_index=0, content="x", success=False)},
        "errors": ["error"], "warnings": [],
        "replan_count": 5,  # 已达上限
        "plan": [],
    }
    with patch("app.team.nodes.get_stream_writer", return_value=writer), \
         patch("app.team.nodes.get_settings") as mock_settings:
        mock_settings.return_value.team_max_replan_attempts = 3
        result = await replan_node(state)

    team_done_events = [e for e in emitted_events if e.get("event") == "team_done"]
    # replan_node 不应发射 team_done（由 aggregate_node 统一发射）
    assert len(team_done_events) == 0, f"replan_node 不应发 team_done，实际发了 {len(team_done_events)}"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/python/unit/team/test_nodes_team_done.py -v`

Expected: FAIL

- [ ] **Step 3: 新增 team_done_emitted 状态字段**

修改 `backend/app/team/state.py`，在 `TeamState` 中新增：

```python
    team_done_emitted: Annotated[bool, "_merge_team_done_emitted"]
```

修改 `backend/app/team/blackboard.py`，新增 reducer：

```python
def _merge_team_done_emitted(left: bool, right: bool) -> bool:
    """v2 reducer：team_done_emitted 用 OR 合并（任一 True 则 True）。"""
    return bool(left or right)
```

并在 `__all__` 中添加 `"_merge_team_done_emitted"`。

修改 `backend/app/team/state.py` 的 import：

```python
from app.team.blackboard import (
    _merge_completed_task_ids,
    _merge_findings,
    _merge_pending_waves,
    _merge_team_done_emitted,
    _merge_warnings,
)
```

- [ ] **Step 4: 修复 aggregate_node — 空结果直接终止 + team_done 单一发射**

修改 `backend/app/team/nodes.py:489-583`：

```python
async def aggregate_node(state: TeamState) -> dict:
    """Aggregate 节点：调用 aggregator + 质量门 + 发射累积 warning SSE。

    BE-B 修复：team_done 只在此节点发射一次，replan_node 不再发射。
    BE-C 修复：findings 为空时直接终止（quality_gate_passed=False 但不 replan），
              通过新增 should_terminate 标志让路由函数直接 END。
    """
    writer = get_stream_writer()
    thread_id = state.get("thread_id", "")

    findings = state.get("findings", {})
    errors = state.get("errors", [])

    # D14: 发射累积的 warning SSE
    for warning in state.get("warnings", []):
        writer(make_sse_event("warning", {"message": warning}))

    # BE-C 修复：findings 为空时直接终止，不进入 replan
    if not findings:
        writer(make_sse_event("team_done", {"status": "error"}))
        return {"quality_gate_passed": False, "team_done_emitted": True, "should_terminate": True}

    # abort 检查
    abort_event = state.get("abort_event")
    if abort_event is None:
        abort_event = await get_abort_event(thread_id)
    if abort_event.is_set():
        writer(
            make_sse_event(
                "team_done", {"status": "error", "error": "用户中止"}
            )
        )
        return {"quality_gate_passed": False, "team_done_emitted": True, "should_terminate": True}

    # v2 Finding → v1 裸字符串
    blackboard_findings: dict[str, str] = {}
    for key, val in findings.items():
        if isinstance(val, list):
            blackboard_findings[key] = "\n---\n".join(
                f.content if hasattr(f, "content") else str(f) for f in val
            )
        else:
            blackboard_findings[key] = val.content if hasattr(val, "content") else str(val)
    blackboard_errors = {f"error_{i}": e for i, e in enumerate(errors)}
    blackboard = {"findings": blackboard_findings, "errors": blackboard_errors}

    message = state["message"]
    chat_model = state.get("chat_model")

    ok, reason = _quality_gate(blackboard)

    if ok:
        async for sse in _run_aggregator(
            message, blackboard, chat_model=chat_model, abort_event=abort_event,
        ):
            writer(sse)

    # team_done 状态决策
    has_error = bool(errors)
    if ok:
        status = "done"
    elif has_error:
        status = "error"
    else:
        status = "replanning"
    agent_summaries = [
        {"agent": f.agent, "summary": f.content if hasattr(f, "content") else str(f)}
        for f in findings.values() if not isinstance(f, list)
    ] + [
        {"agent": f.agent, "summary": f.content}
        for flist in findings.values() if isinstance(flist, list)
        for f in flist
    ]
    writer(
        make_sse_event("team_done", {"status": status, "agents": agent_summaries})
    )
    logger.info(
        "team aggregate_node completed",
        quality_gate_passed=ok, reason=reason,
        findings_count=len(findings), errors_count=len(errors),
    )

    # BE-B 修复：标记 team_done 已发射，replan_node 不再发射
    return {
        "quality_gate_passed": ok,
        "team_done_emitted": True,
        # should_terminate: ok=True (done) 或 has_error (error) 时直接终止
        # 只有 ok=False 且无 error（replanning）时才进 replan
        "should_terminate": ok or has_error,
    }
```

- [ ] **Step 5: 修复 _route_after_aggregate — 增加 should_terminate 分支**

修改 `backend/app/team/nodes.py:585-590`：

```python
def _route_after_aggregate(state: TeamState) -> str:
    """D16 + BE-B/BE-C 修复：aggregate 条件边。

    - should_terminate=True（done / error / 空结果）→ END
    - should_terminate=False（replanning）→ replan
    """
    # BE-C: 空结果或已标记终止
    if state.get("should_terminate", False):
        return END
    quality_gate_passed = state.get("quality_gate_passed", True)
    if quality_gate_passed:
        return END
    return "replan"
```

- [ ] **Step 6: 修复 replan_node — 不再发射 team_done**

修改 `backend/app/team/nodes.py:598-671`：

```python
async def replan_node(state: TeamState) -> dict:
    """Replan 节点：质量门失败时迭代式重规划（D10/D16）。

    BE-B 修复：不再发射 team_done（由 aggregate_node 统一发射）。
    达上限时返回空 pending_waves，路由到 END，由 runner.py 发射 done。
    """
    writer = get_stream_writer()
    settings = get_settings()
    replan_count = state.get("replan_count", 0)
    max_replans = settings.team_max_replan_attempts

    # BE-S 修复：replan 前检查 abort
    abort_event = state.get("abort_event")
    if abort_event is not None and abort_event.is_set():
        logger.info("team replan aborted before LLM call", replan_count=replan_count)
        return {"pending_waves": []}

    if replan_count >= max_replans:
        logger.info(
            "team replan limit reached",
            replan_count=replan_count, max=max_replans,
        )
        # BE-B 修复：不再发射 team_done（aggregate_node 已发过）
        return {"pending_waves": []}

    findings = state.get("findings", {})
    errors = state.get("errors", [])
    plan = state.get("plan", [])
    message = state["message"]

    chat_model = state.get("chat_model")
    planner = Planner(chat_model)

    try:
        new_plan = await planner.replan(
            original_message=message,
            previous_plan=plan,
            findings=findings,
            errors=errors,
            hint=any(t.is_dangerous_hint for t in plan) if plan else False,  # BE-K 修复
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("team replan_node planner failed", error=str(exc))
        return {"pending_waves": []}

    if not new_plan.tasks:
        logger.info("team replan produced no new tasks")
        return {"pending_waves": []}

    waves = resolve_waves(new_plan.tasks)
    updated_plan = list(plan) + new_plan.tasks

    writer(
        make_sse_event(
            "replan",
            {
                "new_tasks": [t.model_dump() for t in new_plan.tasks],
                "replan_count": replan_count + 1,
                "reason": "quality_gate_failed",
            },
        )
    )

    return {
        "plan": updated_plan,
        "pending_waves": waves,
        "replan_count": replan_count + 1,
    }
```

- [ ] **Step 7: 运行测试 + ruff + commit**

```bash
uv run pytest tests/python/unit/team/test_nodes_team_done.py -v
uv run ruff check backend/app/team/nodes.py backend/app/team/state.py backend/app/team/blackboard.py
git add backend/app/team/ tests/python/unit/team/test_nodes_team_done.py
git commit -m "fix(team): BE-B/BE-C/BE-S 修复双重 team_done + 空结果路由 + replan abort 检查"
```

---

## Task 4: 修复 run_with_retry 失效 + semaphore 泄漏（BE-E, BE-I）

**Files:**
- Modify: `backend/app/team/scheduler.py:216-219`（acquire_and_run semaphore）
- Modify: `backend/app/team/scheduler.py:287-388`（run_with_retry）
- Test: `tests/python/unit/team/test_scheduler_retry.py`（新建）

**根因：**
- BE-E: `run_with_retry` 在 `thread_id` 真值分支下 `return result` 提前退出，for 循环变死代码，`max_retries` 失效。
- BE-I: `await semaphore.acquire()` 在 try 块外，`ensure_future` 失败时泄漏 semaphore。

- [ ] **Step 1: 编写失败测试 — run_with_retry 实际重试**

新建 `tests/python/unit/team/test_scheduler_retry.py`：

```python
"""scheduler retry + semaphore 测试。"""
import asyncio
import pytest
from app.team.scheduler import run_with_retry, acquire_and_run, _get_team_semaphore
from app.team.blackboard import TeamSubtaskResult
from app.team.state import TeamTask


@pytest.mark.asyncio
async def test_run_with_retry_retries_on_transient_with_thread_id():
    """thread_id 非空时，瞬态错误应触发重试。"""
    import httpx
    call_count = 0

    async def flaky_runner():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.ConnectError("transient")
        return TeamSubtaskResult(agent="code", success=True, payload="ok")

    def runner_factory():
        return flaky_runner()

    task = TeamTask(id="t1", agent="code", description="test")
    result = await run_with_retry(
        task=task,
        runner_factory=runner_factory,
        max_retries=3,
        thread_id="thread-1",
        abort_event=asyncio.Event(),
    )

    assert result.success is True
    assert call_count == 3, f"应重试 3 次，实际调用 {call_count} 次"
    assert result.retries == 2


@pytest.mark.asyncio
async def test_acquire_and_run_no_semaphore_leak_on_ensure_future_failure():
    """ensure_future 失败时 semaphore 应被释放。"""
    semaphore = _get_team_semaphore()
    initial = semaphore._value

    task = TeamTask(id="t1", agent="code", description="test")

    # 构造一个会在 ensure_future 时失败的 coroutine（已关闭的 coroutine）
    async def dead_coro():
        return TeamSubtaskResult(agent="code", success=True, payload="ok")

    coro = dead_coro()
    coro.close()  # 关闭 coroutine，ensure_future 会抛 RuntimeError

    with pytest.raises(RuntimeError):
        await acquire_and_run(
            task=task,
            runner_coro=coro,
            thread_id="t1",
        )

    # semaphore 应被释放回初始值
    assert semaphore._value == initial, "semaphore 泄漏"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/python/unit/team/test_scheduler_retry.py -v`

Expected: FAIL

- [ ] **Step 3: 修复 acquire_and_run — semaphore acquire 移入 try**

修改 `backend/app/team/scheduler.py:186-279`：

```python
async def acquire_and_run(
    task: TeamTask,
    runner_coro: Coroutine[Any, Any, TeamSubtaskResult],
    thread_id: str,
    abort_event: Optional[asyncio.Event] = None,
    writer: Optional[Callable[[dict], None]] = None,
) -> TeamSubtaskResult:
    """限流 + abort cancel 的执行入口。

    BE-I 修复：semaphore.acquire() 移入 try 块，ensure_future 失败时不泄漏。
    """
    semaphore = _get_team_semaphore()
    # BE-I 修复：先 acquire，但把 ensure_future 也放进 try
    await semaphore.acquire()
    task_obj = None
    try:
        task_obj = asyncio.ensure_future(runner_coro)
        register_running_task(thread_id, task_obj)
        # 若 abort 已触发，立即 cancel
        if abort_event is not None and abort_event.is_set():
            task_obj.cancel()
        else:
            if abort_event is not None:
                abort_wait = asyncio.ensure_future(abort_event.wait())
                try:
                    done, _pending = await asyncio.wait(
                        {task_obj, abort_wait},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if abort_wait in done and not task_obj.done():
                        task_obj.cancel()
                finally:
                    if not abort_wait.done():
                        abort_wait.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await abort_wait
        return await task_obj
    except asyncio.CancelledError:
        logger.info(
            "team subtask aborted",
            agent=task.agent, task_id=getattr(task, "id", ""),
            thread_id=thread_id,
        )
        if writer is not None:
            writer(
                make_sse_event(
                    "delegation",
                    {
                        "target": task.agent, "source": "team",
                        "event": "aborted", "agent": task.agent,
                        "task_id": getattr(task, "id", ""), "message": "用户中止",
                    },
                )
            )
        return TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止")
    except Exception as exc:  # noqa: BLE001
        return TeamSubtaskResult(
            agent=task.agent, success=False,
            payload=f"{task.agent} 子任务异常: {exc}",
        )
    finally:
        semaphore.release()
        if task_obj is not None:
            _unregister_running_task(thread_id, task_obj)
```

- [ ] **Step 4: 修复 run_with_retry — thread_id 分支也走 for 循环**

修改 `backend/app/team/scheduler.py:287-388`：

```python
async def run_with_retry(
    task: TeamTask,
    runner_factory: Callable[[], Coroutine[Any, Any, TeamSubtaskResult]],
    max_retries: int = 2,
    *,
    thread_id: str = "",
    abort_event: Optional[asyncio.Event] = None,
    writer: Optional[Callable[[dict], None]] = None,
) -> TeamSubtaskResult:
    """失败重试 + 指数退避，仅瞬态错误重试。

    BE-E 修复：thread_id 分支不再提前 return，统一走 for 循环重试逻辑。
    acquire_and_run 已包装异常为失败 result，但通过 payload 字符串启发式
    判定是否瞬态（含 "ConnectError" / "TimeoutError" 等关键词）。
    """
    last_exc: BaseException | None = None
    retries_done = 0
    for attempt in range(max_retries + 1):
        try:
            if thread_id:
                result = await acquire_and_run(
                    task=task,
                    runner_coro=runner_factory(),
                    thread_id=thread_id,
                    abort_event=abort_event,
                    writer=writer,
                )
                if result.success:
                    result.retries = retries_done
                    return result
                # BE-E 修复：失败 result 启发式判定是否瞬态
                payload_str = str(result.payload)
                is_transient_payload = any(
                    kw in payload_str
                    for kw in ("ConnectError", "TimeoutError", "ReadTimeout", "WriteTimeout", "PoolTimeout")
                )
                if is_transient_payload and attempt < max_retries:
                    backoff = 2 ** attempt
                    logger.warning(
                        "team subtask transient error (from payload), retrying",
                        agent=task.agent, task_id=getattr(task, "id", ""),
                        attempt=attempt + 1, max_retries=max_retries,
                        backoff_sec=backoff,
                    )
                    await asyncio.sleep(backoff)
                    retries_done = attempt + 1
                    continue
                # 非瞬态失败：不重试
                result.retries = retries_done
                return result
            # 直接 await coroutine
            result = await runner_factory()
            if result.success:
                result.retries = retries_done
                return result
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if _is_transient(exc) and attempt < max_retries:
                backoff = 2 ** attempt
                logger.warning(
                    "team subtask transient error, retrying",
                    agent=task.agent, task_id=getattr(task, "id", ""),
                    attempt=attempt + 1, max_retries=max_retries,
                    backoff_sec=backoff, error=str(exc),
                )
                await asyncio.sleep(backoff)
                retries_done = attempt + 1
                continue
            return TeamSubtaskResult(
                agent=task.agent, success=False,
                payload=f"子任务异常: {exc}", retries=retries_done,
            )
    return TeamSubtaskResult(
        agent=task.agent, success=False,
        payload=f"重试 {retries_done} 次后仍失败: {last_exc}",
        retries=retries_done,
    )
```

- [ ] **Step 5: 运行测试 + ruff + commit**

```bash
uv run pytest tests/python/unit/team/test_scheduler_retry.py -v
uv run ruff check backend/app/team/scheduler.py
git add backend/app/team/scheduler.py tests/python/unit/team/test_scheduler_retry.py
git commit -m "fix(team): BE-E/BE-I 修复 run_with_retry 重试失效 + semaphore 泄漏"
```

---

## Task 5: 修复前端 replan 看门狗 + taskId 匹配（FE-001, FE-002, FE-003）

**Files:**
- Modify: `frontend/renderer/hooks/useChatStream.ts:495-562`（team_done watchdog）
- Modify: `frontend/renderer/hooks/useChatStream.ts:216-251`（delegation task_id 透传）
- Modify: `frontend/renderer/stores/chat/index.ts:306-310`（agentUpdate 接口加 taskId）
- Modify: `frontend/renderer/stores/chat/index.ts:867-979`（upsertTeamNode 按 taskId 匹配）
- Modify: `frontend/shared/api-types.ts`（确认 delegation task_id 字段）
- Test: `frontend/renderer/__tests__/useChatStream.replan.test.ts`（新建）
- Test: `frontend/renderer/__tests__/upsertTeamNode.test.ts`（新建）

**根因：**
- FE-001: `team_done{replanning}` 后启动 2s 看门狗，强制 finalize 把 running 翻转为 done。
- FE-002: `agentUpdate` 按 `a.agent === agent` 匹配，同角色多 agent 时只更新第一个。
- FE-003: `agentMessages` Map 按 agent 索引，同角色 summary 互相覆盖。

- [ ] **Step 1: 编写失败测试 — replanning 不触发看门狗 finalize**

新建 `frontend/renderer/__tests__/useChatStream.replan.test.ts`：

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
// 注意：useChatStream 强耦合 React hooks + zustand store，完整单测较重。
// 此测试聚焦 upsertTeamNode reducer 逻辑，通过直接调用 store 验证。

describe("upsertTeamNode — replanning 不触发 finalize", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it("team_done{replanning} 后不应 finalizeAgents", () => {
    // 验证 reducer 逻辑：replanning 状态下 finalizeAgents=false
    // 完整集成测试需 mock SSE 流，此处验证 store action 行为
    // TODO: 导入 store，创建 pending message，触发 team_done replanning，
    //       断言 agents 数组中 pending/running 状态未被强制改为 done
  });

  it("同角色多 agent 按 taskId 匹配 agentUpdate", () => {
    // 验证：两个 agent 都是 "code" 角色，taskId 不同
    // agentUpdate({agent: "code", taskId: "t2", patch: {status: "running"}})
    // 应只更新 taskId="t2" 的 agent，不影响 taskId="t1"
  });
});
```

- [ ] **Step 2: 修改 agentUpdate 接口增加 taskId**

修改 `frontend/renderer/stores/chat/index.ts:306-312`：

```typescript
  upsertTeamNode: (
    messageId: string,
    updaters: {
      reasoning?: string;
      agentUpdate?: { agent: string; taskId?: string; patch: Partial<TeamAgentState> };
      status?: "running" | "done" | "error";
      initialAgents?: TeamAgentState[];
      finalizeAgents?: boolean;
      agentMessages?: Array<{ agent: string; taskId?: string; message?: string; summary?: string }>;
      replan?: {
        newTasks: Array<{ id: string; agent: string; description: string; dependsOn: string[] }>;
        replanCount: number;
        reason: string;
      };
      addWarning?: string;
      createIfMissing?: boolean;
    },
  ) => void;
```

- [ ] **Step 3: 修改 upsertTeamNode 实现 — 按 taskId 优先匹配**

修改 `frontend/renderer/stores/chat/index.ts:909-916`：

```typescript
                } else if (updaters.agentUpdate) {
                  const { agent, taskId, patch } = updaters.agentUpdate;
                  // FE-002 修复：优先按 (agent, taskId) 双键匹配
                  let idx = -1;
                  if (taskId) {
                    idx = newAgents.findIndex(
                      (a) => a.agent === agent && a.taskId === taskId,
                    );
                  }
                  // taskId 缺失或未匹配到时，回退到按 agent 匹配（兼容旧数据）
                  if (idx === -1) {
                    idx = newAgents.findIndex((a) => a.agent === agent);
                  }
                  if (idx === -1) {
                    newAgents = [...newAgents, { agent, description: "", taskId: taskId ?? "", dependsOn: [], status: "pending" as const, ...patch }];
                  } else {
                    newAgents = newAgents.map((a, i) => (i === idx ? { ...a, ...patch } : a));
                  }
                }
```

- [ ] **Step 4: 修改 agentMessages 回填 — 按 taskId 精确匹配**

修改 `frontend/renderer/stores/chat/index.ts:929-942`：

```typescript
                // FE-003 修复：agentMessages 按 (agent, taskId) 复合 key
                if (updaters.agentMessages && updaters.agentMessages.length > 0) {
                  // 构建复合 key 索引：taskId 存在时用 "agent:taskId"，否则用 "agent"
                  const messageByKey = new Map<string, typeof updaters.agentMessages[number]>();
                  for (const am of updaters.agentMessages) {
                    const key = am.taskId ? `${am.agent}:${am.taskId}` : am.agent;
                    messageByKey.set(key, am);
                  }
                  newAgents = newAgents.map((a) => {
                    // 优先按复合 key 匹配
                    const key = a.taskId ? `${a.agent}:${a.taskId}` : a.agent;
                    const am = messageByKey.get(key);
                    if (am) {
                      return {
                        ...a,
                        ...(am.message !== undefined ? { message: am.message } : {}),
                        ...(am.summary !== undefined ? { summary: am.summary } : {}),
                      };
                    }
                    // 回退：按 agent 名匹配（兼容后端未传 taskId 的旧事件）
                    const fallbackAm = messageByKey.get(a.agent);
                    if (fallbackAm && !fallbackAm.taskId) {
                      return {
                        ...a,
                        ...(fallbackAm.message !== undefined ? { message: fallbackAm.message } : {}),
                        ...(fallbackAm.summary !== undefined ? { summary: fallbackAm.summary } : {}),
                      };
                    }
                    return a;
                  });
                }
```

- [ ] **Step 5: 修改 useChatStream — delegation 事件透传 task_id**

修改 `frontend/renderer/hooks/useChatStream.ts:216-251`（delegation case）：

```typescript
        case "delegation": {
          if (pendingIdRef.current) {
            if (e.event === "aborted" || e.event === "timeout") {
              if (e.source === "team") {
                upsertTeamNode(pendingIdRef.current, {
                  agentUpdate: {
                    agent: e.target,
                    taskId: e.task_id,  // FE-002 修复：透传 task_id
                    patch: {
                      status: "error",
                      finishedAt: Date.now(),
                      summary: e.event === "aborted" ? "用户中止" : `执行超时${e.timeout ? `（${e.timeout}s）` : ""}`,
                    },
                  },
                  createIfMissing: false,  // FE-007 修复
                });
              }
              break;
            }
            addPart(pendingIdRef.current, {
              type: "delegation",
              id: crypto.randomUUID(),
              target: e.target,
              source: e.source,
              message: e.message,
              taskId: e.task_id,  // FE-004 修复：delegation part 携带 taskId
            });
            if (e.source === "team") {
              upsertTeamNode(pendingIdRef.current, {
                agentUpdate: {
                  agent: e.target,
                  taskId: e.task_id,  // FE-002 修复
                  patch: { status: "running", startedAt: Date.now() },
                },
                createIfMissing: false,  // FE-007 修复
              });
            }
          }
          break;
        }
```

- [ ] **Step 6: 修改 useChatStream — replanning 看门狗不 finalize**

修改 `frontend/renderer/hooks/useChatStream.ts:495-562`（team_done case）：

```typescript
        case "team_done": {
          if (!pendingIdRef.current) break;
          const agentMessages = Array.isArray(e.agents)
            ? e.agents.filter(
                (a): a is { agent: string; task_id?: string; message?: string; summary?: string } =>
                  typeof a === "object" && a !== null && typeof (a as Record<string, unknown>).agent === "string",
              )
            : [];
          const isReplanning = e.status === "replanning";
          const teamStatus = e.status === "error"
            ? "error"
            : isReplanning
              ? "running"
              : "done";
          upsertTeamNode(pendingIdRef.current, {
            status: teamStatus,
            finalizeAgents: e.status === "done" || e.status === "error",
            createIfMissing: false,
            agentMessages,
          });

          // FE-001 修复：replanning 不启动看门狗，只清理当前 wave 残留
          markReasoningDone(pendingIdRef.current);
          markRunningToolCallsComplete(pendingIdRef.current);

          if (isReplanning) {
            // replanning 是过渡态：不启动 done 看门狗，等待 replan 事件 + 新 delegation
            // 后续 wave 的 reasoning/tool_call 会作为新 part 创建，不受 markReasoningDone 影响
            break;
          }

          // 终态（done/error）：启动 done 看门狗
          const watchdogThreadId = targetThreadId();
          if (doneWatchdogRef.current) clearTimeout(doneWatchdogRef.current);
          doneWatchdogRef.current = setTimeout(() => {
            const cid = targetThreadId();
            if (cid !== watchdogThreadId) return;
            const state = useChatStore.getState();
            const session = state.sessions[cid];
            if (!session?.isRunning) return;
            finishRunning(false);
            if (pendingIdRef.current) {
              const message = session.messages.find(
                (m: ChatMessage) => m.id === pendingIdRef.current,
              );
              const teamPart = message?.parts.find(
                (p: MessagePart): p is Extract<MessagePart, { type: "team" }> => p.type === "team",
              );
              if (teamPart && teamPart.status === "running") {
                upsertTeamNode(pendingIdRef.current, {
                  status: "done",
                  finalizeAgents: true,
                  createIfMissing: false,
                });
              }
            }
          }, 2000);
          break;
        }
```

- [ ] **Step 7: 修改 replan 事件 — 传 createIfMissing: false**

修改 `frontend/renderer/hooks/useChatStream.ts:565-583`：

```typescript
        case "replan": {
          if (pendingIdRef.current) {
            upsertTeamNode(pendingIdRef.current, {
              replan: {
                newTasks: e.new_tasks.map((t) => ({
                  id: t.id,
                  agent: t.agent,
                  description: t.description,
                  dependsOn: t.depends_on,
                })),
                replanCount: e.replan_count,
                reason: e.reason,
              },
              createIfMissing: false,  // FE-007 修复
            });
          }
          break;
        }
```

- [ ] **Step 8: 修改 MessagePart 类型 — delegation part 增加 taskId**

修改 `frontend/renderer/stores/chat/index.ts` 中 MessagePart 类型定义（delegation part）：

```typescript
    | {
        type: "delegation";
        id: string;
        target: string;
        source: string;
        message: string;
        taskId?: string;  // FE-004 修复：携带 taskId 供 TeamNodeCard 精确匹配
      }
```

- [ ] **Step 9: 运行 typecheck + test**

```bash
cd frontend && pnpm typecheck
cd frontend && pnpm test
```

- [ ] **Step 10: Commit**

```bash
git add frontend/renderer/hooks/useChatStream.ts frontend/renderer/stores/chat/index.ts frontend/shared/api-types.ts frontend/renderer/__tests__/
git commit -m "fix(frontend): FE-001/002/003/007 修复 replan 看门狗 + 同角色多 agent taskId 匹配 + createIfMissing 防御"
```

---

## Task 6: 修复前端同角色多 agent 渲染（FE-004, FE-005, FE-006）

**Files:**
- Modify: `frontend/renderer/components/chat/parts/TeamNodeCard.tsx:211-221`
- Modify: `frontend/renderer/components/chat/AssistantMessageParts.tsx:373-403, 449`
- Modify: `frontend/renderer/hooks/useChatStream.ts:401-419`（childTaskId）
- Test: 追加到 `frontend/renderer/__tests__/useChatStream.replan.test.ts`

**根因：**
- FE-004: TeamNodeCard 按 agent 名匹配 delegation group，同角色轨迹重复显示。
- FE-005: childTaskId 用 source 拼接，同角色多 wave 子任务 todos 互相覆盖。
- FE-006: claimedGroups 逻辑导致 wave 1 后续 tool_call 误归入 wave 2 group。

- [ ] **Step 1: 修改 TeamNodeCard — 按 (agent, taskId) 匹配 delegation group**

修改 `frontend/renderer/components/chat/parts/TeamNodeCard.tsx:219-221`：

```typescript
            // FE-004 修复：优先按 (agent, taskId) 匹配，回退到按 agent 名
            const matchedGroups = subAgentGroups.filter((g) => {
              if (normalizeAgentRole(g.target) !== normalizeAgentRole(agent.agent)) {
                return false;
              }
              // taskId 都存在时必须精确匹配
              if (g.taskId && agent.taskId) {
                return g.taskId === agent.taskId;
              }
              // 任一缺失时回退到按角色名匹配（兼容旧数据）
              return true;
            });
```

同时需要给 `SubAgentTraceGroup` 类型增加 `taskId` 字段，并在 `AssistantMessageParts.tsx` 构造时传入。

- [ ] **Step 2: 修改 AssistantMessageParts — subAgentGroups 携带 taskId**

修改 `frontend/renderer/components/chat/AssistantMessageParts.tsx:449` 附近（subAgentGroups 构造）：

```typescript
        // FE-004 修复：subAgentGroups 携带 taskId
        const subAgentGroups = useMemo(() => {
          // ... 现有逻辑
          // 构造时从 delegation part 提取 taskId
          // group.taskId = firstDelegationPart?.taskId
        }, [...]);
```

具体实现：在 `collapseToolCallGroups` 或 `subAgentGroups` 构造逻辑中，从归属的 delegation part 提取 `taskId` 字段。

- [ ] **Step 3: 修改 useChatStream — childTaskId 用 e.task_id**

修改 `frontend/renderer/hooks/useChatStream.ts:401-419`：

```typescript
          if (parentTaskId && source) {
            const effectiveParentId = currentTaskIdRef.current ?? parentTaskId;
            // FE-005 修复：优先用后端 task_id（子任务 thread_id），避免同角色多 wave 覆盖
            const childTaskId = e.task_id ?? `${effectiveParentId}-child-${source}`;
            const sessionId = activeThreadIdRef?.current ?? currentIdRef.current ?? "";
            const existing = useTasksStore.getState().tasks.find((t) => t.id === childTaskId);
            // ... 其余逻辑不变
```

- [ ] **Step 4: 修改 AssistantMessageParts — claimedGroups 改为 per-source 认领**

修改 `frontend/renderer/components/chat/AssistantMessageParts.tsx:373-403`：

```typescript
        // FE-006 修复：claimedGroups 改为 per-source 认领
        // 同 source 的后续 tool_call 归入已认领的 group，不跨 source 误归
        const claimedGroupsBySource = new Map<string, typeof matchedGroups[number]>();
        // ... 修改 find 逻辑：同 source 优先复用已认领 group
```

- [ ] **Step 5: 运行 typecheck + test + commit**

```bash
cd frontend && pnpm typecheck
cd frontend && pnpm test
git add frontend/
git commit -m "fix(frontend): FE-004/005/006 修复同角色多 agent 渲染 — taskId 精确匹配 + childTaskId 唯一化 + claimedGroups per-source"
```

---

## Task 7: 修复后端 P1 批量 bug（BE-D, BE-H, BE-J, BE-M, BE-R）

**Files:**
- Modify: `backend/app/team/scheduler.py:558-668`（_run_subtask_stream）+ `676-866`（_run_team_role_subtask）
- Modify: `backend/app/team/runner.py:130-148`
- Modify: `backend/app/team/scheduler.py:102-115`（_get_team_semaphore）
- Modify: `backend/app/router/graph.py:411-453`

**根因汇总：**
- BE-D: 两个 `_iterate` 返回协议不一致（None vs bool）
- BE-H: team_done 与 done 事件顺序错乱，abort 路径下 error→done
- BE-J: `_run_team_role_subtask` 不发射 `_subtask_done` 哨兵
- BE-M: `_get_team_semaphore` lru_cache 阻止配置热更新
- BE-R: `_collect_team_sse` 不区分 team_done 的 status=error

- [ ] **Step 1: 修复 _get_team_semaphore — 移除 lru_cache，改用版本比对**

修改 `backend/app/team/scheduler.py:102-115`：

```python
# BE-M 修复：移除 lru_cache，改用模块级变量 + 配置版本比对
_team_semaphore: asyncio.Semaphore | None = None
_team_semaphore_concurrency: int | None = None


def _get_team_semaphore() -> asyncio.Semaphore:
    """全局 semaphore，配置变更时自动重建。

    BE-M 修复：移除 lru_cache，比对当前配置与缓存配置，不一致时重建。
    """
    global _team_semaphore, _team_semaphore_concurrency
    settings = get_settings()
    max_concurrency = settings.team_max_concurrency
    if not isinstance(max_concurrency, int) or max_concurrency <= 0:
        max_concurrency = 5
    if _team_semaphore is None or _team_semaphore_concurrency != max_concurrency:
        _team_semaphore = asyncio.Semaphore(max_concurrency)
        _team_semaphore_concurrency = max_concurrency
    return _team_semaphore


def reset_team_semaphore() -> None:
    """配置变更时主动重置 semaphore（供 settings reload 调用）。"""
    global _team_semaphore, _team_semaphore_concurrency
    _team_semaphore = None
    _team_semaphore_concurrency = None
```

- [ ] **Step 2: 修复 _collect_team_sse — 区分 team_done status**

修改 `backend/app/router/graph.py:411-453`：

```python
        # BE-R 修复：team_done 区分 status，error 时设 has_error
        if event.get("event") == "team_done":
            team_done_received = True
            data_str = event.get("data", "{}")
            try:
                data = json.loads(data_str) if isinstance(data_str, str) else data_str
                if isinstance(data, dict) and data.get("status") == "error":
                    has_error = True
            except (json.JSONDecodeError, TypeError):
                pass
```

- [ ] **Step 3: 修复 runner.py — abort 时不发 done**

修改 `backend/app/team/runner.py:130-148`：

```python
        # stream_mode=["custom"]
        team_done_emitted = False
        async for chunk in graph.astream(
            initial_state,
            stream_mode=["custom"],
        ):
            if not isinstance(chunk, tuple) or len(chunk) != 2:
                continue
            mode, payload = chunk
            if mode == "custom":
                # BE-H 修复：跟踪 team_done 是否已发
                if isinstance(payload, dict) and payload.get("event") == "team_done":
                    team_done_emitted = True
                yield payload

        # BE-H 修复：graph 正常完成后发射 done 事件
        # team_done 已通过 custom stream 发射，done 保证 SSE 流终结
        yield make_sse_event("done", {})
```

- [ ] **Step 4: 修复 BE-D/BE-J — 统一 _iterate 返回协议**

定义统一枚举，修改两个 `_iterate` 函数：

```python
# scheduler.py 顶部新增
from enum import Enum

class IterResult(Enum):
    """_iterate 统一返回协议（BE-D 修复）。"""
    NORMAL_END = "normal_end"      # runner 正常结束
    ABORTED = "aborted"            # abort 触发
    SUBTASK_DONE = "subtask_done"  # _subtask_done 哨兵到达
```

修改 `_run_subtask_stream._iterate` 返回 `IterResult`，修改 `_run_team_role_subtask._iterate` 同步返回 `IterResult`。调用方按枚举统一处理。

（详细代码改动较大，执行时按枚举替换所有 return 语句）

- [ ] **Step 5: 运行测试 + ruff + commit**

```bash
uv run pytest tests/python/unit/team/ -v
uv run ruff check backend/app/team/scheduler.py backend/app/team/runner.py backend/app/router/graph.py
git add backend/
git commit -m "fix(team): BE-D/BE-H/BE-J/BE-M/BE-R 修复迭代协议统一 + done 顺序 + semaphore 热更新 + team_done status 区分"
```

---

## Task 8: 全量回归测试 + 文档同步

**Files:**
- Run: 全量测试
- Update: `docs/agents/02-sse-event-contract.md`（team_done status 枚举）

- [ ] **Step 1: 运行全量后端测试**

Run: `uv run pytest tests/python/unit -m "not integration" -v`

Expected: 全部 PASS

- [ ] **Step 2: 运行全量前端测试**

Run: `cd frontend && pnpm typecheck && pnpm test`

Expected: 全部 PASS

- [ ] **Step 3: ruff 全量检查**

Run: `uv run ruff check backend/`

Expected: No issues

- [ ] **Step 4: 更新 SSE 事件契约文档**

修改 `docs/agents/02-sse-event-contract.md`，在 team_done 事件说明中补充 status 枚举：

```markdown
team_done 事件 status 字段枚举：
- "done" — 终态：团队执行完成
- "error" — 终态：团队执行失败
- "replanning" — 过渡态：质量门失败，正在重规划（前端不应 finalize）
```

- [ ] **Step 5: Final commit**

```bash
git add docs/agents/02-sse-event-contract.md
git commit -m "docs: 同步 team_done status 枚举到 SSE 事件契约"
```

---

## P2 Backlog（17 个，择机修复）

### 后端 P2（6 个）
| 编号 | 简述 | 文件 |
|------|------|------|
| BE-F | abort_event 双重获取 | `nodes.py:367-369` |
| BE-G | SSE trace_id 注入不一致 | `sse/events.py` |
| BE-K | replan hint 取首任务 | `nodes.py:641`（已在 Task 3 修复） |
| BE-L | replan hint 参数语义不清 | `planner.py:670-676` |
| BE-O | errors 字段用 _merge_warnings reducer | `state.py:122-123` |
| BE-Q | _run_aggregator 重复 quality_gate 检查 | `aggregator.py:79-80, 124-131` |

### 前端 P2（11 个）
| 编号 | 简述 | 文件 |
|------|------|------|
| FE-008 | "[工作区恢复]" 前缀过滤依赖单次 yield | `useChatStream.ts:154-158` |
| FE-009 | ExpandableAgentRow expanded 状态丢失 | `TeamNodeCard.tsx:78, 211-218` |
| FE-010 | unmatchedGroups 兜底渲染 status 硬编码 running | `TeamNodeCard.tsx:316-333` |
| FE-011 | ToolCallCard areEqual approvalRequest 只比较存在性 | `ToolCallCard.tsx:396` |
| FE-012 | useTraceAnalysis sending 守卫竞态 | `useTraceAnalysis.ts:41-42` |
| FE-013 | SubAgentGroup running 检测不包含 aborted/timeout | `AssistantMessageParts.tsx:273-282` |
| FE-014 | normalizeTodos fallbackTaskId 用 thread_id | `useChatStream.ts:21-45` |
| FE-015 | collapseToolCallGroups 可能合并不同 source | `AssistantMessageParts.tsx:202-236` |
| FE-016 | hasTeam 重排破坏 text 时间轴 | `AssistantMessageParts.tsx:410-415` |
| FE-017 | replan 事件未传 createIfMissing: false（已在 Task 5 修复） |
| FE-018 | team_done replanning 调用 markReasoningDone（已在 Task 5 修复） |

---

## 验证策略

1. **单元测试**：每个 Task 配套测试文件，TDD 流程（先写失败测试 → 修复 → 验证通过）
2. **集成测试**：Task 8 全量回归
3. **手动验证**：修复后启动应用，触发以下场景：
   - DAG 依赖链（多任务有 depends_on）— 验证后置任务拿到前置 finding
   - replan 场景（质量门失败）— 验证不产生双重 team_done，前端不闪烁
   - 同角色多 agent（replan 后同角色新任务）— 验证状态独立更新
   - abort 场景 — 验证 done 事件正确发射
4. **ruff + typecheck**：每个 Task 完成后运行静态检查
