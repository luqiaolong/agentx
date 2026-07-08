"""Supervisor（work 场景全能 agent）单元测试。

覆盖 task 2.9：
1. @mention 解析（parse_mention / strip_mention）
2. 委派工具构建（make_delegation_tools）
3. Supervisor 构建（build_work_supervisor，mock LLM）
4. @mention 强制委派路由
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.supervisor.mention import MENTION_PATTERN, parse_mention, strip_mention


# ============================================================
# 1. @mention 解析
# ============================================================


class TestParseMention:
    """parse_mention @mention 解析。"""

    def test_no_mention(self) -> None:
        """无 @mention 返回原消息和 None。"""
        cleaned, target = parse_mention("你好，帮我写代码")
        assert cleaned == "你好，帮我写代码"
        assert target is None

    def test_coding_mention(self) -> None:
        """@coding 解析为 expert 委派。"""
        cleaned, target = parse_mention("@coding 帮我review这段代码")
        assert target == ("expert", "coding")
        assert "帮我review这段代码" in cleaned
        assert "@coding" not in cleaned

    def test_rag_mention(self) -> None:
        """@rag 解析为 subagent 委派。"""
        cleaned, target = parse_mention("@rag 查询用户认证流程")
        assert target == ("subagent", "rag")
        assert "查询用户认证流程" in cleaned
        assert "@rag" not in cleaned

    def test_web_mention(self) -> None:
        """@web 解析为 subagent 委派。"""
        cleaned, target = parse_mention("@web 搜索最新React版本")
        assert target == ("subagent", "web")
        assert "搜索最新React版本" in cleaned

    def test_unknown_mention_returns_none(self) -> None:
        """未知 @mention strip 后返回 None。"""
        cleaned, target = parse_mention("@unknown 一些任务")
        assert target is None
        assert "@unknown" not in cleaned
        assert "一些任务" in cleaned

    def test_team_mention_not_supported(self) -> None:
        """@team 不支持，strip 后返回 None。"""
        cleaned, target = parse_mention("@team 任务")
        assert target is None
        assert "@team" not in cleaned

    def test_coding_team_mention_not_supported(self) -> None:
        """@coding_team 不支持，strip 后返回 None。"""
        cleaned, target = parse_mention("@coding_team 任务")
        assert target is None
        assert "@coding_team" not in cleaned

    def test_mention_case_insensitive(self) -> None:
        """@Coding 大写也能识别。"""
        cleaned, target = parse_mention("@Coding 任务")
        assert target == ("expert", "coding")

    def test_mention_with_extra_spaces(self) -> None:
        """@mention 后多余空格被清理。"""
        cleaned, target = parse_mention("@coding    帮我写代码")
        assert target == ("expert", "coding")
        assert "帮我写代码" in cleaned
        # 不应有连续多余空格
        assert "    " not in cleaned

    def test_empty_message(self) -> None:
        """空消息返回空和 None。"""
        cleaned, target = parse_mention("")
        assert cleaned == ""
        assert target is None

    def test_mention_at_middle(self) -> None:
        """@mention 在消息中间也能识别。"""
        cleaned, target = parse_mention("请帮我 @coding review 代码")
        assert target == ("expert", "coding")
        assert "@coding" not in cleaned


class TestStripMention:
    """strip_mention 剥离所有 @mention。"""

    def test_strip_all_mentions(self) -> None:
        """剥离所有 @mention 标记。"""
        result = strip_mention("@coding 帮我 @rag 查询")
        assert "@" not in result
        assert "coding" not in result
        assert "rag" not in result

    def test_strip_no_mention(self) -> None:
        """无 @mention 时返回原消息。"""
        result = strip_mention("你好")
        assert result == "你好"

    def test_strip_empty(self) -> None:
        """空消息返回空。"""
        assert strip_mention("") == ""


class TestMentionPattern:
    """MENTION_PATTERN 正则校验。"""

    def test_matches_alphanumeric(self) -> None:
        """匹配字母数字下划线连字符。"""
        assert MENTION_PATTERN.search("@coding")
        assert MENTION_PATTERN.search("@rag_agent")
        assert MENTION_PATTERN.search("@my-agent")
        assert MENTION_PATTERN.search("@agent123")

    def test_no_match_special_chars(self) -> None:
        """不匹配特殊字符开头。"""
        assert not MENTION_PATTERN.search("@ coding")
        assert not MENTION_PATTERN.search("@!invalid")


# ============================================================
# 2. 委派工具构建
# ============================================================


class TestMakeDelegationTools:
    """make_delegation_tools 委派工具构建。"""

    def test_returns_two_tools(self) -> None:
        """返回 delegate_to_expert 和 delegate_to_subagent 两个工具。"""
        from app.agents.supervisor.delegation import make_delegation_tools

        tools = make_delegation_tools("test-thread")
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "delegate_to_expert" in names
        assert "delegate_to_subagent" in names

    def test_no_invoke_agent_team_tool(self) -> None:
        """确保无 invoke_agent_team 工具。"""
        from app.agents.supervisor.delegation import make_delegation_tools

        tools = make_delegation_tools("test-thread")
        names = {t.name for t in tools}
        assert "invoke_agent_team" not in names

    @pytest.mark.asyncio
    async def test_delegate_to_expert_unknown_returns_error(self) -> None:
        """delegate_to_expert 未知 Expert 返回错误信息。"""
        from app.agents.supervisor.delegation import make_delegation_tools

        tools = make_delegation_tools("test-thread")
        delegate_tool = next(t for t in tools if t.name == "delegate_to_expert")

        # 调用工具（LangChain @tool 装饰器包装）
        result = await delegate_tool.ainvoke({
            "expert_name": "unknown_expert",
            "task": "test task",
        })
        assert "错误" in result or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_delegate_to_subagent_unknown_returns_error(self) -> None:
        """delegate_to_subagent 未知子代理返回错误信息。"""
        from app.agents.supervisor.delegation import make_delegation_tools

        tools = make_delegation_tools("test-thread")
        delegate_tool = next(t for t in tools if t.name == "delegate_to_subagent")

        result = await delegate_tool.ainvoke({
            "agent_name": "unknown_agent",
            "task": "test task",
        })
        assert "错误" in result or "error" in result.lower()


# ============================================================
# 3. Supervisor 构建（mock LLM）
# ============================================================


class TestBuildWorkSupervisor:
    """build_work_supervisor 构建（mock LLM）。"""

    @pytest.mark.asyncio
    async def test_build_returns_compiled_graph(self) -> None:
        """build_work_supervisor 返回编译后的图。"""
        from app.agents.supervisor.work_supervisor import build_work_supervisor

        # mock LLM
        mock_model = MagicMock()
        with patch("app.agents.supervisor.work_supervisor.get_chat_model", return_value=mock_model):
            with patch("app.agents.supervisor.work_supervisor._make_deep_tools", return_value=[]):
                with patch("app.agents.supervisor.work_supervisor.make_delegation_tools", return_value=[]):
                    with patch("app.agents.supervisor.work_supervisor.get_async_checkpointer", new_callable=AsyncMock):
                        with patch("app.agents.supervisor.work_supervisor.create_react_agent") as mock_create:
                            mock_agent = MagicMock()
                            mock_create.return_value = mock_agent

                            agent = await build_work_supervisor("test-thread")

                            assert agent is mock_agent
                            mock_create.assert_called_once()
                            # 验证 name="work_supervisor"
                            call_kwargs = mock_create.call_args.kwargs
                            assert call_kwargs["name"] == "work_supervisor"
                            assert call_kwargs["interrupt_before"] == ["tools"]

    @pytest.mark.asyncio
    async def test_build_with_custom_tools(self) -> None:
        """build_work_supervisor 接受自定义 tools 列表。"""
        from app.agents.supervisor.work_supervisor import build_work_supervisor

        mock_model = MagicMock()
        custom_tools = [MagicMock(name="tool1")]

        with patch("app.agents.supervisor.work_supervisor.get_chat_model", return_value=mock_model):
            with patch("app.agents.supervisor.work_supervisor.get_async_checkpointer", new_callable=AsyncMock):
                with patch("app.agents.supervisor.work_supervisor.create_react_agent") as mock_create:
                    mock_create.return_value = MagicMock()
                    await build_work_supervisor("test-thread", tools=custom_tools)

                    call_args = mock_create.call_args.args
                    assert call_args[1] == custom_tools  # tools 参数


# ============================================================
# 4. @mention 强制委派路由
# ============================================================


class TestMentionRouting:
    """@mention 强制委派路由测试。"""

    @pytest.mark.asyncio
    async def test_mention_coding_routes_to_expert(self) -> None:
        """@coding 直接运行 coding Expert，bypass Supervisor。"""
        from app.agents.supervisor.work_supervisor import run_work_supervisor

        # mock run_coding_expert
        async def mock_expert_stream(*args, **kwargs):
            yield {"event": "token", "data": "expert result"}

        with patch("app.agents.expert.coding.run_coding_expert", mock_expert_stream):
            events = []
            async for sse in run_work_supervisor(
                "@coding 帮我review代码",
                "test-thread",
            ):
                events.append(sse)

            # 应该有 delegation 事件
            delegation_events = [e for e in events if e.get("event") == "delegation"]
            assert len(delegation_events) >= 1
            assert "coding" in delegation_events[0].get("data", "")

            # 应该有 expert 的 token 事件
            token_events = [e for e in events if e.get("event") == "token"]
            assert len(token_events) >= 1

    @pytest.mark.asyncio
    async def test_mention_rag_runs_subagent_then_supervisor(self) -> None:
        """@rag 运行子代理后回注 Supervisor 合成。"""
        from app.agents.supervisor.work_supervisor import run_work_supervisor

        # mock run_rag_agent
        async def mock_rag_stream(*args, **kwargs):
            yield {"type": "token", "content": "RAG 检索结果"}

        # mock Supervisor 构建和执行（避免真正调 LLM）
        with patch("app.subagents.rag_agent.run_rag_agent", mock_rag_stream):
            with patch("app.agents.supervisor.work_supervisor._make_deep_tools", return_value=[]):
                with patch("app.agents.supervisor.work_supervisor.make_delegation_tools", return_value=[]):
                    with patch("app.agents.supervisor.work_supervisor._load_mcp_tools", new_callable=AsyncMock, return_value=([], set())):
                        with patch("app.agents.supervisor.work_supervisor.build_work_supervisor", new_callable=AsyncMock):
                            with patch("app.agents.supervisor.work_supervisor._stream_agent_events") as mock_stream:
                                with patch("app.agents.supervisor.work_supervisor._is_interrupted", new_callable=AsyncMock, return_value=False):
                                    with patch("app.agents.supervisor.work_supervisor._inject_tool_error_messages", new_callable=AsyncMock):
                                        with patch("app.agents.supervisor.work_supervisor._sanitize_message_history", side_effect=lambda x, y: x):

                                            async def mock_supervisor_stream(*args, **kwargs):
                                                yield {"event": "token", "data": "supervisor synthesis"}

                                            mock_stream.return_value = mock_supervisor_stream()

                                            events = []
                                            async for sse in run_work_supervisor(
                                                "@rag 查询文档",
                                                "test-thread",
                                            ):
                                                events.append(sse)

                                            # 应该有 delegation 事件（到 rag）
                                            delegation_events = [e for e in events if e.get("event") == "delegation"]
                                            assert len(delegation_events) >= 1
