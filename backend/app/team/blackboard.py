"""AgentTeam v2 共享黑板：子任务类型 + v2 reducers + 序列化。

v2 重构后本模块仅保留：
- ``TeamPlanTask``：v1 兼容子任务类型（scheduler/nodes/planner 仍使用）
- ``TeamSubtaskResult``：子任务执行结果（scheduler/nodes 仍使用）
- v2 reducers（``_merge_findings`` / ``_merge_warnings`` /
  ``_merge_pending_waves`` / ``_merge_completed_task_ids``）：供 state.py 使用
- ``_serialize_blackboard``：aggregator.py 使用

v1 ``TeamState`` / ``SubtaskState`` / ``_merge_dict`` / ``_merge_todos`` /
``_merge_pending_levels`` / ``_merge_list`` 已随 orchestrator.py 删除清理。

本模块无运行时依赖（仅依赖 dataclasses + typing），可被任何 v2 模块安全导入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

__all__ = [
    "TeamPlanTask",
    "TeamSubtaskResult",
    # v2 reducers（T2/T37）——供 state.py 使用
    "_merge_findings",
    "_merge_warnings",
    "_merge_pending_waves",
    "_merge_completed_task_ids",
    "_serialize_blackboard",
]


@dataclass
class TeamPlanTask:
    """Orchestrator 产出的单个子任务（v1 兼容类型，scheduler/planner 仍使用）。

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
    """单个子任务的执行结果。

    ``retries`` 字段（O4）记录实际重试次数，由 ``run_with_retry`` 填充，
    写入 ``Finding.retries`` 供前端展示「该子任务重试了 N 次才成功」。
    默认 0 表示未发生重试。
    """

    agent: str
    success: bool
    payload: str
    retries: int = 0


# ============================================================
# v2 reducers（T2/T37）——供 state.py TeamState(v2) 使用
# ============================================================


def _merge_findings(left: dict, right: dict) -> dict:
    """v2 reducer：合并 ``dict[str, Finding]``（right 覆盖 left）。

    并行 execute 节点返回部分 ``{key: Finding}`` 时合并到全局 state。
    """
    result = dict(left or {})
    result.update(right or {})
    return result


def _merge_warnings(left: list[str], right: list[str]) -> list[str]:
    """v2 reducer：合并 ``list[str]``（拼接去重，保序）。

    用于 ``state.warnings`` channel（D14）和 ``state.errors``。
    危险任务改写 / fallback 触发 / team_role 缺配置等场景写入 warning，
    ``aggregate_node`` 统一发射 SSE。
    """
    seen: set[str] = set(left or [])
    merged: list[str] = list(left or [])
    for w in (right or []):
        if w not in seen:
            merged.append(w)
            seen.add(w)
    return merged


def _merge_pending_waves(left: list, right: list) -> list:
    """v2 reducer：合并 ``pending_waves: list[list[TeamTask]]``（right 覆盖 left）。

    DAG 多波次场景：``dispatch_node`` 弹出当前 wave 后设置剩余 waves。
    使用 ``is not None`` 检查，确保空列表 ``[]``（最后一层）也能正确覆盖。
    """
    return right if right is not None else (left or [])


def _merge_completed_task_ids(left: list[str], right: list[str]) -> list[str]:
    """v2 reducer：合并 ``completed_task_ids``（去重合并，保序）。

    用 ``list[str]`` 而非 ``set[str]`` 以兼容 LangGraph checkpoint
    JSON 序列化（set 不可 JSON 序列化）。replan 时引用已完成 task。
    """
    result: list[str] = list(left or [])
    for tid in (right or []):
        if tid not in result:
            result.append(tid)
    return result


def _serialize_blackboard(blackboard: Mapping) -> str:
    """把黑板内容序列化为 Aggregator prompt 用的文本。

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
