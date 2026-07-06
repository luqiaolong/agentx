"""subagents-config 后端逻辑单元测试。

覆盖：
1. SubagentSettings 数据结构 (R1)
2. env override 合并逻辑 (R3) — 字段级合并，无效 JSON fallback
3. 禁用子代理退回路径 A (R5) — _select_subagent 返回 None 走路径 A
4. 关键词可配置 + C2 修复 (R6) — 空 keywords 不匹配任何子代理
5. 工具集动态过滤 (R4) — _make_fs_tools / _make_rag_tools 按 tools_enabled 过滤
6. temperature clamp (R7) — 前端实现，后端 skip
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.config import SubagentSettings, get_settings
from app.subagents.dispatch import select_subagent


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
    """不设 AGENTX_SUBAGENTS_CONFIG env，返回默认值（code/rag/web 三键齐全）。"""
    # conftest autouse fixture 已清 env + cache
    settings = get_settings()
    subagents = settings.subagents
    assert set(subagents.keys()) == {"code", "rag", "web"}

    # code 默认值
    assert subagents["code"].enabled is True
    assert subagents["code"].temperature == 0.2
    assert subagents["code"].tools == ["read_file", "list_dir", "glob", "grep"]
    assert "代码" in subagents["code"].trigger_description

    # rag 默认值
    assert subagents["rag"].enabled is True
    assert subagents["rag"].tools == ["rag_retrieve"]
    assert "知识库" in subagents["rag"].trigger_description

    # web 默认值
    assert subagents["web"].enabled is True
    assert subagents["web"].tools == ["web_search"]
    assert "互联网" in subagents["web"].trigger_description


def test_subagents_env_partial_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """env JSON 只设 code.temperature=0.5，其他字段保持默认值（字段级合并）。"""
    monkeypatch.setenv(
        "AGENTX_SUBAGENTS_CONFIG",
        json.dumps({"code": {"temperature": 0.5}}),
    )
    get_settings.cache_clear()

    subagents = get_settings().subagents

    # code.temperature 被覆盖
    assert subagents["code"].temperature == 0.5
    # code 其他字段保持默认（字段级合并）
    assert subagents["code"].enabled is True
    # system_prompt 有默认长文本，不校验具体内容
    assert subagents["code"].system_prompt != ""
    assert subagents["code"].tools == ["read_file", "list_dir", "glob", "grep"]
    assert "代码" in subagents["code"].trigger_description

    # rag/web 不受影响
    assert subagents["rag"].temperature == 0.2
    assert subagents["web"].temperature == 0.2


def test_subagents_env_full_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """env JSON 设全部 code 配置，验证覆盖。"""
    monkeypatch.setenv(
        "AGENTX_SUBAGENTS_CONFIG",
        json.dumps({
            "code": {
                "enabled": False,
                "temperature": 0.8,
                "system_prompt": "你是代码专家",
                "tools": ["read_file"],
                "trigger_description": "用户问题涉及写代码或编程时触发。",
            }
        }),
    )
    get_settings.cache_clear()

    code_cfg = get_settings().subagents["code"]
    assert code_cfg.enabled is False
    assert code_cfg.temperature == 0.8
    assert code_cfg.system_prompt == "你是代码专家"
    assert code_cfg.tools == ["read_file"]
    assert "写代码" in code_cfg.trigger_description


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
    assert set(subagents.keys()) == {"code", "rag", "web"}
    assert subagents["code"].temperature == 0.2
    assert subagents["code"].enabled is True


def test_subagents_env_trigger_description_string_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """env JSON 中 trigger_description 为字符串时，直接保留（防御性转换）。

    前端配置注入时可能误传字符串（如语义描述），后端需兼容。
    """
    monkeypatch.setenv(
        "AGENTX_SUBAGENTS_CONFIG",
        json.dumps({
            "code": {
                "trigger_description": "用户问题涉及代码、文件、目录、技术实现、调试排错、依赖分析或代码审查时触发。",
            }
        }),
    )
    get_settings.cache_clear()

    code_cfg = get_settings().subagents["code"]
    assert isinstance(code_cfg.trigger_description, str)
    assert "代码" in code_cfg.trigger_description


def test_subagents_env_trigger_description_empty_string_becomes_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env JSON 中 trigger_description 为空字符串时，保持为空字符串。"""
    monkeypatch.setenv(
        "AGENTX_SUBAGENTS_CONFIG",
        json.dumps({"code": {"trigger_description": "   "}}),
    )
    get_settings.cache_clear()

    code_cfg = get_settings().subagents["code"]
    assert code_cfg.trigger_description == "   "


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
    assert set(subagents.keys()) == {"code", "rag", "web"}
    assert subagents["code"].temperature == 0.2
    assert subagents["code"].enabled is True


# ============================================================
# 3. 禁用子代理退回路径 A (R5)
# ============================================================


def test_select_subagent_disabled_web_falls_back_to_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """web 禁用时，即使消息含 web 关键词，也退回 code（兜底子代理）。

    注意：_select_subagent 的兜底逻辑是「无命中 → 默认 code（若可用）」，
    所以仅禁用 web 而非全部禁用时，返回 "code" 而非 None。
    全部禁用的场景见 test_select_subagent_all_disabled_returns_none。
    """
    mock_settings = _make_mock_settings(
        subagents_overrides={"web": {"enabled": False}}
    )
    monkeypatch.setattr("app.subagents.dispatch.get_settings", lambda: mock_settings)

    # "搜索一下" 默认匹配 web 关键词，但 web 被禁用 → 跳过
    # rag 关键词不匹配 → 跳过；code.keywords=[] → 不匹配
    # 兜底：code 启用且有工具 → 返回 "code"
    result = select_subagent("搜索一下")
    assert result == "code"


