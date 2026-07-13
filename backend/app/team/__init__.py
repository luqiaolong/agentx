"""路径 D：AgentTeam v2 多代理协作。

v2 模块组成：
- ``state``: TeamTask / TeamPlan / Finding / TeamState / SubtaskState schema
- ``blackboard``: reducers + v1 兼容类型（TeamPlanTask / TeamSubtaskResult）
- ``planner``: Planner 类（structured output + fallback）
- ``dispatcher``: Kahn 分层 + Send fan-out
- ``nodes``: 6 节点函数（plan/dispatch/execute/barrier/aggregate/replan）+ 路由
- ``graph_builder``: StateGraph 拓扑构建
- ``runner``: run_team_path 入口
- ``scheduler``: Semaphore 限流 + retry + abort cancel
- ``aggregator``: 质量门 + aggregator
"""

from app.team.blackboard import TeamPlanTask, TeamSubtaskResult
from app.team.runner import run_team_path

__all__ = ["run_team_path", "TeamPlanTask", "TeamSubtaskResult"]
