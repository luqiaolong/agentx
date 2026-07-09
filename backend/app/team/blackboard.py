"""AgentTeam 共享黑板：保存每个专家的结果摘要与错误信息。

包含：
- ``TeamPlanTask``：Orchestrator 产出的单个子任务。
- ``TeamSubtaskResult``：单个子任务的执行结果。
- ``Blackboard``：AgentTeam 共享黑板（dataclass，向后兼容）。
- ``TeamState``：LangGraph ``StateGraph`` 状态（TypedDict），
  ``findings`` / ``errors`` / ``subtask_results`` 用 ``Annotated[dict, _merge_dict]``
  reducer 合并，``todos`` 用 ``Annotated[list, _merge_todos]`` reducer 合并，
  支持并行 execute 节点返回部分 dict/list 自动归并。
- ``_merge_dict``：dict reducer（right 覆盖 left）。
- ``_merge_todos``：list reducer（按 ``_index`` 字段合并子任务 todo 状态更新）。
- ``_serialize_blackboard``：把黑板内容序列化为 Aggregator prompt 用的文本，
  同时接受 ``Blackboard`` 实例与 ``TeamState`` dict（duck-typed）。

本模块无运行时依赖（仅依赖 dataclasses + typing），可被 planner / scheduler /
aggregator / orchestrator 安全导入，不会产生循环依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Mapping, TypedDict

__all__ = [
    "Blackboard",
    "TeamPlanTask",
    "TeamState",
    "SubtaskState",
    "TeamSubtaskResult",
    "_merge_dict",
    "_merge_todos",
    "_serialize_blackboard",
]


@dataclass
class TeamPlanTask:
    """Orchestrator 产出的单个子任务。"""

    agent: str
    input: str
    purpose: str


@dataclass
class TeamSubtaskResult:
    """单个子任务的执行结果。"""

    agent: str
    success: bool
    payload: str


@dataclass
class Blackboard:
    """AgentTeam 共享黑板：保存每个专家的结果摘要与错误信息。

    LangGraph 迁移后仍保留，用于 Aggregator 的入参（向后兼容）。
    新的 StateGraph 路径使用 ``TeamState`` dict；``aggregate_node`` 会把
    ``TeamState`` 转成 ``Blackboard`` 再喂给 ``_run_aggregator``。
    """

    findings: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


def _merge_dict(left: dict, right: dict) -> dict:
    """reducer：合并两个 dict（right 覆盖 left）。

    LangGraph ``Annotated[dict, _merge_dict]`` 在并行节点返回时调用，
    把每个 execute 节点返回的部分 findings/errors 合并到全局 state。
    """
    result = dict(left or {})
    result.update(right or {})
    return result


def _merge_todos(left: list[dict], right: list[dict]) -> list[dict]:
    """reducer：按 ``_index`` 字段合并子任务 todo 状态更新。

    LangGraph ``Annotated[list, _merge_todos]`` 在并行子任务节点返回时调用。
    ``right`` 每项可含 ``_index`` 字段定位 ``left`` 中对应 todo，合并 ``status``
    后移除 ``_index``。无 ``_index`` 或 index 越界时，``right`` 整体追加到 ``left``
    （Orchestrator 初始化场景）。

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
            result[idx] = target
    return result


class TeamState(TypedDict, total=False):
    """LangGraph StateGraph 状态：Team 路径 plan / execute / aggregate 节点共享。

    - ``findings`` / ``errors`` / ``subtask_results`` 使用 ``_merge_dict`` reducer，
      允许 execute 节点返回部分 dict 自动归并（支持并行子任务结果聚合）。
    - ``todos`` 使用 ``_merge_todos`` reducer，允许子任务节点返回部分 todo 更新
      （带 ``_index`` 字段）自动归并到全局 todo 列表。schema 对齐 deepagents 原生
      ``{content: str, status: "pending"|"in_progress"|"completed"}``。
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
    subtask_results: Annotated[dict[str, dict], _merge_dict]
    todos: Annotated[list[dict], _merge_todos]


class SubtaskState(TypedDict, total=False):
    """单个子任务节点的状态（通过 ``langgraph.types.Send`` 注入）。

    - ``task``: 字典 ``{"agent": str, "input": str, "purpose": str}``，
      避免直接序列化 dataclass 对象，也兼容测试里的 MagicMock 任务。
    - ``task_index``: 子任务在 plan 中的序号，用于生成独立 child_thread_id
      和 ``_merge_todos`` reducer 定位 todo 项。
    - ``parent_thread_id``: 父 thread_id，用于 abort 事件查找与 child 命名。
    - ``todos``: 父状态 todos 快照（dispatch 时传入），供子任务节点构造
      ``todo_update`` SSE 事件时引用完整列表。
    - 其余字段透传自 ``TeamState``。
    """

    task: dict
    task_index: int
    parent_thread_id: str
    todos: list[dict]
    history: list
    permission_mode: str
    scene_prompt: str | None
    profile_prompt: str
    workspace_path: str | None
    chat_model: Any
    subtask_runners: Any


def _serialize_blackboard(blackboard: Blackboard | Mapping) -> str:
    """把黑板内容序列化为 Aggregator prompt 用的文本。

    Duck-typed：同时接受 ``Blackboard`` dataclass（``.findings`` / ``.errors``
    属性）与 ``TeamState`` dict（``["findings"]`` / ``["errors"]`` key）。

    Args:
        blackboard: ``Blackboard`` 实例或含 ``findings`` / ``errors`` key 的 Mapping。
    """
    findings = (
        blackboard.findings
        if isinstance(blackboard, Blackboard)
        else blackboard.get("findings", {})
    )
    errors = (
        blackboard.errors
        if isinstance(blackboard, Blackboard)
        else blackboard.get("errors", {})
    )
    lines: list[str] = []
    for agent_name, finding in findings.items():
        lines.append(f"--- {agent_name} ---")
        lines.append(finding)
    for agent_name, error in errors.items():
        lines.append(f"--- {agent_name} [失败] ---")
        lines.append(error)
    return "\n\n".join(lines)
