"""ReadonlyLoopGuardMiddleware 单元测试。

覆盖：
1. ``_count_readonly_streak`` 计数逻辑（连续只读 ToolMessage、混合工具、空列表）
2. ``awrap_model_call`` 阈值未达 → 透传原 request
3. ``awrap_model_call`` 阈值达成 → override tool_choice="none" + 追加 hint
4. ``threshold <= 0`` 禁用保护
5. 非只读 ToolMessage 打断 streak
"""

from __future__ import annotations

from typing import Any
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.deepagent.middleware import ReadonlyLoopGuardMiddleware


def _make_tool_message(name: str, content: str = "ok") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="tc-1", name=name)


def _make_request(messages: list[Any], system_prompt: str = "你是助手") -> Any:
    """构造一个最小可用的 ModelRequest mock。

    使用 SimpleNamespace 模拟 ``ModelRequest`` 的 ``messages`` / ``system_prompt`` /
    ``override`` 接口，避免依赖具体初始化参数（model/runtime 等）。
    """
    captured: dict[str, Any] = {}

    def _override(**overrides: Any) -> Any:
        captured["overrides"] = overrides
        # 返回一个新的 namespace 表示修改后的 request，方便断言
        return SimpleNamespace(
            messages=messages,
            system_prompt=(overrides.get("system_message").content
                           if overrides.get("system_message") is not None else system_prompt),
            tool_choice=overrides.get("tool_choice"),
            captured=captured,
        )

    return SimpleNamespace(
        messages=messages,
        system_prompt=system_prompt,
        tool_choice=None,
        override=_override,
        captured=captured,
    )


class TestCountReadonlyStreak:
    """``_count_readonly_streak`` 计数逻辑测试。"""

    def test_empty_messages_returns_zero(self) -> None:
        mw = ReadonlyLoopGuardMiddleware(threshold=10)
        assert mw._count_readonly_streak([]) == 0

    def test_all_readonly_tools_counted(self) -> None:
        mw = ReadonlyLoopGuardMiddleware(threshold=10)
        messages = [
            HumanMessage(content="hi"),
            AIMessage(content=""),
            _make_tool_message("ls"),
            _make_tool_message("read_file"),
            _make_tool_message("glob"),
            _make_tool_message("grep"),
        ]
        assert mw._count_readonly_streak(messages) == 4

    def test_non_readonly_tool_breaks_streak(self) -> None:
        """非只读工具（如 edit_file）应打断 streak。"""
        mw = ReadonlyLoopGuardMiddleware(threshold=10)
        messages = [
            _make_tool_message("ls"),
            _make_tool_message("edit_file"),  # 非只读 → 打断
            _make_tool_message("read_file"),
        ]
        # 从末尾向前：read_file(只读+1) → edit_file(非只读 → 停止) = 1
        assert mw._count_readonly_streak(messages) == 1

    def test_non_tool_message_breaks_streak(self) -> None:
        """非 ToolMessage（如 AIMessage）应打断 streak。"""
        mw = ReadonlyLoopGuardMiddleware(threshold=10)
        messages = [
            _make_tool_message("ls"),
            _make_tool_message("read_file"),
            AIMessage(content="thinking..."),  # 非 ToolMessage → 打断
            _make_tool_message("glob"),
        ]
        # 从末尾向前：glob(只读+1) → AIMessage(非 ToolMessage → 停止) = 1
        assert mw._count_readonly_streak(messages) == 1

    def test_unnamed_tool_message_breaks_streak(self) -> None:
        """name 为空字符串的 ToolMessage 应打断 streak（保守策略）。"""
        mw = ReadonlyLoopGuardMiddleware(threshold=10)
        unnamed = ToolMessage(content="x", tool_call_id="tc-1", name="")
        messages = [_make_tool_message("ls"), unnamed]
        assert mw._count_readonly_streak(messages) == 0


