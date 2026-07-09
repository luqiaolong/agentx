"""tools-config 后端逻辑单元测试。

覆盖：
1. tools_enabled 数据结构 (R1) — 工具字段名 + 默认全 true
2. env override 合并 (R4) — 部分覆盖 + 无效 JSON fallback
3. DeepAgent 工具过滤 (R5) — _make_deep_tools 按 tools_enabled 过滤
4. DANGEROUS_TOOLS 解耦 (R3) — 常量不变 + runtime_dangerous 交集计算
"""

from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.deepagent.agent import (
    DANGEROUS_TOOLS,
    _TOOL_NAME_MAP,
    _make_deep_tools,
)


# ============================================================
# 1. tools_enabled 数据结构 (R1)
# ============================================================


def test_tools_enabled_default_all_true() -> None:
    """不设 env，tools_enabled 返回全部工具全 true。"""
    settings = get_settings()
    tools = settings.tools_enabled
    assert len(tools) == 18
    assert all(tools.values())


def test_tools_enabled_field_names() -> None:
    """验证全部工具字段名。"""
    settings = get_settings()
    tools = settings.tools_enabled
    expected = {
        "read_file", "ls", "glob", "grep",
        "write_file", "edit_file", "delete_file",
        "web_search", "rag_retrieve",
        # Git
        "git_status", "git_diff", "git_log", "git_branches",
        "git_clone", "git_pull", "git_checkout", "git_stage", "git_commit",
    }
    assert set(tools.keys()) == expected


# ============================================================
# 2. env override 合并 (R4)
# ============================================================


def test_tools_enabled_partial_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """env JSON 只设 {"web_search": false}，其他保持 true。"""
    monkeypatch.setenv(
        "AGENTX_TOOLS_CONFIG",
        json.dumps({"web_search": False}),
    )
    get_settings.cache_clear()

    tools = get_settings().tools_enabled
    assert tools["web_search"] is False
    # 其他保持 true
    for name, enabled in tools.items():
        if name != "web_search":
            assert enabled is True


@pytest.mark.xfail(
    reason="backend bug: pydantic-settings v2 的 EnvSettingsSource 在 @field_validator "
    "运行前自动 json.loads 复杂类型字段，无效 JSON 直接抛 SettingsError，"
    "config._parse_json_env 的 fallback 到 {} 无法生效。需后端修复（如改用 "
    "str 字段 + validator 解析，或覆写 prepare_field_value）",
    raises=Exception,
    strict=True,
)
def test_tools_enabled_invalid_json_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 无效字符串，fallback 到全 true。

    期望行为：无效 JSON → _parse_json_env 返回空 dict → tools_enabled 全 true。
    实际行为：pydantic-settings 在 validator 之前抛 SettingsError（xfail）。
    """
    monkeypatch.setenv("AGENTX_TOOLS_CONFIG", "invalid-json{{{")
    get_settings.cache_clear()

    settings = get_settings()
    # 无效 JSON → _parse_json_env 应返回空 dict
    assert settings.tools_config == {}

    tools = settings.tools_enabled
    assert len(tools) == 10
    assert all(tools.values())


# ============================================================
# 3. DeepAgent 工具过滤 (R5)
# ============================================================


def test_make_deep_tools_filters_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """tools_enabled.web_search=false，_make_deep_tools 返回的列表不含 web_search。"""
    monkeypatch.setenv(
        "AGENTX_TOOLS_CONFIG",
        json.dumps({"web_search": False}),
    )
    get_settings.cache_clear()

    tools = _make_deep_tools("t1")
    tool_names = {t.name for t in tools}
    assert "web_search" not in tool_names
    # 其他工具仍在
    assert "rag_retrieve" in tool_names
    assert "delete_file" in tool_names
    assert "git_status" in tool_names
    # 内置 fs 工具不在 _make_deep_tools 输出中（由 backend 注入）
    assert "read_file" not in tool_names
    assert "write_file" not in tool_names


def test_make_deep_tools_all_enabled() -> None:
    """默认全启用，返回 delete_file + git + rag + web（内置 fs 工具由 backend 注入）。"""
    tools = _make_deep_tools("t1")
    # delete_file(1) + git(9) + rag(1) + web(1) = 12
    assert len(tools) == 12
    tool_names = {t.name for t in tools}
    expected = {
        "delete_file",
        "rag_retrieve",
        "web_search",
        # Git
        "git_status",
        "git_diff",
        "git_log",
        "git_branches",
        "git_clone",
        "git_pull",
        "git_checkout",
        "git_stage",
        "git_commit",
    }
    assert tool_names == expected


def test_make_deep_tools_all_disabled_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全禁用，返回空列表。"""
    monkeypatch.setenv(
        "AGENTX_TOOLS_CONFIG",
        json.dumps(
            {
                "read_file": False,
                "ls": False,
                "glob": False,
                "grep": False,
                "write_file": False,
                "edit_file": False,
                "delete_file": False,
                "web_search": False,
                "rag_retrieve": False,
                "git_status": False,
                "git_diff": False,
                "git_log": False,
                "git_branches": False,
                "git_clone": False,
                "git_pull": False,
                "git_checkout": False,
                "git_stage": False,
                "git_commit": False,
            }
        ),
    )
    get_settings.cache_clear()

    tools = _make_deep_tools("t1")
    assert len(tools) == 0


