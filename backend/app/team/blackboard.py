"""AgentTeam 共享黑板：保存每个专家的结果摘要与错误信息。

包含：
- ``TeamPlanTask``：Orchestrator 产出的单个子任务。
- ``TeamSubtaskResult``：单个子任务的执行结果。
- ``TeamState``：LangGraph ``StateGraph`` 状态（TypedDict），
  ``findings`` / ``errors`` 用 ``Annotated[dict, _merge_dict]``
  reducer 合并，``todos`` 用 ``Annotated[list, _merge_todos]`` reducer 合并，
  支持并行 execute 节点返回部分 dict/list 自动归并。
- ``_merge_dict``：dict reducer（right 覆盖 left）。
- ``_merge_todos``：list reducer（按 ``_index`` 字段合并子任务 todo 状态更新）。
- ``_serialize_blackboard``：把 findings/errors 序列化为 Aggregator prompt 用的文本。

Phase 2 清理：删除 ``Blackboard`` dataclass（内联进 aggregator 入参）、
删除 ``subtask_results`` 死字段、``_merge_todos`` 粘性 in_progress。

本模块无运行时依赖（仅依赖 dataclasses + typing），可被 planner / scheduler /
aggregator / orchestrator 安全导入，不会产生循环依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Mapping, TypedDict

__all__ = [
    "TeamPlanTask",
    "TeamState",
    "SubtaskState",
    "TeamSubtaskResult",
    "_merge_dict",
    "_merge_todos",
    "_merge_pending_levels",
    "_merge_list",
    "_serialize_blackboard",
]


@dataclass
class TeamPlanTask:
    """Orchestrator 产出的单个子任务。

    ``deps`` 字段（DAG 依赖编排）声明本任务依赖的前置任务索引列表：
    ``deps=[0, 1]`` 表示本任务必须在 ``plan[0]`` 和 ``plan[1]`` 完成后执行。
    ``deps=[]`` 表示根任务，首批并行执行。
    """

    agent: str
    input: str
    purpose: str
    deps: list[int] = field(default_factory=list)


@dataclass
class TeamSubtaskResult:
    """单个子任务的执行结果。"""

    agent: str
    success: bool
    payload: str


def _merge_dict(left: dict, right: dict) -> dict:
    """reducer：合并两个 dict（right 覆盖 left）。

    LangGraph ``Annotated[dict, _merge_dict]`` 在并行节点返回时调用，
    把每个 execute 节点返回的部分 findings/errors 合并到全局 state。
    """
    result = dict(left or {})
    result.update(right or {})
    return result


def _merge_pending_levels(
    left: list[list[int]], right: list[list[int]]
) -> list[list[int]]:
    """reducer：合并 ``pending_levels``（right 非 None 时覆盖 left）。

    DAG 依赖编排场景：同一层并行子任务都会返回 ``pending_levels``。
    所有子任务返回相同的 ``remaining_levels`` 值（来自 ``_dispatch_batch_node``
    的 Send payload），因此覆盖是幂等的。

    使用 ``is not None`` 检查而非 truthiness：最后一层的 ``remaining_levels``
    为 ``[]``（空列表），truthiness 检查会忽略它导致 ``pending_levels`` 无法清空，
    造成无限循环。``is not None`` 检查确保空列表也能正确覆盖。
    """
    return right if right is not None else (left or [])


def _merge_list(left: list, right: list) -> list:
    """reducer：去重合并两个 list（保留顺序）。

    用于 ``completed_tasks``：并行子任务各自返回 ``[task_index]``，
    reducer 去重合并为已完成的任务索引列表。
    """
    result: list = list(left or [])
    for item in (right or []):
        if item not in result:
            result.append(item)
    return result


def _merge_todos(left: list[dict], right: list[dict]) -> list[dict]:
    """reducer：按 ``_index`` 字段合并子任务 todo 状态更新（粘性 in_progress）。

    LangGraph ``Annotated[list, _merge_todos]`` 在并行子任务节点返回时调用。
    ``right`` 每项可含 ``_index`` 字段定位 ``left`` 中对应 todo，合并 ``status``
    后移除 ``_index``。无 ``_index`` 或 index 越界时，``right`` 整体追加到 ``left``
    （Orchestrator 初始化场景）。

    粘性 in_progress（T3.1）：合并后若任一侧 status 为 ``in_progress`` 且新状态
    非 ``completed``/``error``，则保持 ``in_progress``。这避免并行子任务节点
    返回 ``pending`` 把已经标记 ``in_progress`` 的 todo 误降级回 ``pending``。

    典型用法：
    - ``_plan_node`` 返回 ``{"todos": [完整列表]}`` → 初始化所有 pending
    - 子任务节点返回 ``{"todos": [{"_index": 0, "status": "completed"}]}`` →
      更新 left[0].status 为 completed
    """
    result: list[dict] = [dict(t) for t in (left or [])]
    for item in (right or []):
        idx = item.get("_index")
        if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(result):
            # 无 _index 或越界：整体追加（Orchestrator 初始化场景）
            cleaned = {k: v for k, v in item.items() if k != "_index"}
            result.append(cleaned)
        else:
            # 按 _index 合并：更新对应 todo 的字段（status/content）
            target = dict(result[idx])
            for k, v in item.items():
                if k != "_index":
                    target[k] = v
            # 粘性 in_progress：left 或 right 任一为 in_progress 且新状态非终态
            # 则保持 in_progress，避免 pending 降级；
            # 但 left 已为终态时保持 left 终态，避免并行子任务 race condition
            # 把 completed/error 回退为 in_progress（review issue 3 修复）
            left_status = result[idx].get("status")
            right_status = target.get("status")
            terminal = {"completed", "error"}
            if left_status in terminal:
                target["status"] = left_status
            elif (left_status == "in_progress" or right_status == "in_progress") and right_status not in terminal:
                target["status"] = "in_progress"
            result[idx] = target
    return result


class TeamState(TypedDict, total=False):
    """LangGraph StateGraph 状态：Team 路径 plan / execute / aggregate 节点共享。

    - ``findings`` / ``errors`` 使用 ``_merge_dict`` reducer，
      允许 execute 节点返回部分 dict 自动归并（支持并行子任务结果聚合）。
    - ``todos`` 使用 ``_merge_todos`` reducer（粘性 in_progress），允许子任务节点
      返回部分 todo 更新（带 ``_index`` 字段）自动归并到全局 todo 列表。schema
      对齐 deepagents 原生 ``{content: str, status: "pending"|"in_progress"|"completed"}``。
    - ``pending_levels`` 使用 ``_merge_pending_levels`` reducer（DAG 依赖编排）：
      存储待执行的拓扑层级，每层一组任务索引。同一层并行子任务返回时，
      第一个设置 remaining_levels，其余返回空列表被 reducer 忽略。
    - ``completed_tasks`` 使用 ``_merge_list`` reducer（去重合并）：
      累积已完成的任务索引，用于 replan 时确定新任务的 after 引用。
    - ``replan_count``: replan 次数计数（防无限循环）。
    - ``team_semaphore`` / ``subtask_timeout``: Phase 1 稳定性硬化字段，
      运行时对象不参与 checkpoint 序列化（与 ``subtask_runners`` 同策略）。
    - 其余字段无 reducer，后续节点返回的同名字段会覆盖（LangGraph 默认行为）。
    - ``total=False`` 允许初始化时只传部分字段。
    """

    message: str
    thread_id: str
    history: list
    permission_mode: str
    scene_prompt: str | None
    profile_prompt: str
    workspace_path: str | None
    chat_model: Any
    subtask_runners: Any
    plan: list
    reasoning: str
    findings: Annotated[dict[str, str], _merge_dict]
    errors: Annotated[dict[str, str], _merge_dict]
    todos: Annotated[list[dict], _merge_todos]
    # DAG 依赖编排字段
    pending_levels: Annotated[list[list[int]], _merge_pending_levels]
    completed_tasks: Annotated[list[int], _merge_list]
    replan_count: int
    # Phase 1 稳定性硬化：运行时对象，不参与 checkpoint 序列化
    team_semaphore: Any  # asyncio.Semaphore，运行时在 run_team_path 入口创建
    subtask_timeout: int  # 子任务超时秒数，默认 300


class SubtaskState(TypedDict, total=False):
    """单个子任务节点的状态（通过 ``langgraph.types.Send`` 注入）。

    - ``task``: 字典 ``{"agent": str, "input": str, "purpose": str, "deps": list[int]}``，
      避免直接序列化 dataclass 对象，也兼容测试里的 MagicMock 任务。
      ``deps`` 字段为 DAG 依赖任务索引列表（DAG 依赖编排）。
    - ``task_index``: 子任务在 plan 中的序号，用于生成独立 child_thread_id
      和 ``_merge_todos`` reducer 定位 todo 项。
    - ``parent_thread_id``: 父 thread_id，用于 abort 事件查找与 child 命名。
    - ``todos``: 父状态 todos 快照（dispatch 时传入），供子任务节点构造
      ``todo_update`` SSE 事件时引用完整列表。
    - ``remaining_levels``: DAG 依赖编排：当前层执行完后剩余的待执行层。
      第一个子任务返回 ``pending_levels = remaining_levels``，其余返回空列表
      （由 ``_merge_pending_levels`` reducer 忽略）。
    - ``team_semaphore`` / ``subtask_timeout``: Phase 1 稳定性硬化字段，
      由 ``_dispatch_batch_node`` 从 ``TeamState`` 透传到各子任务节点。
    - 其余字段透传自 ``TeamState``。
    """

    task: dict
    task_index: int
    parent_thread_id: str
    todos: list[dict]
    remaining_levels: list[list[int]]
    history: list
    permission_mode: str
    scene_prompt: str | None
    profile_prompt: str
    workspace_path: str | None
    chat_model: Any
    subtask_runners: Any
    # Phase 1 稳定性硬化：由 _dispatch_batch_node 从 TeamState 透传
    team_semaphore: Any  # asyncio.Semaphore
    subtask_timeout: int  # 子任务超时秒数


def _serialize_blackboard(blackboard: Mapping) -> str:
    """把黑板内容序列化为 Aggregator prompt 用的文本。

    Phase 2 清理：删除 ``Blackboard`` dataclass 后，本函数只接受 Mapping
    （通常为 ``TeamState`` dict 或含 ``findings`` / ``errors`` key 的 dict）。

    Args:
        blackboard: 含 ``findings`` / ``errors`` key 的 Mapping。
    """
    findings = blackboard.get("findings", {})
    errors = blackboard.get("errors", {})
    lines: list[str] = []
    for agent_name, finding in findings.items():
        lines.append(f"--- {agent_name} ---")
        lines.append(finding)
    for agent_name, error in errors.items():
        lines.append(f"--- {agent_name} [失败] ---")
        lines.append(error)
    return "\n\n".join(lines)