class TestAwrapModelCall:
    """``awrap_model_call`` 行为测试。"""

    @pytest.mark.asyncio
    async def test_passthrough_when_streak_below_threshold(self) -> None:
        """streak < threshold → 直接调用 handler(request)，不修改。"""
        mw = ReadonlyLoopGuardMiddleware(threshold=10)
        messages = [
            HumanMessage(content="hi"),
            _make_tool_message("ls"),
            _make_tool_message("read_file"),
        ]  # streak = 2 < 10
        request = _make_request(messages)
        handler_called_with: list[Any] = []

        async def _handler(req: Any) -> str:
            handler_called_with.append(req)
            return "model-response"

        result = await mw.awrap_model_call(request, _handler)
        assert result == "model-response"
        assert handler_called_with == [request]  # 原样透传

    @pytest.mark.asyncio
    async def test_forces_tool_choice_none_when_threshold_reached(self) -> None:
        """streak >= threshold → override tool_choice="none" + 追加 hint 到 system prompt。"""
        mw = ReadonlyLoopGuardMiddleware(threshold=3)
        messages = [
            HumanMessage(content="hi"),
            _make_tool_message("ls"),
            _make_tool_message("read_file"),
            _make_tool_message("glob"),
        ]  # streak = 3 == threshold
        request = _make_request(messages, system_prompt="你是助手")
        handler_called_with: list[Any] = []

        async def _handler(req: Any) -> str:
            handler_called_with.append(req)
            return "forced-response"

        result = await mw.awrap_model_call(request, _handler)
        assert result == "forced-response"
        assert len(handler_called_with) == 1
        modified_req = handler_called_with[0]
        assert modified_req.tool_choice == "none"
        # system prompt 应包含原 prompt + hint
        assert "你是助手" in modified_req.system_prompt
        assert "只读工具" in modified_req.system_prompt
        assert "ls/read_file/glob/grep" in modified_req.system_prompt

    @pytest.mark.asyncio
    async def test_exceeds_threshold_triggers_protection(self) -> None:
        """streak > threshold 也应触发保护。"""
        mw = ReadonlyLoopGuardMiddleware(threshold=2)
        messages = [
            _make_tool_message("ls"),
            _make_tool_message("read_file"),
            _make_tool_message("glob"),
            _make_tool_message("grep"),
        ]  # streak = 4 > 2
        request = _make_request(messages)

        async def _handler(req: Any) -> str:
            return "ok"

        await mw.awrap_model_call(request, _handler)
        assert request.captured["overrides"].get("tool_choice") == "none"

    @pytest.mark.asyncio
    async def test_threshold_zero_disables_protection(self) -> None:
        """threshold <= 0 → 完全禁用，直接透传（即使是只读工具 streak 也不拦截）。"""
        mw = ReadonlyLoopGuardMiddleware(threshold=0)
        messages = [_make_tool_message("ls")] * 20  # streak = 20
        request = _make_request(messages)
        handler_called_with: list[Any] = []

        async def _handler(req: Any) -> str:
            handler_called_with.append(req)
            return "passthrough"

        result = await mw.awrap_model_call(request, _handler)
        assert result == "passthrough"
        # 原样透传，handler 收到的是原 request（非 override 结果）
        assert handler_called_with == [request]
        # 没有调用 override
        assert "overrides" not in request.captured or not request.captured["overrides"]

    @pytest.mark.asyncio
    async def test_negative_threshold_disables_protection(self) -> None:
        """threshold < 0 同样禁用保护。"""
        mw = ReadonlyLoopGuardMiddleware(threshold=-1)
        messages = [_make_tool_message("ls")] * 5
        request = _make_request(messages)

        async def _handler(req: Any) -> str:
            return "passthrough"

        result = await mw.awrap_model_call(request, _handler)
        assert result == "passthrough"

    @pytest.mark.asyncio
    async def test_empty_system_prompt_handled(self) -> None:
        """system_prompt 为 None/空时不应崩溃。"""
        mw = ReadonlyLoopGuardMiddleware(threshold=1)
        messages = [_make_tool_message("ls")]  # streak = 1 == threshold
        request = _make_request(messages, system_prompt="")
        request.system_prompt = ""

        async def _handler(req: Any) -> str:
            return "ok"

        # 不应抛异常
        result = await mw.awrap_model_call(request, _handler)
        assert result == "ok"
        # 验证 override 被调用（hint 已追加，原 prompt 为空）
        assert "overrides" in request.captured
        modified_system_message = request.captured["overrides"].get("system_message")
        assert modified_system_message is not None
        assert "只读工具" in modified_system_message.content

    @pytest.mark.asyncio
    async def test_protection_triggered_only_by_readonly_streak(self) -> None:
        """危险工具的 ToolMessage 不应计入只读 streak，不应触发保护。"""
        mw = ReadonlyLoopGuardMiddleware(threshold=3)
        messages = [
            _make_tool_message("ls"),
            _make_tool_message("edit_file"),  # 危险工具 → 打断只读 streak
            _make_tool_message("read_file"),
            _make_tool_message("glob"),
        ]  # 从末尾向前：glob(只读+1) → read_file(只读+2) → edit_file(非只读 → 停止) = 2 < 3
        request = _make_request(messages)
        handler_called_with: list[Any] = []

        async def _handler(req: Any) -> str:
            handler_called_with.append(req)
            return "passthrough"

        result = await mw.awrap_model_call(request, _handler)
        assert result == "passthrough"
        # 原样透传，没有 override
        assert handler_called_with == [request]
        assert "overrides" not in request.captured or not request.captured["overrides"]
