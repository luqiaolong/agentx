"""AgentTeam planner 任务校验测试。

覆盖：
1. _validate_task 对内置子代理（code/rag/web）的启用与工具状态校验
2. _validate_task 对团队角色子代理的启用与工具状态校验
3. _validate_task 对自定义子代理的存在性、启用状态、工具绑定状态校验
4. deep 子代理始终可用，未知 agent 类型返回错误
"""

from __future__ import annotations

from types import SimpleNamespace


from app.team.blackboard import TeamPlanTask
from app.team.planner import _validate_task


def _make_settings(
    *,
    code_enabled: bool = True,
    code_tools: list[str] | None = None,
    rag_tools: list[str] | None = None,
    custom: dict[str, dict] | None = None,
    team_enabled: bool = True,
    team_tools: list[str] | None = None,
    tools_enabled: dict[str, bool] | None = None,
) -> SimpleNamespace:
    """构造用于 _validate_task 的模拟 settings 对象。

    注: ``code_enabled`` 同时控制 rag/web 的 enabled 字段（mock 简化）。
    场景化架构下 code 子代理已由 coding Expert 取代，_validate_task 对
    ``agent="code"`` 直接返回 True，不再读取 settings.subagents["code"]。
    """
    code_tools = code_tools or ["read_file", "list_dir"]
    rag_tools = rag_tools or ["rag_retrieve"]
    team_tools = team_tools or ["read_file", "list_dir"]
    tools_enabled = tools_enabled or {}
    custom = custom or {}

    custom_entries = {
        key: SimpleNamespace(
            key=key,
            name=val.get("name", key),
            enabled=val.get("enabled", True),
            tools=val.get("tools", []),
        )
        for key, val in custom.items()
    }

    return SimpleNamespace(
        subagents={
            "rag": SimpleNamespace(enabled=code_enabled, tools=rag_tools),
            "web": SimpleNamespace(enabled=code_enabled, tools=["web_search"]),
        },
        team_subagents={
            "frontend_dev": SimpleNamespace(enabled=team_enabled, tools=team_tools),
        },
        custom_subagents=custom_entries,
        tools_enabled=tools_enabled,
    )


# ---- deep / 未知 agent ----


def test_validate_deep_agent_always_ok() -> None:
    """deep 子代理无需校验，始终可用。"""
    ok, err = _validate_task(TeamPlanTask(agent="deep", input="do it", purpose="test"), _make_settings())
    assert ok is True
    assert err == ""


def test_validate_unknown_agent_fails() -> None:
    """未知 agent 类型返回错误。"""
    ok, err = _validate_task(TeamPlanTask(agent="unknown", input="do it", purpose="test"), _make_settings())
    assert ok is False
    assert "未知 agent 类型" in err


# ---- 内置子代理 ----


def test_validate_builtin_enabled_with_enabled_tools_ok() -> None:
    """内置子代理（rag）启用且至少一个工具启用时可用。

    注: code 已映射到 coding Expert（场景化架构），_validate_task 直接返回 True，
    不走 subagents 配置校验，故用 rag 验证通用校验逻辑。
    """
    settings = _make_settings(tools_enabled={"rag_retrieve": True})
    ok, err = _validate_task(TeamPlanTask(agent="rag", input="检索", purpose="test"), settings)
    assert ok is True
    assert err == ""


def test_validate_builtin_disabled_fails() -> None:
    """内置子代理（rag）被禁用时不可用。"""
    settings = _make_settings(code_enabled=False)
    ok, err = _validate_task(TeamPlanTask(agent="rag", input="检索", purpose="test"), settings)
    assert ok is False
    assert "已禁用" in err


def test_validate_builtin_all_tools_disabled_fails() -> None:
    """内置子代理（rag）启用但所有绑定工具被禁用时不可用。"""
    settings = _make_settings(rag_tools=["rag_retrieve"], tools_enabled={"rag_retrieve": False})
    ok, err = _validate_task(TeamPlanTask(agent="rag", input="检索", purpose="test"), settings)
    assert ok is False
    assert "工具全部被禁用" in err


# ---- 团队角色子代理 ----


def test_validate_team_role_enabled_with_enabled_tools_ok() -> None:
    """团队角色启用且至少一个工具启用时可用。"""
    settings = _make_settings(tools_enabled={"read_file": True, "list_dir": False})
    ok, err = _validate_task(TeamPlanTask(agent="frontend_dev", input="ui", purpose="test"), settings)
    assert ok is True
    assert err == ""


def test_validate_team_role_disabled_fails() -> None:
    """团队角色被禁用时不可用。"""
    settings = _make_settings(team_enabled=False)
    ok, err = _validate_task(TeamPlanTask(agent="frontend_dev", input="ui", purpose="test"), settings)
    assert ok is False
    assert "已禁用" in err


def test_validate_team_role_all_tools_disabled_fails() -> None:
    """团队角色启用但所有绑定工具被禁用时不可用。"""
    settings = _make_settings(tools_enabled={"read_file": False, "list_dir": False})
    ok, err = _validate_task(TeamPlanTask(agent="frontend_dev", input="ui", purpose="test"), settings)
    assert ok is False
    assert "工具全部被禁用" in err


# ---- 自定义子代理 ----


def test_validate_custom_missing_fails() -> None:
    """自定义子代理不存在时返回错误。"""
    settings = _make_settings()
    ok, err = _validate_task(TeamPlanTask(agent="custom-mycoder", input="code", purpose="test"), settings)
    assert ok is False
    assert "不存在" in err


def test_validate_custom_disabled_fails() -> None:
    """自定义子代理被禁用时不可用。"""
    settings = _make_settings(
        custom={"mycoder": {"enabled": False, "tools": ["read_file"]}}
    )
    ok, err = _validate_task(TeamPlanTask(agent="custom-mycoder", input="code", purpose="test"), settings)
    assert ok is False
    assert "已禁用" in err


def test_validate_custom_no_tools_fails() -> None:
    """自定义子代理未绑定工具时不可用。"""
    settings = _make_settings(custom={"mycoder": {"enabled": True, "tools": []}})
    ok, err = _validate_task(TeamPlanTask(agent="custom-mycoder", input="code", purpose="test"), settings)
    assert ok is False
    assert "未绑定工具" in err


def test_validate_custom_all_tools_disabled_fails() -> None:
    """自定义子代理绑定工具全部被禁用时不可用。"""
    settings = _make_settings(
        custom={"mycoder": {"enabled": True, "tools": ["read_file", "list_dir"]}},
        tools_enabled={"read_file": False, "list_dir": False},
    )
    ok, err = _validate_task(TeamPlanTask(agent="custom-mycoder", input="code", purpose="test"), settings)
    assert ok is False
    assert "工具全部被禁用" in err


def test_validate_custom_at_least_one_enabled_tool_ok() -> None:
    """自定义子代理至少有一个绑定工具启用时可用。"""
    settings = _make_settings(
        custom={"mycoder": {"enabled": True, "tools": ["read_file", "list_dir"]}},
        tools_enabled={"read_file": True, "list_dir": False},
    )
    ok, err = _validate_task(TeamPlanTask(agent="custom-mycoder", input="code", purpose="test"), settings)
    assert ok is True
    assert err == ""
