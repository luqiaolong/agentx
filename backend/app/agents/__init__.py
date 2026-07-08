"""场景化智能体包：Supervisor / Expert / ScenarioTeam 三层执行体。

架构层次（代码实现术语）：
- Supervisor（work 场景全能 agent）：自带完整工具集 + delegate_to_expert 委派能力
- Expert（场景绑定专家）：coding Expert 为首个实现，基于 build_deep_agent
- AgentTeam（场景级团队）：coding team 为首个实现，复用 app.team 框架
- Subagent（rag/web/custom）：轻量工具代理，被 Supervisor/Expert 调用

外部展示术语（场景）：work / coding / coding_team
"""

from __future__ import annotations

__all__: list[str] = []
