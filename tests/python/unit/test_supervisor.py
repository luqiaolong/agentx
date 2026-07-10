"""Supervisor（work 场景全能 agent）单元测试。

覆盖：
1. @mention 解析（parse_mention / strip_mention）
2. 子代理 runnable 构建（_build_subagent_runnables）
3. coding Expert 委派工具（make_expert_delegation_tool）
4. Supervisor 构建（build_work_supervisor，mock LLM）
5. @mention 强制委派路由
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scenarios.work.mention import MENTION_PATTERN, parse_mention, strip_mention


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
# 2. 子代理 runnable 构建
# ============================================================


class TestBuildSubagentRunnables:
    """_build_subagent_runnables 构建 compiled subagent 列表。"""

    def test_builds_rag_and_web_when_enabled(self) -> None:
        """rag / web 启用时生成对应的 CompiledSubAgent。"""
        from app.scenarios.work.agent import _build_subagent_runnables

        rag_cfg = MagicMock(enabled=True, trigger_description="rag desc")
        web_cfg = MagicMock(enabled=True, trigger_description="web desc")
        settings = MagicMock()
        settings.subagents = {"rag": rag_cfg, "web": web_cfg}
        settings.custom_subagents = {}

        with patch("app.scenarios.work.agent.get_settings", return_value=settings):
            with patch("app.subagents.rag_agent.build_rag_agent") as mock_rag:
                with patch("app.subagents.web_agent.build_web_agent") as mock_web:
                    mock_rag.return_value = MagicMock(name="rag_graph")
                    mock_web.return_value = MagicMock(name="web_graph")

                    subagents = _build_subagent_runnables("test-thread")

                    names = {s["name"] for s in subagents}
                    assert names == {"rag", "web"}
                    assert any(s["runnable"] is mock_rag.return_value for s in subagents)
                    assert any(s["runnable"] is mock_web.return_value for s in subagents)
                    mock_rag.assert_called_once_with("test-thread", chat_model=None)
                    mock_web.assert_called_once_with("test-thread", chat_model=None)

    def test_skips_disabled(self) -> None:
        """禁用的子代理不会出现在列表中。"""
        from app.scenarios.work.agent import _build_subagent_runnables

        rag_cfg = MagicMock(enabled=False, trigger_description="")
        web_cfg = MagicMock(enabled=True, trigger_description="")
        settings = MagicMock()
        settings.subagents = {"rag": rag_cfg, "web": web_cfg}
        settings.custom_subagents = {}

        with patch("app.scenarios.work.agent.get_settings", return_value=settings):
            with patch("app.subagents.web_agent.build_web_agent") as mock_web:
                mock_web.return_value = MagicMock()

                subagents = _build_subagent_runnables("test-thread")

                assert [s["name"] for s in subagents] == ["web"]

    def test_custom_subagents(self) -> None:
        """自定义子代理按 key 生成。"""
        from app.scenarios.work.agent import _build_subagent_runnables

        settings = MagicMock()
        settings.subagents = {}
        custom_cfg = MagicMock(enabled=True, trigger_description="custom desc")
        settings.custom_subagents = {"myagent": custom_cfg}

        with patch("app.scenarios.work.agent.get_settings", return_value=settings):
            with patch("app.subagents.custom_agent.build_custom_agent") as mock_custom:
                mock_custom.return_value = MagicMock()

                subagents = _build_subagent_runnables("test-thread", workspace_path="/ws")

                assert [s["name"] for s in subagents] == ["myagent"]
                mock_custom.assert_called_once_with(
                    "myagent", thread_id="test-thread", workspace_path="/ws", chat_model=None
                )


# ============================================================
# 3. coding Expert 委派工具
# ============================================================


class TestExpertDelegationTool:
    """make_expert_delegation_tool 构建的 delegate_to_expert 工具。"""

    def _settings_with_coding(self) -> MagicMock:
        settings = MagicMock()
        coding_cfg = MagicMock(enabled=True, scenario="coding")
        settings.agents.experts = {"coding": coding_cfg}
        return settings

    def test_returns_tool(self) -> None:
        """返回一个名为 delegate_to_expert 的工具。"""
        from app.scenarios.work.agent import make_expert_delegation_tool

        with patch("app.scenarios.work.agent.get_settings", return_value=self._settings_with_coding()):
            tool = make_expert_delegation_tool("test-thread")
            assert tool.name == "delegate_to_expert"

    @pytest.mark.asyncio
    async def test_unknown_expert_returns_error(self) -> None:
        """未知 Expert 返回错误信息。"""
        from app.scenarios.work.agent import make_expert_delegation_tool

        with patch("app.scenarios.work.agent.get_settings", return_value=self._settings_with_coding()):
            tool = make_expert_delegation_tool("test-thread")
            result = await tool.ainvoke({"expert_name": "unknown", "task": "test"})
            assert "错误" in result or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_coding_expert_collects_tokens(self) -> None:
        """coding Expert 的 token 事件被收集为最终文本。"""
        from app.scenarios.work.agent import make_expert_delegation_tool

        async def mock_expert(*args, **kwargs):
            yield {"event": "token", "data": "hello "}
            yield {"event": "token", "data": "world"}

        with patch("app.scenarios.work.agent.get_settings", return_value=self._settings_with_coding()):
            with patch("app.scenarios.coding.agent.run_coding_expert", mock_expert):
                tool = make_expert_delegation_tool("test-thread")
                result = await tool.ainvoke({"expert_name": "coding", "task": "test"})
                assert result == "hello world"


# ============================================================
# 4. Supervisor 构建（mock LLM）
# ============================================================


class TestBuildWorkSupervisor:
    """build_work_supervisor 构建（mock LLM）。"""

    @pytest.mark.asyncio
    async def test_build_returns_compiled_graph(self) -> None:
        """build_work_supervisor 返回编译后的图。"""
        from app.scenarios.work.agent import build_work_supervisor

        mock_model = MagicMock()
        with patch("app.scenarios.work.agent.get_chat_model", return_value=mock_model):
            with patch("app.scenarios.work.agent._make_deep_tools", return_value=[]):
                with patch("app.scenarios.work.agent.make_expert_delegation_tool") as mock_expert_tool:
                    mock_expert_tool.return_value = MagicMock(name="delegate_to_expert")
                    with patch("app.scenarios.work.agent.get_async_checkpointer", new_callable=AsyncMock):
                        with patch("app.scenarios.work.agent.create_agent") as mock_create:
                            mock_agent = MagicMock()
                            mock_create.return_value = mock_agent

                            agent = await build_work_supervisor("test-thread")

                            assert agent is mock_agent
                            mock_create.assert_called_once()
                            call_kwargs = mock_create.call_args.kwargs
                            assert call_kwargs["name"] == "work_supervisor"
                            assert "subagents" in call_kwargs

    @pytest.mark.asyncio
    async def test_build_with_custom_tools(self) -> None:
        """build_work_supervisor 接受自定义 tools 列表。"""
        from app.scenarios.work.agent import build_work_supervisor

        mock_model = MagicMock()
        custom_tools = [MagicMock(name="tool1")]
        custom_subagents = [MagicMock(name="sub1")]

        with patch("app.scenarios.work.agent.get_chat_model", return_value=mock_model):
            with patch("app.scenarios.work.agent.get_async_checkpointer", new_callable=AsyncMock):
                with patch("app.scenarios.work.agent.create_agent") as mock_create:
                    mock_create.return_value = MagicMock()
                    await build_work_supervisor(
                        "test-thread",
                        tools=custom_tools,
                        subagents=custom_subagents,
                    )

                    call_args = mock_create.call_args.args
                    assert call_args[1] == custom_tools  # tools 参数（位置参数）
                    assert mock_create.call_args.kwargs["subagents"] == custom_subagents


# ============================================================
# 5. @mention 强制委派路由
# ============================================================


class TestMentionRouting:
    """@mention 强制委派路由测试。"""

    @pytest.mark.asyncio
    async def test_mention_coding_routes_to_expert(self) -> None:
        """@coding 直接运行 coding Expert，bypass Supervisor。"""
        from app.scenarios.work.agent import run_work_supervisor

        async def mock_expert_stream(*args, **kwargs):
            yield {"event": "token", "data": "expert result"}

        with patch("app.scenarios.coding.agent.run_coding_expert", mock_expert_stream):
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
        from app.scenarios.work.agent import run_work_supervisor

        async def mock_rag_stream(*args, **kwargs):
            yield {"type": "token", "content": "RAG 检索结果"}

        async def mock_supervisor_stream(*args, **kwargs):
            yield {"event": "token", "data": "supervisor synthesis"}

        with patch("app.subagents.rag_agent.run_rag_agent", mock_rag_stream):
            with patch("app.scenarios.work.agent._make_deep_tools", return_value=[]):
                with patch(
                    "app.scenarios.work.agent.make_expert_delegation_tool"
                ) as mock_expert_tool:
                    mock_expert_tool.return_value = MagicMock(name="delegate_to_expert")
                    with patch(
                        "app.scenarios.work.agent._load_mcp_tools",
                        new_callable=AsyncMock,
                        return_value=([], set()),
                    ):
                        with patch(
                            "app.scenarios.work.agent._build_subagent_runnables",
                            return_value=[],
                        ):
                            with patch(
                                "app.scenarios.work.agent.build_work_supervisor",
                                new_callable=AsyncMock,
                            ):
                                with patch(
                                    "app.scenarios.work.agent.run_agent_with_approval",
                                    mock_supervisor_stream,
                                ):
                                    events = []
                                    async for sse in run_work_supervisor(
                                        "@rag 查询文档",
                                        "test-thread",
                                    ):
                                        events.append(sse)

                                    # 应该有 delegation 事件（到 rag）
                                    delegation_events = [
                                        e for e in events if e.get("event") == "delegation"
                                    ]
                                    assert len(delegation_events) >= 1
                                    assert "rag" in delegation_events[0].get("data", "")

                                    # 应该有 Supervisor 合成 token
                                    token_events = [e for e in events if e.get("event") == "token"]
                                    assert len(token_events) >= 1