# ============================================================
# 4. DANGEROUS_TOOLS 解耦 (R3)
# ============================================================


def test_dangerous_tools_constant_unchanged() -> None:
    """DANGEROUS_TOOLS 常量始终包含 edit_file/write_file/delete_file 及 Git 写操作。

    execute 已移除，审批改为 directory_extension 机制。
    常量是模块级 frozenset，不随 tools_enabled 变化。
    即使工具被禁用，常量本身不变（运行时危险集合通过交集计算）。
    """
    assert DANGEROUS_TOOLS == {
        "edit_file",
        "write_file",
        "delete_file",
        "git_clone",
        "git_pull",
        "git_checkout",
        "git_stage",
        "git_commit",
    }


def test_runtime_dangerous_excludes_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """tools_enabled.delete_file=false，runtime_dangerous 不含 delete_file。

    runtime_dangerous = DANGEROUS_TOOLS ∩ enabled_tool_names
    （enabled_tool_names 来自 _make_deep_tools 返回的工具名集合）。

    注意：write_file/edit_file 由 backend 注入，不在 _make_deep_tools 输出中，
    故无 workspace_path 时 runtime_dangerous 不含它们（交集运算自动排除）。
    """
    monkeypatch.setenv(
        "AGENTX_TOOLS_CONFIG",
        json.dumps({"delete_file": False}),
    )
    get_settings.cache_clear()

    # 模拟 run_deep_path 中的 runtime_dangerous 计算
    agent_tools = _make_deep_tools("t1")
    enabled_tool_names = {_TOOL_NAME_MAP.get(t.name, t.name) for t in agent_tools}
    runtime_dangerous = DANGEROUS_TOOLS & enabled_tool_names

    assert "delete_file" not in runtime_dangerous
    assert "git_clone" in runtime_dangerous  # git_clone 仍启用


def test_runtime_dangerous_all_enabled() -> None:
    """默认全启用，runtime_dangerous = DANGEROUS_TOOLS ∩ 已启用工具名。

    注意：write_file/edit_file 由 backend 注入、不在 _make_deep_tools 返回的工具集中，
    故无 workspace_path 时 runtime_dangerous 不含它们（交集运算自动排除）。
    有 workspace_path 时由 run_deep_path 显式补充（见 agent.py）。
    """
    agent_tools = _make_deep_tools("t1")
    enabled_tool_names = {_TOOL_NAME_MAP.get(t.name, t.name) for t in agent_tools}
    runtime_dangerous = DANGEROUS_TOOLS & enabled_tool_names

    # _make_deep_tools 含 delete_file 及全部 Git 写操作；
    # write_file/edit_file 不在工具集中（由 backend 注入）
    assert runtime_dangerous == {
        "delete_file",
        "git_clone",
        "git_pull",
        "git_checkout",
        "git_stage",
        "git_commit",
    }
    assert "execute" not in runtime_dangerous
    assert "write_file" not in runtime_dangerous
    assert "edit_file" not in runtime_dangerous
