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
        from app.config.prompts.agent import _DEFAULT_CODING_EXPERT_SYSTEM_PROMPT

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


# ============================================================
# 4. readonly streak / 重复调用 循环保护
# ============================================================


class TestReadonlyStreakProtection:
    """readonly streak 超限 / 重复调用检测 循环保护测试。

    覆盖 bug 修复：streak 超限后应让 LLM 产出最终回复（token），
    而非立即 return + yield error（旧行为导致用户得到困惑的错误且无任何输出）。
    """

    @pytest.mark.asyncio
    async def test_readonly_streak_triggers_force_answer_not_error(self) -> None:
        """readonly streak 超限后应让 LLM 产出 token，而非 yield error。"""
        from app.agents.expert.coding import run_coding_expert
        from contextlib import ExitStack
        from types import SimpleNamespace

        # is_interrupted: True for 10 iterations, then False (LLM answered)
        interrupted_values = [True] * 10 + [False]
        # pending_calls: 10 DIFFERENT readonly calls (no duplicates → streak only)
        pending_values = [
            [{"name": "list_dir", "args": {"path": f"dir-{i}"}, "id": f"tc-{i}"}]
            for i in range(1, 11)
        ]

        stream_calls = {"n": 0}

        def _stream_side_effect(*args, **kwargs):
            stream_calls["n"] += 1
            n = stream_calls["n"]

            async def _gen():
                # call 11 = force-answer stream → yields token
                if n == 11:
                    yield {"event": "token", "data": "这是基于已有信息的回答"}

            return _gen()

        with ExitStack() as stack:
            stack.enter_context(patch("app.agents.expert.coding._make_deep_tools", return_value=[]))
            stack.enter_context(patch("app.agents.expert.coding._load_mcp_tools", new_callable=AsyncMock, return_value=([], set())))
            stack.enter_context(patch("app.agents.expert.coding.make_expert_delegation_tools", return_value=[]))
            stack.enter_context(patch("app.agents.expert.coding.build_coding_expert", new_callable=AsyncMock))
            stack.enter_context(patch("app.agents.expert.coding._sanitize_message_history", side_effect=lambda x, y: x))
            stack.enter_context(patch("app.agents.expert.coding._inject_tool_error_messages", new_callable=AsyncMock))
            stack.enter_context(patch("app.agents.expert.coding._inject_tool_error_for_call", new_callable=AsyncMock))
            stack.enter_context(patch("app.agents.expert.coding.is_paused", new_callable=AsyncMock, return_value=False))
            stack.enter_context(patch("app.agents.expert.coding.get_sandbox", return_value=MagicMock()))
            stack.enter_context(patch("app.agents.expert.coding._handle_directory_extension", new_callable=AsyncMock, return_value=SimpleNamespace(events=[], denied=False, timed_out=False)))
            stack.enter_context(patch("app.agents.expert.coding._is_interrupted", new_callable=AsyncMock, side_effect=interrupted_values))
            stack.enter_context(patch("app.agents.expert.coding._get_pending_tool_calls", new_callable=AsyncMock, side_effect=pending_values))
            stack.enter_context(patch("app.agents.expert.coding._stream_agent_events", side_effect=_stream_side_effect))

            events = []
            async for sse in run_coding_expert("项目技术栈", "test-streak"):
                events.append(sse)

        # Should have token events (the LLM answer after force-answer)
        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) >= 1, f"expected token events, got: {[e['event'] for e in events]}"
        assert "这是基于已有信息的回答" in token_events[-1]["data"]

        # Should NOT have error events (old behavior yielded error and returned)
        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 0, f"expected no error events, got: {error_events}"

        # Should have reasoning event about "已收集足够上下文"
        reasoning_events = [e for e in events if e["event"] == "reasoning"]
        assert len(reasoning_events) >= 1

    @pytest.mark.asyncio
    async def test_duplicate_tool_calls_triggers_force_answer_immediately(self) -> None:
        """连续两轮完全相同的 tool_call → 立即触发 force-answer（无需等 streak=10）。"""
        from app.agents.expert.coding import run_coding_expert
        from contextlib import ExitStack
        from types import SimpleNamespace

        # is_interrupted: True for 2 iterations, then False
        interrupted_values = [True, True, False]
        # pending_calls: SAME call both times → duplicate detected on iteration 2
        same_call = [{"name": "list_dir", "args": {"path": "."}, "id": "tc-1"}]
        pending_values = [same_call, same_call]

        stream_calls = {"n": 0}

        def _stream_side_effect(*args, **kwargs):
            stream_calls["n"] += 1
            n = stream_calls["n"]

            async def _gen():
                # call 3 = force-answer stream → yields token
                if n == 3:
                    yield {"event": "token", "data": "基于已有信息的回答"}

            return _gen()

        with ExitStack() as stack:
            stack.enter_context(patch("app.agents.expert.coding._make_deep_tools", return_value=[]))
            stack.enter_context(patch("app.agents.expert.coding._load_mcp_tools", new_callable=AsyncMock, return_value=([], set())))
            stack.enter_context(patch("app.agents.expert.coding.make_expert_delegation_tools", return_value=[]))
            stack.enter_context(patch("app.agents.expert.coding.build_coding_expert", new_callable=AsyncMock))
            stack.enter_context(patch("app.agents.expert.coding._sanitize_message_history", side_effect=lambda x, y: x))
            stack.enter_context(patch("app.agents.expert.coding._inject_tool_error_messages", new_callable=AsyncMock))
            stack.enter_context(patch("app.agents.expert.coding._inject_tool_error_for_call", new_callable=AsyncMock))
            stack.enter_context(patch("app.agents.expert.coding.is_paused", new_callable=AsyncMock, return_value=False))
            stack.enter_context(patch("app.agents.expert.coding.get_sandbox", return_value=MagicMock()))
            stack.enter_context(patch("app.agents.expert.coding._handle_directory_extension", new_callable=AsyncMock, return_value=SimpleNamespace(events=[], denied=False, timed_out=False)))
            stack.enter_context(patch("app.agents.expert.coding._is_interrupted", new_callable=AsyncMock, side_effect=interrupted_values))
            stack.enter_context(patch("app.agents.expert.coding._get_pending_tool_calls", new_callable=AsyncMock, side_effect=pending_values))
            stack.enter_context(patch("app.agents.expert.coding._stream_agent_events", side_effect=_stream_side_effect))

            events = []
            async for sse in run_coding_expert("列出目录", "test-dup"):
                events.append(sse)

        # Should trigger on iteration 2 (not 10) → only 3 stream calls
        assert stream_calls["n"] == 3, f"expected 3 stream calls, got {stream_calls['n']}"

        # Should have token events
        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) >= 1

        # Should NOT have error events
        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 0

    @pytest.mark.asyncio
    async def test_normal_flow_below_threshold_no_force_answer(self) -> None:
        """正常多工具流程（streak < 10，无重复）不应触发 force-answer。"""
        from app.agents.expert.coding import run_coding_expert
        from contextlib import ExitStack
        from types import SimpleNamespace

        # is_interrupted: True for 3 iterations, then False (LLM answered)
        interrupted_values = [True, True, True, False]
        # pending_calls: 3 DIFFERENT readonly calls (no duplicates, streak=3 < 10)
        pending_values = [
            [{"name": "list_dir", "args": {"path": f"dir-{i}"}, "id": f"tc-{i}"}]
            for i in range(1, 4)
        ]

        stream_calls = {"n": 0}

        def _stream_side_effect(*args, **kwargs):
            stream_calls["n"] += 1
            n = stream_calls["n"]

            async def _gen():
                # call 4 = final standard resume → yields token (LLM answered)
                if n == 4:
                    yield {"event": "token", "data": "正常回答"}

            return _gen()

        with ExitStack() as stack:
            stack.enter_context(patch("app.agents.expert.coding._make_deep_tools", return_value=[]))
            stack.enter_context(patch("app.agents.expert.coding._load_mcp_tools", new_callable=AsyncMock, return_value=([], set())))
            stack.enter_context(patch("app.agents.expert.coding.make_expert_delegation_tools", return_value=[]))
            stack.enter_context(patch("app.agents.expert.coding.build_coding_expert", new_callable=AsyncMock))
            stack.enter_context(patch("app.agents.expert.coding._sanitize_message_history", side_effect=lambda x, y: x))
            stack.enter_context(patch("app.agents.expert.coding._inject_tool_error_messages", new_callable=AsyncMock))
            stack.enter_context(patch("app.agents.expert.coding._inject_tool_error_for_call", new_callable=AsyncMock))
            stack.enter_context(patch("app.agents.expert.coding.is_paused", new_callable=AsyncMock, return_value=False))
            stack.enter_context(patch("app.agents.expert.coding.get_sandbox", return_value=MagicMock()))
            stack.enter_context(patch("app.agents.expert.coding._handle_directory_extension", new_callable=AsyncMock, return_value=SimpleNamespace(events=[], denied=False, timed_out=False)))
            stack.enter_context(patch("app.agents.expert.coding._is_interrupted", new_callable=AsyncMock, side_effect=interrupted_values))
            stack.enter_context(patch("app.agents.expert.coding._get_pending_tool_calls", new_callable=AsyncMock, side_effect=pending_values))
            stack.enter_context(patch("app.agents.expert.coding._stream_agent_events", side_effect=_stream_side_effect))

            events = []
            async for sse in run_coding_expert("简单问题", "test-normal"):
                events.append(sse)

        # Should have token events (normal answer)
        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) >= 1
        assert "正常回答" in token_events[-1]["data"]

        # Should NOT have reasoning events about force-answer
        reasoning_events = [
            e for e in events if e["event"] == "reasoning"
            and "已收集足够上下文" in e.get("data", "")
        ]
        assert len(reasoning_events) == 0

        # Should NOT have error events
        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 0
