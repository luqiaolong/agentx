"""应用配置包：Settings + 子代理配置模型 + 场景化智能体配置 + 专家角色 prompt。

聚合导出 ``settings`` / ``subagents`` / ``agents`` / ``prompts`` 子模块，保持
``from app.config import get_settings, Settings, ...`` 向后兼容。
"""

from app.config.agents import (
    AgentsConfig,
    BUILTIN_EXPERT_KEYS,
    BUILTIN_TEAM_KEYS as BUILTIN_SCENARIO_TEAM_KEYS,
    ExpertSettings,
    ScenarioTeamSettings,
    SupervisorSettings,
    _default_agents_config,
    _parse_agents_config,
)
from app.config.settings import (
    BACKEND_ROOT,
    DATA_DIR,
    PROJECT_ROOT,
    UPLOADS_DIR,
    WORKSPACE_DIR,
    Settings,
    _default_tools_enabled,
    get_settings,
    reload_settings,
)
from app.config.subagents import (
    BUILTIN_SUBAGENT_KEYS,
    BUILTIN_TEAM_KEYS,
    FORBIDDEN_SUBAGENT_TOOLS,
    CustomSubagentEntry,
    SubagentSettings,
    _ALL_TOOLS,
    _default_subagents,
    _default_team_subagents,
    _parse_custom_subagents,
    _sanitize_custom_tools,
)

__all__ = [
    # settings
    "Settings",
    "get_settings",
    "reload_settings",
    "PROJECT_ROOT",
    "BACKEND_ROOT",
    "DATA_DIR",
    "WORKSPACE_DIR",
    "UPLOADS_DIR",
    "_default_tools_enabled",
    # subagents
    "SubagentSettings",
    "CustomSubagentEntry",
    "BUILTIN_SUBAGENT_KEYS",
    "BUILTIN_TEAM_KEYS",
    "FORBIDDEN_SUBAGENT_TOOLS",
    "_ALL_TOOLS",
    "_default_subagents",
    "_default_team_subagents",
    "_sanitize_custom_tools",
    "_parse_custom_subagents",
    # agents (场景化智能体)
    "AgentsConfig",
    "SupervisorSettings",
    "ExpertSettings",
    "ScenarioTeamSettings",
    "BUILTIN_EXPERT_KEYS",
    "BUILTIN_SCENARIO_TEAM_KEYS",
    "_default_agents_config",
    "_parse_agents_config",
]
