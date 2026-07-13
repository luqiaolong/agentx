"""AgentTeam v2 Dispatcher：Kahn wave 解析 + Send fan-out（T4）。

本模块负责 DAG 依赖编排的派发逻辑：

- ``resolve_waves``: Kahn 算法按 ``depends_on`` 拓扑分层，层内并行、层间串行。
- ``dispatch_node``: LangGraph 节点函数（无状态更新），实际 fan-out 由
  ``_dispatch_router`` 条件边完成。
- ``_dispatch_router``: 取 ``pending_waves[0]`` 当前层任务，为每个任务构造
  ``Send("execute", SubtaskState)``，传递 ``remaining_waves`` 供 execute 节点
  通过 ``_merge_pending_waves`` reducer 弹出当前层。
- ``_inject_upstream_findings``: 根据 ``task.depends_on`` 从 ``state.findings``
  提取依赖任务的结果（D4）。
- ``_compose_input_with_upstream``: 把上游 findings 拼入 agent context，
  每个依赖内容截断到 ``team_result_max_chars``（D4）。

与旧 ``orchestrator._dispatch_batch_node`` 的差异：
- 使用 ``TeamTask``（含 ``id`` / ``depends_on`` str 列表）替代 ``TeamPlanTask``
  （含 ``deps`` int 列表），引用改为按 task id 而非 list index。
- findings 类型从 ``dict[str, str]`` 改为 ``dict[str, Finding]``（结构化）。
- ``_inject_dependency_context`` 拆为 ``_inject_upstream_findings`` +
  ``_compose_input_with_upstream`` 双函数（注入 + 拼接分离）。
"""

from __future__ import annotations

from typing import Any

from langgraph.types import Send

from app.config import get_settings
from app.observability.logger import logger
from app.team.state import Finding, TeamState, TeamTask

__all__ = [
    "resolve_waves",
    "dispatch_node",
    "_dispatch_router",
    "_inject_upstream_findings",
    "_compose_input_with_upstream",
    "build_dispatch_sends",
]


# ============================================================
# Kahn 算法拓扑分层
# ============================================================


def resolve_waves(tasks: list[TeamTask]) -> list[list[TeamTask]]:
    """Kahn 算法按 ``depends_on`` 拓扑分层（D3）。

    层内任务可并行执行，层间串行。所有任务无 ``depends_on`` 时返回单层
    （等价并行 fan-out）。

    边界处理：
    - 越界 ``dep_id``（不在 tasks 中）丢弃 + ``logger.warning``
    - 自环（``dep_id == task.id``）丢弃 + ``logger.warning``
    - 循环检测：入度为 0 的节点为空时，强加入度最小的节点打破循环 + ``logger.error``

    Args:
        tasks: ``TeamTask`` 列表（含 ``depends_on`` 字段，str task id 引用）。

    Returns:
        拓扑分层结果，每层一组 ``TeamTask``（``list[list[TeamTask]]``）。
    """
    n = len(tasks)
    if n == 0:
        return []

    id_to_idx: dict[str, int] = {t.id: i for i, t in enumerate(tasks)}

    in_degree = [0] * n
    dependents: list[list[int]] = [[] for _ in range(n)]

    for i, task in enumerate(tasks):
        for dep_id in task.depends_on:
            if dep_id not in id_to_idx:
                logger.warning(
                    "team DAG dep_id not found, dropping",
                    dep_id=dep_id,
                    task_id=task.id,
                )
                continue
            if dep_id == task.id:
                logger.warning(
                    "team DAG self-loop, dropping",
                    task_id=task.id,
                    dep_id=dep_id,
                )
                continue
            dep_idx = id_to_idx[dep_id]
            dependents[dep_idx].append(i)
            in_degree[i] += 1

    waves: list[list[TeamTask]] = []
    remaining = set(range(n))

    while remaining:
        current = [i for i in sorted(remaining) if in_degree[i] == 0]
        if not current:
            min_indeg = min(in_degree[i] for i in remaining)
            current = [i for i in sorted(remaining) if in_degree[i] == min_indeg]
            for i in current:
                logger.error(
                    "team DAG cycle detected, breaking by force",
                    task_id=tasks[i].id,
                    depends_on=tasks[i].depends_on,
                )
        waves.append([tasks[i] for i in current])
        for i in current:
            remaining.discard(i)
            for j in dependents[i]:
                in_degree[j] -= 1

    return waves


# ============================================================
# Upstream findings 注入
# ============================================================


