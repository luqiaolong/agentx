"""Team 包：场景级 AgentTeam。

AgentTeam 是统称（类型），当前唯一实例是 coding team。
复用现有 ``app.team`` 框架（orchestrator/scheduler/planner/aggregator/blackboard）。
AgentTeam 是场景的子模式（如 coding_team），不是顶层模式。
"""

from __future__ import annotations

from app.agents.team.coding_team import run_coding_team

__all__ = ["run_coding_team"]
