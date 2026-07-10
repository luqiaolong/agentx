"""subagents-config 后端逻辑单元测试。

覆盖：
1. SubagentSettings 数据结构 (R1)
2. env override 合并逻辑 (R3) — 字段级合并，无效 JSON fallback
3. 工具集动态过滤 (R4) — _make_fs_tools / _make_rag_tools 按 tools_enabled 过滤
4. temperature clamp (R7) — 前端实现，后端 skip

场景化架构（Supervisor + Expert）下，code 子代理已被 coding Expert 取代，
路径 B（SINGLE_TOOL）分发逻辑已删除。本文件不再测试 select_subagent 相关逻辑。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.config import SubagentSettings, get_settings


# ============================================================
# 辅助函数
# ============================================================


def _make_mock_settings(
    subagents_overrides: dict | None = None,
    tools_overrides: dict | None = None,
) -> MagicMock:
    """构造 mock settings 对象，返回带 subagents/tools_enabled 属性的 MagicMock。

    默认值与 ``config._default_subagents()`` / ``_default_tools_enabled()`` 一致，
    通过 ``subagents_overrides`` / ``tools_overrides`` 做字段级覆盖。
    """
    from app.config import _default_subagents, _default_tools_enabled

    # 构建子代理配置（字段级覆盖）
    subagents = _default_subagents()
    for name, raw in (subagents_overrides or {}).items():
        if name in subagents and isinstance(raw, dict):
            merged = subagents[name].model_dump()
            merged.update(raw)
            subagents[name] = SubagentSettings(**merged)

    # 构建工具配置（顶层覆盖）
    tools_enabled = _default_tools_enabled()
    tools_enabled.update(tools_overrides or {})

    mock = MagicMock(name="mock_settings")
    mock.subagents = subagents
    mock.tools_enabled = tools_enabled
    return mock


# ============================================================
# 1. SubagentSettings 数据结构 (R1)
# ============================================================


def test_subagent_settings_fields() -> None:
    """SubagentSettings 实例化，验证 5 个字段类型正确。"""
    cfg = SubagentSettings(
        enabled=True,
        temperature=0.5,
        system_prompt="you are a coder",
        tools=["read_file"],
        trigger_description="用户问题涉及写代码时触发。",
    )
    assert isinstance(cfg.trigger_description, str)
    assert cfg.trigger_description == "用户问题涉及写代码时触发。"
    assert isinstance(cfg.enabled, bool)
    assert cfg.enabled is True
    assert isinstance(cfg.temperature, float)
    assert cfg.temperature == 0.5
    assert isinstance(cfg.system_prompt, str)
    assert cfg.system_prompt == "you are a coder"
    assert isinstance(cfg.tools, list)
    assert cfg.tools == ["read_file"]


def test_subagent_settings_defaults() -> None:
    """SubagentSettings 默认值。"""
    cfg = SubagentSettings()
    assert cfg.enabled is True
    assert cfg.temperature == 0.2
    assert cfg.system_prompt == ""
    assert cfg.tools == []
    assert cfg.trigger_description == ""


# ============================================================
# 2. env override 合并 (R3)
# ============================================================


def test_subagents_default_when_no_env() -> None:
    """不设 AGENTX_SUBAGENTS_CONFIG env，返回默认值（rag/web 两键齐全）。"""
    # conftest autouse fixture 已清 env + cache
    settings = get_settings()
    subagents = settings.subagents
    assert set(subagents.keys()) == {"rag", "web"}

    # rag 默认值
    assert subagents["rag"].enabled is True
    assert subagents["rag"].temperature == 0.2
    assert subagents["rag"].tools == ["rag_retrieve"]
    assert "知识库" in subagents["rag"].trigger_description

    # web 默认值
    assert subagents["web"].enabled is True
    assert subagents["web"].tools == ["web_search"]
    assert "互联网" in subagents["web"].trigger_description


def test_subagents_env_partial_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """env JSON 只设 rag.temperature=0.5，其他字段保持默认值（字段级合并）。"""
    monkeypatch.setenv(
        "AGENTX_SUBAGENTS_CONFIG",
        json.dumps({"rag": {"temperature": 0.5}}),
    )
    get_settings.cache_clear()

    subagents = get_settings().subagents

    # rag.temperature 被覆盖
    assert subagents["rag"].temperature == 0.5
    # rag 其他字段保持默认（字段级合并）
    assert subagents["rag"].enabled is True
    # system_prompt 有默认长文本，不校验具体内容
    assert subagents["rag"].system_prompt != ""
    assert subagents["rag"].tools == ["rag_retrieve"]
    assert "知识库" in subagents["rag"].trigger_description

    # web 不受影响
    assert subagents["web"].temperature == 0.2


def test_subagents_env_full_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """env JSON 设全部 rag 配置，验证覆盖。"""
    monkeypatch.setenv(
        "AGENTX_SUBAGENTS_CONFIG",
        json.dumps({
            "rag": {
                "enabled": False,
                "temperature": 0.8,
                "system_prompt": "你是知识库检索专家",
                "tools": ["rag_retrieve"],
                "trigger_description": "用户问题涉及知识库检索时触发。",
            }
        }),
    )
    get_settings.cache_clear()

    rag_cfg = get_settings().subagents["rag"]
    assert rag_cfg.enabled is False
    assert rag_cfg.temperature == 0.8
    assert rag_cfg.system_prompt == "你是知识库检索专家"
    assert rag_cfg.tools == ["rag_retrieve"]
    assert "知识库" in rag_cfg.trigger_description


@pytest.mark.xfail(
    reason="backend bug: pydantic-settings v2 的 EnvSettingsSource 在 @field_validator "
           "运行前自动 json.loads 复杂类型字段，无效 JSON 直接抛 SettingsError，"
           "config._parse_json_env 的 fallback 到 {} 无法生效。需后端修复（如改用 "
           "str 字段 + validator 解析，或覆写 prepare_field_value）",
    raises=Exception,
    strict=True,
)
def test_subagents_env_invalid_json_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """env JSON 是无效字符串，验证 fallback 到默认值（不抛异常）。

    期望行为：无效 JSON → _parse_json_env 返回空 dict → subagents 返回默认值。
    实际行为：pydantic-settings 在 validator 之前抛 SettingsError（xfail）。
    """
    monkeypatch.setenv("AGENTX_SUBAGENTS_CONFIG", "not-a-valid-json{{{")
    get_settings.cache_clear()

    settings = get_settings()
    # 无效 JSON → _parse_json_env 应返回空 dict
    assert settings.subagents_config == {}

    subagents = settings.subagents
    assert set(subagents.keys()) == {"rag", "web"}
    assert subagents["rag"].temperature == 0.2
    assert subagents["rag"].enabled is True


def test_subagents_env_trigger_description_string_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """env JSON 中 trigger_description 为字符串时，直接保留（防御性转换）。

    前端配置注入时可能误传字符串（如语义描述），后端需兼容。
    """
    monkeypatch.setenv(
        "AGENTX_SUBAGENTS_CONFIG",
        json.dumps({
            "rag": {
                "trigger_description": "用户问题涉及知识库、文档、资料检索时触发。",
            }
        }),
    )
    get_settings.cache_clear()

    rag_cfg = get_settings().subagents["rag"]
    assert isinstance(rag_cfg.trigger_description, str)
    assert "知识库" in rag_cfg.trigger_description


def test_subagents_env_trigger_description_empty_string_becomes_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env JSON 中 trigger_description 为空字符串时，保持为空字符串。"""
    monkeypatch.setenv(
        "AGENTX_SUBAGENTS_CONFIG",
        json.dumps({"rag": {"trigger_description": "   "}}),
    )
    get_settings.cache_clear()

    rag_cfg = get_settings().subagents["rag"]
    assert rag_cfg.trigger_description == "   "


