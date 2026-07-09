"""coding Expert（coding 场景专家 agent）单元测试。

覆盖 task 3.8：
1. 子代理声明构建（_build_subagents）
2. build_coding_expert 构建（mock LLM）
3. run_coding_expert 基本流程与审批执行层委托
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================
# 1. 子代理声明构建
# ============================================================


class TestBuildSubagents:
    """_build_subagents 子代理声明构建。"""

    def test_returns_subagents_only(self) -> None:
        """只返回子代理声明，不含其他 Expert 或 invoke_agent_team。"""
        from app.agents.expert.coding import _build_subagents

        with patch("app.agents.expert.coding.make_rag_tools", return_value=[]):
            with patch("app.agents.expert.coding.make_web_tools", return_value=[]):
                subagents = _build_subagents("test-thread")

        names = {s["name"] for s in subagents}
        assert "delegate_to_expert" not in names
        assert "invoke_agent_team" not in names

    def test_builtin_rag_web_present(self) -> None:
        """默认启用 rag / web 子代理声明。"""
        from app.agents.expert.coding import _build_subagents

        with patch("app.agents.expert.coding.make_rag_tools", return_value=[]):
            with patch("app.agents.expert.coding.make_web_tools", return_value=[]):
                subagents = _build_subagents("test-thread")

        names = {s["name"] for s in subagents}
        assert "rag" in names
        assert "web" in names

    def test_custom_subagent_included(self) -> None:
        """已启用的自定义子代理被包含。"""
        from app.agents.expert.coding import _build_subagents
        from app.config import CustomSubagentEntry

        custom = {
            "my_custom": CustomSubagentEntry(
                key="my_custom",
                name="My Custom",
                enabled=True,
                tools=["read_file", "rag_retrieve"],
            )
        }

        with patch("app.agents.expert.coding.get_settings") as mock_settings:
            mock_settings.return_value.subagents = {"rag": MagicMock(enabled=True), "web": MagicMock(enabled=True)}
            mock_settings.return_value.custom_subagents = custom
            with patch("app.agents.expert.coding.make_rag_tools", return_value=[]):
                with patch("app.agents.expert.coding.make_web_tools", return_value=[]):
                    with patch("app.subagents.custom_agent._make_custom_tools", return_value=[]):
                        subagents = _build_subagents("test-thread")

        names = {s["name"] for s in subagents}
        assert "my_custom" in names

    def test_forbidden_tools_filtered(self) -> None:
        """自定义子代理工具集过滤 FORBIDDEN_SUBAGENT_TOOLS 中的工具。"""
        from app.agents.expert.coding import _build_subagents
        from app.config import CustomSubagentEntry
        from app.security.dangerous_tools import FORBIDDEN_SUBAGENT_TOOLS

        forbidden = next(iter(FORBIDDEN_SUBAGENT_TOOLS))
        custom = {
            "bad_agent": CustomSubagentEntry(
                key="bad_agent",
                name="Bad Agent",
                enabled=True,
                tools=["read_file", forbidden],
            )
        }

        captured_tool_names: list[list[str]] = []

        def _capture_tools(thread_id: str, tool_names: list[str], workspace_path: str | None) -> list:
            captured_tool_names.append(tool_names)
            return []

        with patch("app.agents.expert.coding.get_settings") as mock_settings:
            mock_settings.return_value.subagents = {"rag": MagicMock(enabled=True), "web": MagicMock(enabled=True)}
            mock_settings.return_value.custom_subagents = custom
            with patch("app.agents.expert.coding.make_rag_tools", return_value=[]):
                with patch("app.agents.expert.coding.make_web_tools", return_value=[]):
                    with patch("app.subagents.custom_agent._make_custom_tools", side_effect=_capture_tools):
                        _build_subagents("test-thread")

        assert len(captured_tool_names) == 1
        assert forbidden not in captured_tool_names[0]
        assert "read_file" in captured_tool_names[0]


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
                with patch("app.agents.expert.coding._build_subagents", return_value=[]):
                    agent = await build_coding_expert("test-thread")

                    assert agent is mock_agent
                    mock_build.assert_called_once()
                    # 验证 scene_prompt 被传入（覆盖默认 _DEEP_SYSTEM_PROMPT）
                    call_kwargs = mock_build.call_args.kwargs
                    assert "scene_prompt" in call_kwargs
                    assert call_kwargs["scene_prompt"]  # 非空

    @pytest.mark.asyncio
    async def test_build_passes_subagents(self) -> None:
        """build_coding_expert 将子代理声明透传给 build_deep_agent。"""
        from app.agents.expert.coding import build_coding_expert

        mock_agent = MagicMock()
        fake_subagents = [{"name": "rag"}, {"name": "web"}]
        with patch("app.agents.expert.coding.build_deep_agent", new_callable=AsyncMock, return_value=mock_agent) as mock_build:
            with patch("app.agents.expert.coding._make_deep_tools", return_value=[]):
                with patch("app.agents.expert.coding._build_subagents", return_value=fake_subagents):
                    await build_coding_expert("test-thread")

                    call_kwargs = mock_build.call_args.kwargs
                    assert call_kwargs.get("subagents") is fake_subagents

    @pytest.mark.asyncio
    async def test_build_with_custom_tools(self) -> None:
        """build_coding_expert 接受自定义 tools 列表。"""
        from app.agents.expert.coding import build_coding_expert

        mock_agent = MagicMock()
        custom_tools = [MagicMock(name="tool1")]

        with patch("app.agents.expert.coding.build_deep_agent", new_callable=AsyncMock, return_value=mock_agent) as mock_build:
            with patch("app.agents.expert.coding._build_subagents", return_value=[]):
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
                with patch("app.agents.expert.coding._build_subagents", return_value=[]):
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
        """run_coding_expert 通过 run_agent_with_approval 流式产出 SSE 事件。"""
        from app.agents.expert.coding import run_coding_expert

        async def mock_approval_loop(*args, **kwargs):
            yield {"event": "token", "data": "hello"}

        with patch("app.agents.expert.coding._make_deep_tools", return_value=[]):
            with patch("app.agents.expert.coding._load_mcp_tools", new_callable=AsyncMock, return_value=([], set())):
                with patch("app.agents.expert.coding.build_coding_expert", new_callable=AsyncMock):
                    with patch("app.agents.expert.coding.run_agent_with_approval", side_effect=mock_approval_loop) as mock_run:
                        events = []
                        async for sse in run_coding_expert(
                            "帮我写代码",
                            "test-thread",
                        ):
                            events.append(sse)

                        assert len(events) >= 1
                        assert events[0]["event"] == "token"
                        assert events[0]["data"] == "hello"
                        # 验证 source 为 coding
                        call_kwargs = mock_run.call_args.kwargs
                        assert call_kwargs["source"] == "coding"

    @pytest.mark.asyncio
    async def test_run_passes_parent_thread_id(self) -> None:
        """parent_thread_id 透传给 run_agent_with_approval。"""
        from app.agents.expert.coding import run_coding_expert

        async def mock_approval_loop(*args, **kwargs):
            yield {"event": "done", "data": ""}

        with patch("app.agents.expert.coding._make_deep_tools", return_value=[]):
            with patch("app.agents.expert.coding._load_mcp_tools", new_callable=AsyncMock, return_value=([], set())):
                with patch("app.agents.expert.coding.build_coding_expert", new_callable=AsyncMock):
                    with patch("app.agents.expert.coding.run_agent_with_approval", side_effect=mock_approval_loop) as mock_run:
                        async for _ in run_coding_expert(
                            "帮我写代码",
                            "test-thread",
                            parent_thread_id="parent-123",
                        ):
                            pass

                        call_kwargs = mock_run.call_args.kwargs
                        assert call_kwargs["parent_thread_id"] == "parent-123"

    @pytest.mark.asyncio
    async def test_run_llm_unavailable_yields_error(self) -> None:
        """LLM 不可用时 yield error 事件。"""
        from app.agents.expert.coding import run_coding_expert

        with patch("app.agents.expert.coding._make_deep_tools", return_value=[]):
            with patch("app.agents.expert.coding._load_mcp_tools", new_callable=AsyncMock, return_value=([], set())):
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
# 4. readonly streak 循环保护（run_agent_with_approval 行为）
# ============================================================


class TestReadonlyStreakProtection:
    """readonly streak 超限循环保护测试。

    feature/sandbox-security-refactor 重构后，审批循环逻辑提取到
    ``app.deep.execution.run_agent_with_approval``。coding Expert 通过
    ``readonly_streak_threshold=10`` 参数启用循环保护。
    """

    @pytest.mark.asyncio
    async def test_readonly_streak_triggers_error_stop(self) -> None:
        """readonly streak 达到阈值后应 yield error 事件并停止循环。"""
        from app.agents.expert.coding import run_coding_expert
        from contextlib import ExitStack
        from types import SimpleNamespace

        # is_interrupted: True for 10 iterations (streak builds up to threshold=10)
        interrupted_values = [True] * 10
        # pending_calls: 10 DIFFERENT readonly calls (streak increments each iteration)
        pending_values = [
            [{"name": "list_dir", "args": {"path": f"dir-{i}"}, "id": f"tc-{i}"}]
            for i in range(1, 11)
        ]

        def _stream_side_effect(*args, **kwargs):
            async def _gen():
                # 初始流和恢复流都不产出 token（streak 保护在第 10 轮触发后 return）
                return
                yield  # pragma: no cover — makes this an async generator

            return _gen()

        with ExitStack() as stack:
            stack.enter_context(patch("app.agents.expert.coding._make_deep_tools", return_value=[]))
            stack.enter_context(patch("app.agents.expert.coding._load_mcp_tools", new_callable=AsyncMock, return_value=([], set())))
            stack.enter_context(patch("app.agents.expert.coding.build_coding_expert", new_callable=AsyncMock))
            stack.enter_context(patch("app.agents.expert.coding._stream_agent_events", side_effect=_stream_side_effect))
            stack.enter_context(patch("app.deep.execution._is_interrupted", new_callable=AsyncMock, side_effect=interrupted_values))
            stack.enter_context(patch("app.agents.expert.coding.get_sandbox", return_value=AsyncMock()))
            # run_agent_with_approval 内部 helper（coding.py 未传自定义实现，使用 execution 默认值）
            stack.enter_context(patch("app.deep.execution._inject_tool_error_for_call", new_callable=AsyncMock))
            stack.enter_context(patch("app.deep.execution._get_pending_tool_calls", new_callable=AsyncMock, side_effect=pending_values))
            stack.enter_context(patch("app.deep.execution.is_paused", new_callable=AsyncMock, return_value=False))
            stack.enter_context(patch("app.deep.execution._handle_directory_extension", new_callable=AsyncMock, return_value=SimpleNamespace(events=[], denied=False, timed_out=False)))

            events = []
            async for sse in run_coding_expert("项目技术栈", "test-streak"):
                events.append(sse)

        # streak 达到 10 → yield error 事件并 return
        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) >= 1, f"expected error events, got: {[e['event'] for e in events]}"
        assert "工具调用次数过多" in error_events[-1]["data"]

    @pytest.mark.asyncio
    async def test_normal_flow_below_threshold_no_protection(self) -> None:
        """正常多工具流程（streak < 10）不应触发 streak 保护。"""
        from app.agents.expert.coding import run_coding_expert
        from contextlib import ExitStack
        from types import SimpleNamespace

        # is_interrupted: True for 3 iterations, then False (LLM answered)
        interrupted_values = [True, True, True, False]
        # pending_calls: 3 DIFFERENT readonly calls (streak=3 < 10)
        pending_values = [
            [{"name": "list_dir", "args": {"path": f"dir-{i}"}, "id": f"tc-{i}"}]
            for i in range(1, 4)
        ]

        stream_calls = {"n": 0}

        def _stream_side_effect(*args, **kwargs):
            stream_calls["n"] += 1
            n = stream_calls["n"]

            async def _gen():
                # call 4 = final resume → yields token (LLM answered)
                if n == 4:
                    yield {"event": "token", "data": "正常回答"}

            return _gen()

        with ExitStack() as stack:
            stack.enter_context(patch("app.agents.expert.coding._make_deep_tools", return_value=[]))
            stack.enter_context(patch("app.agents.expert.coding._load_mcp_tools", new_callable=AsyncMock, return_value=([], set())))
            stack.enter_context(patch("app.agents.expert.coding.build_coding_expert", new_callable=AsyncMock))
            stack.enter_context(patch("app.agents.expert.coding._stream_agent_events", side_effect=_stream_side_effect))
            stack.enter_context(patch("app.deep.execution._is_interrupted", new_callable=AsyncMock, side_effect=interrupted_values))
            stack.enter_context(patch("app.agents.expert.coding.get_sandbox", return_value=AsyncMock()))
            stack.enter_context(patch("app.deep.execution._inject_tool_error_for_call", new_callable=AsyncMock))
            stack.enter_context(patch("app.deep.execution._get_pending_tool_calls", new_callable=AsyncMock, side_effect=pending_values))
            stack.enter_context(patch("app.deep.execution.is_paused", new_callable=AsyncMock, return_value=False))
            stack.enter_context(patch("app.deep.execution._handle_directory_extension", new_callable=AsyncMock, return_value=SimpleNamespace(events=[], denied=False, timed_out=False)))

            events = []
            async for sse in run_coding_expert("简单问题", "test-normal"):
                events.append(sse)

        # Should have token events (normal answer)
        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) >= 1
        assert "正常回答" in token_events[-1]["data"]

        # Should NOT have error events (streak < threshold, no protection triggered)
        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 0
