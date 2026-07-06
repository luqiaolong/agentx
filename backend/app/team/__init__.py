"""路径 D：AgentTeam 多代理协作。"""

from app.team.orchestrator import (
    Blackboard,
    TeamPlanTask,
    TeamSubtaskResult,
    run_team_path,
)

__all__ = ["run_team_path", "Blackboard", "TeamPlanTask", "TeamSubtaskResult"]
