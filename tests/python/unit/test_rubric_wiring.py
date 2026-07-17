"""RubricMiddleware 真实启用测试。

覆盖：
1. SubagentSettings / CustomSubagentEntry 支持 rubric 字段
2. _parse_custom_subagents 透传 rubric 字段
3. build_custom_agent 在 rubric 非空时透传到 create_agent
4. build_custom_agent rubric 为空时不传（保持向后兼容）
5. grader_model 字段在 SubagentSettings 中可配置
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_subagent_settings_accepts_rubric() -> None:
    """SubagentSettings 支持 rubric 字段。"""
    from app.config.subagents import SubagentSettings

    s = SubagentSettings(
        enabled=True,
        system_prompt="x",
        tools=["read_file"],
        rubric="输出必须包含代码示例与解释。",
    )
    assert s.rubric == "输出必须包含代码示例与解释。"


def test_subagent_settings_default_rubric_empty() -> None:
    """SubagentSettings.rubric 默认空字符串。"""
    from app.config.subagents import SubagentSettings

    s = SubagentSettings()
    assert s.rubric == ""


def test_custom_subagent_entry_accepts_rubric() -> None:
    """CustomSubagentEntry 支持 rubric 字段。"""
    from app.config.subagents import CustomSubagentEntry

    e = CustomSubagentEntry(
        key="researcher",
        name="Researcher",
        system_prompt="You research.",
        tools=["read_file"],
        rubric="答案必须引用至少 2 个来源。",
    )
    assert e.rubric == "答案必须引用至少 2 个来源。"


def test_parse_custom_subagents_propagates_rubric() -> None:
    """_parse_custom_subagents 把 rubric 字段透传到 entry。"""
    from app.config.subagents import _parse_custom_subagents

    raw = {
        "researcher": {
            "name": "Researcher",
            "system_prompt": "You research.",
            "tools": ["read_file"],
            "rubric": "答案必须引用至少 2 个来源。",
        }
    }
    result = _parse_custom_subagents(raw)
    assert "researcher" in result
    assert result["researcher"].rubric == "答案必须引用至少 2 个来源。"


def test_build_custom_agent_passes_rubric_to_create_agent() -> None:
    """build_custom_agent 在 rubric 非空时透传到 create_agent。"""
    with patch("app.deepagent.subagents.custom_agent.get_chat_model"), \
         patch("app.deepagent.factory.create_agent") as mock_create:
        mock_create.return_value = MagicMock()

        from app.deepagent.subagents.custom_agent import build_custom_agent

        build_custom_agent(
            key="researcher",
            system_prompt="You research.",
            tools=["read_file"],
            rubric="答案必须引用至少 2 个来源。",
        )

        _, kwargs = mock_create.call_args
        assert kwargs.get("rubric") == "答案必须引用至少 2 个来源。"


def test_build_custom_agent_no_rubric_omits_field() -> None:
    """build_custom_agent 在 rubric 为空时不应传 rubric 字段。"""
    with patch("app.deepagent.subagents.custom_agent.get_chat_model"), \
         patch("app.deepagent.factory.create_agent") as mock_create:
        mock_create.return_value = MagicMock()

        from app.deepagent.subagents.custom_agent import build_custom_agent

        build_custom_agent(
            key="plain",
            system_prompt="You are plain.",
            tools=["read_file"],
        )

        _, kwargs = mock_create.call_args
        # rubric 为空时不应传 (默认行为)
        assert not kwargs.get("rubric")


def test_build_custom_agent_passes_grader_model() -> None:
    """build_custom_agent 支持 grader_model 透传。"""
    with patch("app.deepagent.subagents.custom_agent.get_chat_model"), \
         patch("app.deepagent.factory.create_agent") as mock_create:
        mock_create.return_value = MagicMock()
        fake_grader = MagicMock(name="grader")

        from app.deepagent.subagents.custom_agent import build_custom_agent

        build_custom_agent(
            key="researcher",
            system_prompt="You research.",
            tools=["read_file"],
            rubric="答案必须引用至少 2 个来源。",
            grader_model=fake_grader,
        )

        _, kwargs = mock_create.call_args
        assert kwargs.get("grader_model") is fake_grader
