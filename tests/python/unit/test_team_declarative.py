"""AgentTeam 声明式子代理化测试。

覆盖：
1. _build_team_experts_description 从 settings.team_subagents 生成描述
2. 描述内容随 settings.team_subagents 变化（增删/重命名）
3. _validate_task 使用 BUILTIN_TEAM_KEYS 而非硬编码集合
4. _validate_task 支持 settings 中新增的 team 角色
5. _validate_task 拒绝 disabled / 无工具绑定的 team 角色
6. _ORCHESTRATOR_SYSTEM_PROMPT 模板格式化后包含 team experts
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from app.config.subagents import (
    BUILTIN_TEAM_KEYS,
    SubagentSettings,
)
from app.team.planner import (
    _BASE_EXPERTS,
    _ORCHESTRATOR_SYSTEM_PROMPT,
    _build_project_context,
    _build_team_experts_description,
    _validate_task,
)
from app.team.blackboard import TeamPlanTask


def _make_settings(
    team_subagents: dict[str, SubagentSettings] | None = None,
    custom_subagents: dict[str, Any] | None = None,
    tools_enabled: dict[str, bool] | None = None,
) -> MagicMock:
    """构造 settings mock：仅暴露 _validate_task 关注的字段。"""
    s = MagicMock()
    s.team_subagents = team_subagents or {}
    s.subagents = {"rag": MagicMock(enabled=True, tools=["rag_retrieve"]),
                   "web": MagicMock(enabled=True, tools=["web_search"])}
    s.custom_subagents = custom_subagents or {}
    s.tools_enabled = tools_enabled or {
        "read_file": True, "list_dir": True, "glob": True, "grep": True,
        "rag_retrieve": True, "web_search": True,
    }
    return s


def test_build_team_experts_description_empty_returns_empty_string() -> None:
    """无 team 角色配置时返回空串，不应报错。"""
    settings = _make_settings(team_subagents={})
    result = _build_team_experts_description(settings)
    assert result == ""


def test_build_team_experts_description_uses_trigger_description() -> None:
    """从 settings.team_subagents 取 trigger_description 渲染。"""
    team = {
        "frontend_dev": SubagentSettings(
            enabled=True,
            trigger_description="前端开发专家，擅长 React/Vue/HTML/CSS",
        ),
        "backend_dev": SubagentSettings(
            enabled=True,
            trigger_description="后端开发专家，擅长 Python/Java/Go",
        ),
    }
    settings = _make_settings(team_subagents=team)
    result = _build_team_experts_description(settings)
    assert "frontend_dev" in result
    assert "前端开发专家" in result
    assert "backend_dev" in result
    assert "后端开发专家" in result


def test_build_team_experts_description_omits_disabled() -> None:
    """disabled=True 的 team 角色不应出现在描述中。"""
    team = {
        "frontend_dev": SubagentSettings(enabled=True, trigger_description="前端专家"),
        "architect": SubagentSettings(enabled=False, trigger_description="架构专家"),
    }
    settings = _make_settings(team_subagents=team)
    result = _build_team_experts_description(settings)
    assert "frontend_dev" in result
    assert "architect" not in result


def test_build_team_experts_description_supports_custom_team_role() -> None:
    """新增一个不在 BUILTIN_TEAM_KEYS 中的 team 角色应被支持。"""
    team = {
        "data_scientist": SubagentSettings(
            enabled=True,
            trigger_description="数据科学专家，擅长 SQL/统计/ML",
        ),
    }
    settings = _make_settings(team_subagents=team)
    result = _build_team_experts_description(settings)
    assert "data_scientist" in result
    assert "数据科学专家" in result


def test_orchestrator_system_prompt_includes_team_experts_when_formatted() -> None:
    """_ORCHESTRATOR_SYSTEM_PROMPT 格式化后应包含 _build_team_experts_description 输出。"""
    team = {
        "frontend_dev": SubagentSettings(
            enabled=True, trigger_description="前端专家"
        ),
    }
    settings = _make_settings(team_subagents=team)
    experts = _BASE_EXPERTS + "\n" + _build_team_experts_description(settings)
    prompt = _ORCHESTRATOR_SYSTEM_PROMPT.format(
        experts=experts,
        max_tasks=5,
        context=_build_project_context(),
    )
    # team experts 在 prompt 中
    assert "frontend_dev" in prompt
    assert "前端专家" in prompt
    # base experts 也在
    assert "code" in prompt
    assert "rag" in prompt
    # write_todos 工具使用说明
    assert "write_todos" in prompt
    assert "[agent:code]" in prompt


def test_validate_task_accepts_builtin_team_keys() -> None:
    """_validate_task 应接受 BUILTIN_TEAM_KEYS 中所有角色。"""
    settings = _make_settings()
    for team_key in BUILTIN_TEAM_KEYS:
        settings.team_subagents = {team_key: SubagentSettings(enabled=True, tools=["read_file"])}
        task = TeamPlanTask(agent=team_key, input="任务", purpose="")
        ok, err = _validate_task(task, settings)
        assert ok, f"{team_key} should be valid, got error: {err}"


def test_validate_task_accepts_custom_team_role_via_settings() -> None:
    """_validate_task 应接受 settings.team_subagents 中定义的任何角色（不再 hardcode 集合）。"""
    team = {
        "data_scientist": SubagentSettings(
            enabled=True, tools=["read_file", "list_dir"]
        ),
    }
    settings = _make_settings(team_subagents=team)
    task = TeamPlanTask(agent="data_scientist", input="分析数据", purpose="")
    ok, err = _validate_task(task, settings)
    assert ok
    assert err == ""


def test_validate_task_rejects_disabled_team_role() -> None:
    """team 角色 enabled=False 时 _validate_task 应拒绝。"""
    team = {
        "frontend_dev": SubagentSettings(enabled=False, tools=["read_file"]),
    }
    settings = _make_settings(team_subagents=team)
    task = TeamPlanTask(agent="frontend_dev", input="做前端", purpose="")
    ok, err = _validate_task(task, settings)
    assert not ok
    assert "禁用" in err or "未启用" in err


def test_validate_task_rejects_team_role_with_no_enabled_tools() -> None:
    """team 角色所有工具都被 tools_enabled 关闭时 _validate_task 应拒绝。"""
    team = {
        "frontend_dev": SubagentSettings(enabled=True, tools=["read_file"]),
    }
    settings = _make_settings(
        team_subagents=team,
        tools_enabled={"read_file": False},
    )
    task = TeamPlanTask(agent="frontend_dev", input="做前端", purpose="")
    ok, err = _validate_task(task, settings)
    assert not ok
    assert "禁用" in err or "不可用" in err


def test_validate_task_rejects_unknown_agent() -> None:
    """完全未知的 agent 类型应被拒绝。"""
    settings = _make_settings()
    task = TeamPlanTask(agent="ghost_role", input="...", purpose="")
    ok, err = _validate_task(task, settings)
    assert not ok
    assert "未知" in err or "不存在" in err