def test_select_subagent_all_disabled_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """三个子代理都禁用，返回 None（退回路径 A）。"""
    mock_settings = _make_mock_settings(
        subagents_overrides={
            "code": {"enabled": False},
            "rag": {"enabled": False},
            "web": {"enabled": False},
        }
    )
    monkeypatch.setattr("app.subagents.dispatch.get_settings", lambda: mock_settings)

    result = select_subagent("搜索一下")
    assert result is None


def test_select_subagent_code_disabled_no_match_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """code 禁用且无关键词命中时返回 None（退回路径 A）。

    web/rag 保持默认，但消息不匹配它们的关键词，code 被禁用 → 无兜底 → None。
    """
    mock_settings = _make_mock_settings(
        subagents_overrides={"code": {"enabled": False}}
    )
    monkeypatch.setattr("app.subagents.dispatch.get_settings", lambda: mock_settings)

    # "你好" 不匹配 web/rag 默认关键词，code 被禁用 → None
    result = select_subagent("你好")
    assert result is None


def test_select_subagent_all_tools_disabled_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子代理匹配关键词但其工具全禁用时，返回 None。"""
    mock_settings = _make_mock_settings(
        subagents_overrides={"code": {"enabled": False}},
        tools_overrides={"web_search": False},
    )
    monkeypatch.setattr("app.subagents.dispatch.get_settings", lambda: mock_settings)

    # "搜索" 匹配 web 关键词，但 web_search 工具被禁用 → None
    result = select_subagent("搜索")
    assert result is None


# ============================================================
# 4. 关键词可配置 (R6) — 验证 C2 修复
# ============================================================


def test_select_subagent_empty_keywords_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """web.keywords=[] (空列表)，发任何消息都不匹配 web 子代理（C2 修复点）。

    C2 修复：``any(kw in message for kw in cfg.keywords)`` 在 keywords 为空时
    返回 False（空可迭代对象的 any() = False），不再误匹配。
    """
    mock_settings = _make_mock_settings(
        subagents_overrides={
            "web": {"trigger_description": ""},
            "rag": {"trigger_description": ""},
            "code": {"enabled": False},  # 排除 code 兜底干扰
        }
    )
    monkeypatch.setattr("app.subagents.dispatch.get_settings", lambda: mock_settings)

    # "搜索一下" 不匹配任何子代理（web/rag 空 trigger_description，code 禁用）→ None
    result = select_subagent("搜索一下")
    assert result is None


def test_select_subagent_custom_trigger_description_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """web.trigger_description 含短关键词 "搜一下"，发「帮我搜一下」消息匹配 web。"""
    mock_settings = _make_mock_settings(
        subagents_overrides={
            "web": {"trigger_description": "搜一下、查查"},
            "rag": {"trigger_description": ""},
        }
    )
    monkeypatch.setattr("app.subagents.dispatch.get_settings", lambda: mock_settings)

    result = select_subagent("帮我搜一下")
    assert result == "web"


def test_select_subagent_trigger_description_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """web.trigger_description="搜索"，发「写代码」消息不匹配 web。"""
    mock_settings = _make_mock_settings(
        subagents_overrides={
            "web": {"trigger_description": "用户问题涉及搜索时触发。"},
            "code": {"enabled": False},  # 排除 code 兜底干扰
        }
    )
    monkeypatch.setattr("app.subagents.dispatch.get_settings", lambda: mock_settings)

    # "写代码" 不含 "搜索" → web 不匹配 → None
    result = select_subagent("写代码")
    assert result is None


def test_select_subagent_code_empty_keywords_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """code.trigger_description="" (默认), 仍可作为 fallback 匹配（如有工具可用）。

    code 是兜底子代理：即使 trigger_description 为空（不匹配任何关键词），
    当其他子代理都不匹配时，code 仍会被选为 fallback（若 enabled 且工具可用）。
    """
    mock_settings = _make_mock_settings()  # 全默认
    monkeypatch.setattr("app.subagents.dispatch.get_settings", lambda: mock_settings)

    # "你好" 不匹配任何关键词，但 code 作为兜底子代理仍返回
    result = select_subagent("你好")
    assert result == "code"


# ============================================================
# 5. 工具集动态过滤 (R4)
# ============================================================


def test_make_fs_tools_filters_disabled_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tools_enabled.glob=false，_make_fs_tools 返回的工具集不含 glob_files。"""
    from app.subagents.code_agent import _make_fs_tools

    monkeypatch.setenv(
        "AGENTX_TOOLS_CONFIG",
        json.dumps({"glob": False}),
    )
    get_settings.cache_clear()

    tools = _make_fs_tools("t1")
    tool_names = {t.name for t in tools}
    assert "glob_files" not in tool_names
    assert "read_file" in tool_names
    assert "list_dir" in tool_names
    assert "grep_files" in tool_names


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
# 6. temperature clamp (R7) — 前端实现，后端不重复
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