def _inject_upstream_findings(
    task: TeamTask, findings: dict[str, Finding]
) -> dict[str, Finding]:
    """根据 ``task.depends_on`` 从 ``state.findings`` 提取上游结果（D4）。

    遍历 ``findings`` 的 value（``Finding`` 对象），匹配 ``finding.task_id``
    是否在 ``task.depends_on`` 中。未找到时 ``logger.warning``。

    Args:
        task: 当前要执行的子任务。
        findings: 全局 ``state.findings``（``dict[str, Finding]``）。

    Returns:
        上游 findings 子集（``dict[str, Finding]``），key 为 findings 全局 key。
    """
    if not task.depends_on:
        return {}

    upstream: dict[str, Finding] = {}
    dep_ids = set(task.depends_on)
    for key, finding in findings.items():
        if finding.task_id in dep_ids:
            upstream[key] = finding

    found_ids = {f.task_id for f in upstream.values()}
    missing = dep_ids - found_ids
    if missing:
        logger.warning(
            "team dispatch upstream finding missing",
            task_id=task.id,
            missing_deps=list(missing),
        )
    return upstream


def _compose_input_with_upstream(
    task: TeamTask, upstream: dict[str, Finding]
) -> str:
    """把上游 findings 拼入 agent context（D4）。

    无上游时原样返回 ``task.description``。有上游时构造 ``[依赖任务结果]``
    头部 + 每个依赖的 ``--- #task_id [agent] ---`` + 内容（截断到
    ``team_result_max_chars``）+ ``[当前任务]`` + 原描述。

    Args:
        task: 当前要执行的子任务。
        upstream: 上游 findings（``dict[str, Finding]``）。

    Returns:
        注入上游上下文后的 input 字符串。
    """
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


# ============================================================
# LangGraph dispatch 节点 + 条件边路由
# ============================================================


def dispatch_node(state: TeamState) -> dict:
    """DAG 分批派发节点（入口）：无状态更新，fan-out 由 ``_dispatch_router`` 完成。

    LangGraph 0.5+ 要求节点返回 ``dict``（状态更新），``list[Send]`` 由
    ``add_conditional_edges`` 的 path function 返回。本节点返回空 dict，
    实际 fan-out 逻辑在 ``_dispatch_router`` 中。

    wave 弹出策略：execute 节点通过 ``_merge_pending_waves`` reducer 返回
    ``remaining_waves``，第一个完成的 execute 节点设置 ``pending_waves``，
    其余返回相同值被 reducer 幂等覆盖。
    """
    return {}


def build_dispatch_sends(state: TeamState) -> list[Send]:
    """构造当前 wave 的 ``Send`` 列表（核心 fan-out 逻辑）。

    取 ``pending_waves[0]`` 当前层任务，为每个任务构造 ``Send("execute", SubtaskState)``：
    - 调用 ``_inject_upstream_findings`` 提取依赖结果
    - 传递 ``remaining_waves`` 供 execute 节点弹出当前层
    - 透传 ``chat_model`` / ``subtask_runners`` / ``team_semaphore`` 等运行时对象

    ``pending_waves`` 为空时返回 ``[Send("aggregate", {})]``，确保 graph
    路由到 aggregate 节点。

    BE-A 修复：``wave_index`` 使用 ``replan_count * 100 + len(completed_task_ids)``
    作为启发式，避免 replan 后 findings key 语义错乱（replan_count 单独无法区分
    同一轮 replan 内的多个 wave）。

    Args:
        state: Team 路径全局 state。

    Returns:
        当前层每个任务一个 ``Send("execute", SubtaskState)``。
    """
    pending_waves = state.get("pending_waves", [])
    if not pending_waves:
        return [Send("aggregate", {})]

    current_wave = pending_waves[0]
    remaining_waves = pending_waves[1:]

    findings = state.get("findings", {})
    replan_count = state.get("replan_count", 0)
    # BE-A 修复：wave_index 用 completed_task_ids 长度推断已完成 wave 数
    # （更准确的方式是在 state 增加 wave_index 字段，但当前用启发式避免 schema 大改）
    completed_task_ids = state.get("completed_task_ids", [])
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
                    "subtask_timeout": state.get("subtask_timeout", 600),
                    "abort_event": state.get("abort_event"),
                    "replan_count": replan_count,
                },
            )
        )
    return sends


def _dispatch_router(state: TeamState) -> list[Send]:
    """DAG 分批派发路由函数（条件边 path function）。

    委托 ``build_dispatch_sends`` 构造 ``Send`` 列表。LangGraph 在
    ``add_conditional_edges("dispatch", _dispatch_router)`` 时调用本函数，
    返回的 ``Send`` 列表触发 ``execute`` 节点并行 fan-out。
    """
    return build_dispatch_sends(state)


# ============================================================
# 屏蔽未使用的类型导入（TypedDict 运行时不需要 Any）
# ============================================================

# Any 用于 SubtaskState 的 chat_model 等字段类型，本模块不直接使用
_ = Any
