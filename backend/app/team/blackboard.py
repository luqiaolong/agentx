"""AgentTeam 共享黑板：保存每个专家的结果摘要与错误信息。

包含：
- ``TeamPlanTask``：Orchestrator 产出的单个子任务。
- ``TeamSubtaskResult``：单个子任务的执行结果。
- ``Blackboard``：AgentTeam 共享黑板，保存每个专家的结果摘要与错误信息。
- ``_serialize_blackboard``：把黑板内容序列化为 Aggregator prompt 用的文本。

本模块无运行时依赖（仅依赖 dataclasses），可被 planner / scheduler / aggregator /
orchestrator 安全导入，不会产生循环依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Blackboard",
    "TeamPlanTask",
    "TeamSubtaskResult",
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
    """AgentTeam 共享黑板：保存每个专家的结果摘要与错误信息。"""

    findings: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


def _serialize_blackboard(blackboard: Blackboard) -> str:
    lines: list[str] = []
    for agent_name, finding in blackboard.findings.items():
        lines.append(f"--- {agent_name} ---")
        lines.append(finding)
    for agent_name, error in blackboard.errors.items():
        lines.append(f"--- {agent_name} [失败] ---")
        lines.append(error)
    return "\n\n".join(lines)
