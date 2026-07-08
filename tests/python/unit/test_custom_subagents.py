"""自定义子代理（custom_subagents）后端逻辑单元测试。

覆盖：
1. _parse_custom_subagents 解析与 sanitize
2. Settings.custom_subagents 属性合并
3. 危险工具过滤（write_file / edit_file / shell_exec 必须被剔除）
4. key 与内置冲突时被拒绝

场景化架构下 ``select_subagent`` 路径 B 分发逻辑已删除，
本测试不再覆盖子代理选择逻辑（由 Supervisor/Expert 的 delegate_to_subagent 工具接管）。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.config import (
    BUILTIN_SUBAGENT_KEYS,
    FORBIDDEN_SUBAGENT_TOOLS,
    CustomSubagentEntry,
    _parse_custom_subagents,
    _sanitize_custom_tools,
    get_settings,
)


# ============================================================
# 辅助函数
# ============================================================


def _make_mock_settings_with_custom(
    subagents_overrides: dict | None = None,
    tools_overrides: dict | None = None,
    custom_subagents: dict | None = None,
) -> MagicMock:
    """构造 mock settings 对象，含 subagents / tools_enabled / custom_subagents 属性。"""
    from app.config import _default_subagents, _default_tools_enabled
    from app.config import SubagentSettings

    subagents = _default_subagents()
    for name, raw in (subagents_overrides or {}).items():
        if name in subagents and isinstance(raw, dict):
            merged = subagents[name].model_dump()
            merged.update(raw)
            subagents[name] = SubagentSettings(**merged)

    tools_enabled = _default_tools_enabled()
    tools_enabled.update(tools_overrides or {})

    mock = MagicMock(name="mock_settings")
    mock.subagents = subagents
    mock.tools_enabled = tools_enabled
    # custom_subagents 是 dict[str, CustomSubagentEntry]，直接传入
    mock.custom_subagents = custom_subagents or {}
    return mock


# ============================================================
# 1. _sanitize_custom_tools 工具过滤
# ============================================================


def test_sanitize_custom_tools_removes_dangerous() -> None:
    """危险工具（write_file/edit_file/shell_exec）必须被剔除。"""
    tools = ["read_file", "write_file", "edit_file", "shell_exec", "glob"]
    result = _sanitize_custom_tools(tools)
    assert "read_file" in result
    assert "glob" in result
    for forbidden in FORBIDDEN_SUBAGENT_TOOLS:
        assert forbidden not in result


def test_sanitize_custom_tools_removes_unknown() -> None:
    """未知工具名被剔除，仅保留 _ALL_TOOLS 中的安全工具。"""
    tools = ["read_file", "unknown_tool", "glob", "fake"]
    result = _sanitize_custom_tools(tools)
    assert result == ["read_file", "glob"]


def test_sanitize_custom_tools_dedup() -> None:
    """重复工具名去重保序。"""
    tools = ["read_file", "read_file", "glob", "glob"]
    result = _sanitize_custom_tools(tools)
    assert result == ["read_file", "glob"]


# ============================================================
# 2. _parse_custom_subagents 解析
# ============================================================


def test_parse_custom_subagents_basic() -> None:
    """正常解析自定义子代理 dict。"""
    raw = {
        "my_helper": {
            "key": "my_helper",
            "name": "我的助手",
            "systemPrompt": "you are helper",
            "tools": ["read_file", "glob"],
            "triggerDescription": "帮忙、助手",
        }
    }
    result = _parse_custom_subagents(raw)
    assert "my_helper" in result
    entry = result["my_helper"]
    assert isinstance(entry, CustomSubagentEntry)
    assert entry.key == "my_helper"
    assert entry.name == "我的助手"
    assert entry.enabled is True
    assert entry.temperature == 0.2
    assert entry.system_prompt == "you are helper"
    assert entry.tools == ["read_file", "glob"]
    assert entry.trigger_description == "帮忙、助手"


def test_parse_custom_subagents_rejects_builtin_key() -> None:
    """与内置 key 冲突的自定义 key 被拒绝。"""
    for builtin in BUILTIN_SUBAGENT_KEYS:
        raw = {builtin: {"name": "fake", "tools": []}}
        result = _parse_custom_subagents(raw)
        assert builtin not in result, f"内置 key {builtin} 不应被接受为自定义"


def test_parse_custom_subagents_filters_dangerous_tools() -> None:
    """解析时危险工具被剔除。"""
    raw = {
        "agent_a": {
            "key": "agent_a",
            "name": "A",
            "tools": ["read_file", "write_file", "edit_file"],
        }
    }
    result = _parse_custom_subagents(raw)
    entry = result["agent_a"]
    assert "read_file" in entry.tools
    assert "write_file" not in entry.tools
    assert "edit_file" not in entry.tools


def test_parse_custom_subagents_invalid_returns_empty() -> None:
    """非 dict / 非 dict value / 异常字段被静默跳过。"""
    raw = {
        "ok": {"name": "OK", "tools": ["read_file"]},
        "bad_value": "not a dict",
        "bad_temp": {"name": "T", "temperature": "not-a-number"},
    }
    result = _parse_custom_subagents(raw)
    assert "ok" in result
    assert "bad_value" not in result
    # bad_temp: temperature 转 float 失败应被跳过
    assert "bad_temp" not in result


def test_parse_custom_subagents_empty_key_rejected() -> None:
    """空 key 被拒绝。"""
    raw = {"": {"name": "Empty"}}
    result = _parse_custom_subagents(raw)
    assert result == {}


def test_parse_custom_subagents_non_dict_returns_empty() -> None:
    """raw 非 dict 时返回空。"""
    assert _parse_custom_subagents(None) == {}
    assert _parse_custom_subagents("not a dict") == {}
    assert _parse_custom_subagents([]) == {}


# ============================================================
# 3. Settings.custom_subagents 属性合并
# ============================================================


def test_custom_subagents_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """不设 AGENTX_CUSTOM_SUBAGENTS_CONFIG env，custom_subagents 返回空 dict。"""
    # conftest autouse 已清 env + cache
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.custom_subagents == {}


def test_custom_subagents_env_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    """env JSON 注入自定义子代理，custom_subagents 属性返回解析后的 dict。"""
    raw = {
        "my_agent": {
            "key": "my_agent",
            "name": "我的代理",
            "systemPrompt": "",
            "tools": ["read_file"],
            "triggerDescription": "帮我",
        }
    }
    monkeypatch.setenv(
        "AGENTX_CUSTOM_SUBAGENTS_CONFIG", json.dumps(raw)
    )
    get_settings.cache_clear()

    custom = get_settings().custom_subagents
    assert "my_agent" in custom
    entry = custom["my_agent"]
    assert entry.name == "我的代理"
    assert entry.tools == ["read_file"]


def test_custom_subagents_env_dangerous_tools_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env 注入的危险工具在 custom_subagents 属性中被过滤。"""
    raw = {
        "bad_agent": {
            "key": "bad_agent",
            "name": "Bad",
            "tools": ["read_file", "write_file", "edit_file", "shell_exec"],
        }
    }
    monkeypatch.setenv(
        "AGENTX_CUSTOM_SUBAGENTS_CONFIG", json.dumps(raw)
    )
    get_settings.cache_clear()

    custom = get_settings().custom_subagents
    entry = custom["bad_agent"]
    assert entry.tools == ["read_file"]
