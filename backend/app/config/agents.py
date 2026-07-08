"""场景化智能体配置模型：Supervisor / Expert / ScenarioTeam。

与 ``subagents.py`` 的区别：
- ``subagents.py`` 管理轻量工具代理（rag/web/custom）
- 本模块管理场景绑定的执行体：Supervisor（work 全能 agent）、Expert（场景专家）、ScenarioTeam（场景级团队）

配置通过 ``AGENTX_AGENTS_CONFIG`` 环境变量注入（JSON 字符串），结构：
{
  "supervisor": {"temperature": 0.3, "system_prompt": "...", ...},
  "experts": {"coding": {"temperature": 0.2, "system_prompt": "...", ...}},
  "teams": {"coding": {"enabled": true, "max_tasks": 5, ...}}
}
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.config.prompts.agent import (
    _DEFAULT_CODING_EXPERT_SYSTEM_PROMPT,
    _DEFAULT_SUPERVISOR_SYSTEM_PROMPT,
)

__all__ = [
    "SupervisorSettings",
    "ExpertSettings",
    "ScenarioTeamSettings",
    "AgentsConfig",
    "BUILTIN_EXPERT_KEYS",
    "BUILTIN_TEAM_KEYS",
    "_default_agents_config",
    "_parse_agents_config",
]

# Supervisor 默认完整工具集（fs 读写 + cli + git + rag + web）
_DEFAULT_SUPERVISOR_TOOLS: list[str] = [
    "read_file", "list_dir", "glob", "grep",
    "write_file", "edit_file",
    "web_search", "rag_retrieve",
    "git_status", "git_diff", "git_log", "git_branches",
    "git_clone", "git_pull", "git_checkout", "git_stage", "git_commit",
    "cli_execute",
]

# coding Expert 默认工具集（与 Supervisor 一致，Expert 需要完整代码工具）
_DEFAULT_CODING_EXPERT_TOOLS: list[str] = list(_DEFAULT_SUPERVISOR_TOOLS)

# 危险工具列表（触发 interrupt_before 审批流）
_DEFAULT_INTERRUPT_BEFORE_TOOLS: list[str] = [
    "write_file", "edit_file", "cli_execute",
    "git_clone", "git_pull", "git_checkout", "git_stage", "git_commit",
]

# 内置 Expert 键名集合（当前仅 coding，未来扩展 research/trading 等）
BUILTIN_EXPERT_KEYS: frozenset[str] = frozenset({"coding"})

# 内置场景级 Team 键名集合（当前仅 coding team）
BUILTIN_TEAM_KEYS: frozenset[str] = frozenset({"coding"})


class SupervisorSettings(BaseModel):
    """work 场景 Supervisor（全能 agent）配置。

    Supervisor 基于 ``create_react_agent`` 构建，自带完整工具集 +
    ``delegate_to_expert`` / ``delegate_to_subagent`` 委派工具。
    写操作走 ``interrupt_before`` 审批流。
    """

    enabled: bool = True
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=lambda: list(_DEFAULT_SUPERVISOR_TOOLS))
    interrupt_before_tools: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_INTERRUPT_BEFORE_TOOLS)
    )


class ExpertSettings(BaseModel):
    """场景绑定 Expert（专家 agent）配置。

    Expert 基于 ``build_deep_agent`` 构建，替换 system prompt 为领域专用。
    每个 Expert 绑定唯一场景，可调用 rag/web 子代理（通过 ``delegate_to_subagent``）。
    Expert 不可委派其他 Expert，也不可触发 AgentTeam。

    ``rubric`` 字段为可选自纠规则文本，透传到 ``build_deep_agent`` →
    ``create_agent``，非空时挂载 RubricMiddleware 启用运行时自纠。
    """

    enabled: bool = True
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=lambda: list(_DEFAULT_CODING_EXPERT_TOOLS))
    scenario: str = ""
    interrupt_before_tools: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_INTERRUPT_BEFORE_TOOLS)
    )
    rubric: str = ""
    grader_model: str | None = None


class ScenarioTeamSettings(BaseModel):
    """场景级 AgentTeam 配置。

    AgentTeam 是场景的子模式（如 ``coding_team``），复用现有 ``app.team`` 框架。
    work 场景无 team 模式。
    """

    enabled: bool = True
    max_tasks: int = Field(default=5, ge=1, le=10)
    max_parallel: int = Field(default=3, ge=1, le=5)
    result_max_chars: int = Field(default=2000, ge=500, le=8000)
    subtask_timeout: int = Field(default=300, ge=30, le=1800)


class AgentsConfig(BaseModel):
    """场景化智能体配置聚合模型。"""

    supervisor: SupervisorSettings = Field(default_factory=SupervisorSettings)
    experts: dict[str, ExpertSettings] = Field(default_factory=dict)
    teams: dict[str, ScenarioTeamSettings] = Field(default_factory=dict)

    @property
    def coding_team_enabled(self) -> bool:
        """coding_team 模式是否启用。"""
        team = self.teams.get("coding")
        return team is not None and team.enabled


def _default_agents_config() -> AgentsConfig:
    """返回默认场景化智能体配置。"""
    return AgentsConfig(
        supervisor=SupervisorSettings(
            system_prompt=_DEFAULT_SUPERVISOR_SYSTEM_PROMPT,
            tools=list(_DEFAULT_SUPERVISOR_TOOLS),
            interrupt_before_tools=list(_DEFAULT_INTERRUPT_BEFORE_TOOLS),
        ),
        experts={
            "coding": ExpertSettings(
                system_prompt=_DEFAULT_CODING_EXPERT_SYSTEM_PROMPT,
                tools=list(_DEFAULT_CODING_EXPERT_TOOLS),
                scenario="coding",
                interrupt_before_tools=list(_DEFAULT_INTERRUPT_BEFORE_TOOLS),
            ),
        },
        teams={
            "coding": ScenarioTeamSettings(
                enabled=True,
            ),
        },
    )


def _parse_agents_config(raw: Any) -> AgentsConfig:
    """从 env JSON 解析场景化智能体配置。

    - raw 为空或非 dict → 返回默认配置
    - raw 为 dict → 合并默认值，env 覆盖
    """
    from app.observability.logger import logger

    if not isinstance(raw, dict) or not raw:
        return _default_agents_config()

    try:
        defaults = _default_agents_config()

        # 解析 supervisor
        supervisor_raw = raw.get("supervisor", {})
        if isinstance(supervisor_raw, dict) and supervisor_raw:
            merged = defaults.supervisor.model_dump()
            merged.update(supervisor_raw)
            defaults.supervisor = SupervisorSettings(**merged)

        # 解析 experts
        experts_raw = raw.get("experts", {})
        if isinstance(experts_raw, dict):
            for name, expert_raw in experts_raw.items():
                if not isinstance(expert_raw, dict):
                    continue
                base = defaults.experts.get(name)
                if base is not None:
                    merged = base.model_dump()
                    merged.update(expert_raw)
                    defaults.experts[name] = ExpertSettings(**merged)
                else:
                    # 新增的自定义 Expert（未来扩展）
                    expert_raw.setdefault("scenario", name)
                    defaults.experts[name] = ExpertSettings(**expert_raw)

        # 解析 teams
        teams_raw = raw.get("teams", {})
        if isinstance(teams_raw, dict):
            for name, team_raw in teams_raw.items():
                if not isinstance(team_raw, dict):
                    continue
                base = defaults.teams.get(name)
                if base is not None:
                    merged = base.model_dump()
                    merged.update(team_raw)
                    defaults.teams[name] = ScenarioTeamSettings(**merged)
                else:
                    defaults.teams[name] = ScenarioTeamSettings(**team_raw)

        return defaults
    except Exception as exc:  # noqa: BLE001
        logger.warning("agents_config 解析失败，使用默认配置", error=str(exc))
        return _default_agents_config()