# ============================================================
# 3. 工具集动态过滤 (R4)
# ============================================================


def test_make_rag_tools_filters_when_rag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tools_enabled.rag_retrieve=false，_make_rag_tools 返回空列表。"""
    from app.subagents.rag_agent import _make_rag_tools

    monkeypatch.setenv(
        "AGENTX_TOOLS_CONFIG",
        json.dumps({"rag_retrieve": False}),
    )
    get_settings.cache_clear()

    tools = _make_rag_tools("t1")
    assert len(tools) == 0


# ============================================================
# 4. temperature clamp (R7) — 前端实现，后端不重复
# ============================================================


@pytest.mark.skip(
    reason="temperature clamp 在 frontend sanitizeSubagent 中实现，backend 不重复；"
           "后端 SubagentSettings 用 pydantic Field(ge=0.0, le=2.0) 做校验"
           "（越界抛 ValidationError），不做 clamp"
)
def test_subagent_temperature_clamp() -> None:
    """后端不做 temperature clamp（前端 sanitizeSubagent 负责）。

    前端在 SubagentsSettings.tsx 的 sanitizeSubagent 中将 temperature clamp 到 [0, 2]，
    后端 SubagentSettings.temperature 用 pydantic Field(ge=0.0, le=2.0) 做校验，
    越界值会抛 ValidationError 而非 clamp。此测试仅为文档目的。
    """
    pass
