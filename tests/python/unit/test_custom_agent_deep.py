"""自定义子代理迁移到 create_deep_agent 测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.deepagent.subagents.custom_agent import build_custom_agent


@pytest.mark.asyncio
async def test_build_custom_agent_uses_create_deep_agent() -> None:
    """自定义子代理应基于 create_deep_agent 而非 create_react_agent。"""
    with patch("app.deepagent.subagents.custom_agent.get_chat_model"), \
         patch("app.deepagent.factory.create_agent") as mock_create:
        mock_create.return_value = MagicMock()

        build_custom_agent(
            key="test",
            system_prompt="You are a test agent.",
            tools=["read_file", "list_dir"],
            temperature=0.2,
        )

        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        assert kwargs["system_prompt"] is not None


@pytest.mark.asyncio
async def test_build_custom_agent_forbids_dangerous_tools() -> None:
    """子代理工具集应过滤掉危险工具。

    内置 fs 工具（read_file/ls/glob/grep）由 AuthorizedLocalShellBackend 注入，
    不在 ``tools`` 列表中。本测试验证 _make_custom_tools 过滤危险工具名，
    保留安全工具（rag_retrieve）。
    """
    rag_tool = MagicMock()
    rag_tool.name = "rag_retrieve"
    with patch("app.deepagent.subagents.custom_agent.get_chat_model"), \
         patch("app.tools.subagent_tools.make_rag_tools", return_value=[rag_tool]), \
         patch("app.deepagent.factory.create_agent") as mock_create:
        mock_create.return_value = MagicMock()

        build_custom_agent(
            key="test",
            system_prompt="You are a test agent.",
            tools=["rag_retrieve", "write_file", "edit_file", "cli_execute"],
            temperature=0.2,
        )

        args, kwargs = mock_create.call_args
        tools = args[1] if len(args) > 1 else kwargs["tools"]
        tool_names = {t.name for t in tools}
        # 危险工具被过滤
        assert "write_file" not in tool_names
        assert "edit_file" not in tool_names
        assert "cli_execute" not in tool_names
        # 安全工具保留
        assert "rag_retrieve" in tool_names
