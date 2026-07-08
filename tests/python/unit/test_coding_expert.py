"""coding Expert（coding 场景专家 agent）单元测试。

覆盖 task 3.8：
1. Expert 委派工具构建（make_expert_delegation_tools）
   - 仅含 delegate_to_subagent，无 delegate_to_expert / invoke_agent_team
2. build_coding_expert 构建（mock LLM）
3. run_coding_expert 基本流程（@mention + 审批流）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================
# 1. Expert 委派工具构建
# ============================================================


class TestMakeExpertDelegationTools:
    """make_expert_delegation_tools 委派工具构建。"""

    def test_returns_only_delegate_to_subagent(self) -> None:
        """Expert 仅返回 delegate_to_subagent 工具。"""
        from app.agents.expert.coding import make_expert_delegation_tools

        tools = make_expert_delegation_tools("test-thread")
        assert len(tools) == 1
        assert tools[0].name == "delegate_to_subagent"

    def test_no_delegate_to_expert(self) -> None:
        """Expert 无 delegate_to_expert 工具（不可委派其他 Expert）。"""
        from app.agents.expert.coding import make_expert_delegation_tools

        tools = make_expert_delegation_tools("test-thread")
        names = {t.name for t in tools}
        assert "delegate_to_expert" not in names

    def test_no_invoke_agent_team(self) -> None:
        """Expert 无 invoke_agent_team 工具（不可触发 AgentTeam）。"""
        from app.agents.expert.coding import make_expert_delegation_tools

        tools = make_expert_delegation_tools("test-thread")
        names = {t.name for t in tools}
        assert "invoke_agent_team" not in names

    @pytest.mark.asyncio
    async def test_delegate_to_subagent_unknown_returns_error(self) -> None:
        """delegate_to_subagent 未知子代理返回错误信息。"""
        from app.agents.expert.coding import make_expert_delegation_tools

        tools = make_expert_delegation_tools("test-thread")
        delegate_tool = tools[0]

        result = await delegate_tool.ainvoke({
            "agent_name": "unknown_agent",
            "task": "test task",
        })
        assert "错误" in result or "error" in result.lower()


# ============================================================
# 2. build_coding_expert 构建（mock LLM）
# ============================================================


class TestBuildCodingExpert:
    """build_coding_expert 构建（mock LLM）。"""

    @pytest.mark.asyncio
    async def test_build_returns_compiled_graph(self) -> None:
        """build_coding_expert 返回编译后的图（通过 build_deep_agent）。"""
        from app.agents.expert.coding import build_coding_expert

        mock_agent = MagicMock()
        with patch("app.agents.expert.coding.build_deep_agent", new_callable=AsyncMock, return_value=mock_agent) as mock_build:
            with patch("app.agents.expert.coding._make_deep_tools", return_value=[]):
                with patch("app.agents.expert.coding.make_expert_delegation_tools", return_value=[]):
                    agent = await build_coding_expert("test-thread")

                    assert agent is mock_agent
                    mock_build.assert_called_once()
                    # 验证 scene_prompt 被传入（覆盖默认 _DEEP_SYSTEM_PROMPT）
                    call_kwargs = mock_build.call_args.kwargs
                    assert "scene_prompt" in call_kwargs
                    assert call_kwargs["scene_prompt"]  # 非空

    @pytest.mark.asyncio
    async def test_build_with_custom_tools(self) -> None:
        """build_coding_expert 接受自定义 tools 列表。"""
        from app.agents.expert.coding import build_coding_expert

        mock_agent = MagicMock()
        custom_tools = [MagicMock(name="tool1")]

        with patch("app.agents.expert.coding.build_deep_agent", new_callable=AsyncMock, return_value=mock_agent) as mock_build:
            agent = await build_coding_expert("test-thread", tools=custom_tools)

            assert agent is mock_agent
            call_args = mock_build.call_args.kwargs
            assert call_args["tools"] == custom_tools

    @pytest.mark.asyncio
    async def test_build_uses_coding_system_prompt(self) -> None:
        """build_coding_expert 使用 coding Expert 专用 system prompt。"""
        from app.agents.expert.coding import build_coding_expert

        mock_agent = MagicMock()
        with patch("app.agents.expert.coding.build_deep_agent", new_callable=AsyncMock, return_value=mock_agent) as mock_build:
            with patch("app.agents.expert.coding._make_deep_tools", return_value=[]):
                with patch("app.agents.expert.coding.make_expert_delegation_tools", return_value=[]):
                    await build_coding_expert("test-thread")

                    call_kwargs = mock_build.call_args.kwargs
                    # scene_prompt 应为 coding Expert 专用 prompt
                    assert "coding" in call_kwargs["scene_prompt"].lower() or "代码" in call_kwargs["scene_prompt"]


# ============================================================
# 3. run_coding_expert 基本流程
# ============================================================


class TestRunCodingExpert:
    """run_coding_expert 基本流程测试。"""

    @pytest.mark.asyncio
    async def test_run_yields_sse_events(self) -> None:
        """run_coding_expert 流式产出 SSE 事件。"""
        from app.agents.expert.coding import run_coding_expert

        async def mock_stream(*args, **kwargs):
            yield {"event": "token", "data": "hello"}

        with patch("app.agents.expert.coding._make_deep_tools", return_value=[]):
            with patch("app.agents.expert.coding._load_mcp_tools", new_callable=AsyncMock, return_value=([], set())):
                with patch("app.agents.expert.coding.make_expert_delegation_tools", return_value=[]):
                    with patch("app.agents.expert.coding.build_coding_expert", new_callable=AsyncMock):
                        with patch("app.agents.expert.coding._stream_agent_events") as mock_sse:
                            with patch("app.agents.expert.coding._is_interrupted", new_callable=AsyncMock, return_value=False):
                                with patch("app.agents.expert.coding._inject_tool_error_messages", new_callable=AsyncMock):
                                    with patch("app.agents.expert.coding._sanitize_message_history", side_effect=lambda x, y: x):

                                        mock_sse.return_value = mock_stream()

                                        events = []
                                        async for sse in run_coding_expert(
                                            "帮我写代码",
                                            "test-thread",
                                        ):
                                            events.append(sse)

                                        assert len(events) >= 1
                                        assert events[0]["event"] == "token"
                                        assert events[0]["data"] == "hello"

    @pytest.mark.asyncio
    async def test_run_llm_unavailable_yields_error(self) -> None:
        """LLM 不可用时 yield error 事件。"""
        from app.agents.expert.coding import run_coding_expert

        with patch("app.agents.expert.coding._make_deep_tools", return_value=[]):
            with patch("app.agents.expert.coding._load_mcp_tools", new_callable=AsyncMock, return_value=([], set())):
                with patch("app.agents.expert.coding.make_expert_delegation_tools", return_value=[]):
                    with patch("app.agents.expert.coding.build_coding_expert", new_callable=AsyncMock, side_effect=ValueError("LLM 不可用")):
                        events = []
                        async for sse in run_coding_expert(
                            "帮我写代码",
                            "test-thread",
                        ):
                            events.append(sse)

                        assert len(events) == 1
                        assert events[0]["event"] == "error"
                        assert "LLM 不可用" in events[0]["data"]
