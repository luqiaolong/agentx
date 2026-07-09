"""AgentTeam 共享黑板：保存每个专家的结果摘要与错误信息。

包含：
- ``TeamPlanTask``：Orchestrator 产出的单个子任务。
- ``TeamSubtaskResult``：单个子任务的执行结果。
- ``Blackboard``：AgentTeam 共享黑板（dataclass，向后兼容）。
- ``TeamState``：LangGraph ``StateGraph`` 状态（TypedDict），
  ``findings`` / ``errors`` / ``subtask_results`` 用 ``Annotated[dict, _merge_dict]``
  reducer 合并，支持并行 execute 节点返回部分 dict 自动归并。
- ``_merge_dict``：dict reducer（right 覆盖 left）。
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


class TeamState(TypedDict, total=False):
    """LangGraph StateGraph 状态：Team 路径 plan / execute / aggregate 节点共享。

    - ``findings`` / ``errors`` / ``subtask_results`` 使用 ``_merge_dict`` reducer，
      允许 execute 节点返回部分 dict 自动归并（支持并行子任务结果聚合）。
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


class SubtaskState(TypedDict, total=False):
    """单个子任务节点的状态（通过 ``langgraph.types.Send`` 注入）。

    - ``task``: 字典 ``{"agent": str, "input": str, "purpose": str}``，
      避免直接序列化 dataclass 对象，也兼容测试里的 MagicMock 任务。
    - ``task_index``: 子任务在 plan 中的序号，用于生成独立 child_thread_id。
    - ``parent_thread_id``: 父 thread_id，用于 abort 事件查找与 child 命名。
    - 其余字段透传自 ``TeamState``。
    """

    task: dict
    task_index: int
    parent_thread_id: str
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
