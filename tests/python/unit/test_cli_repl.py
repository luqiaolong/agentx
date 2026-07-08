"""app.cli.repl 模块单元测试。

覆盖 consume_events：
- 正常事件流完成（返回 True）
- error 事件返回 False
- approval_request 事件触发 handle_approval
- 异常事件流返回 False
- KeyboardInterrupt 在非 REPL 模式下被吞，REPL 模式下重新抛出
- 空事件流直接返回 True
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.cli.repl import consume_events


async def _aiter(items):
    """构造 async iterator，从列表产出。"""
    for item in items:
        yield item


# ============================================================
# consume_events 测试
# ============================================================

class TestConsumeEventsNormal:
    """正常事件流。"""

    @pytest.mark.asyncio
    async def test_normal_completion(self):
        """正常 token + done 返回 True。"""
        events = [
            {"event": "token", "data": "Hello"},
            {"event": "done", "data": "{}"},
        ]
        renderer = MagicMock()
        result = await consume_events(_aiter(events), renderer, "test-thread")
        assert result is True
        # 应该对所有事件调用 render
        assert renderer.render.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_stream(self):
        """空事件流返回 True。"""
        renderer = MagicMock()
        result = await consume_events(_aiter([]), renderer, "test-thread")
        assert result is True
        renderer.render.assert_not_called()

    @pytest.mark.asyncio
    async def test_done_event_resets_first_token(self):
        """done 事件被正确渲染。"""
        events = [
            {"event": "token", "data": "x"},
            {"event": "done", "data": "{}"},
        ]
        renderer = MagicMock()
        await consume_events(_aiter(events), renderer, "test-thread")
        # 验证 done 被传入 render
        rendered_events = [call.args[0] for call in renderer.render.call_args_list]
        events_seen = [e["event"] for e in rendered_events]
        assert "token" in events_seen
        assert "done" in events_seen


class TestConsumeEventsError:
    """error 事件。"""

    @pytest.mark.asyncio
    async def test_error_event_returns_false(self):
        events = [
            {"event": "token", "data": "Hello"},
            {"event": "error", "data": "Something failed"},
        ]
        renderer = MagicMock()
        result = await consume_events(_aiter(events), renderer, "test-thread")
        assert result is False

    @pytest.mark.asyncio
    async def test_error_event_still_rendered(self):
        """error 事件本身也被渲染。"""
        events = [
            {"event": "error", "data": "fail"},
        ]
        renderer = MagicMock()
        await consume_events(_aiter(events), renderer, "test-thread")
        assert renderer.render.call_count == 1


class TestConsumeEventsApproval:
    """approval_request 事件。"""

    @pytest.mark.asyncio
    async def test_approval_request_triggers_handler(self):
        """approval_request 事件触发 handle_approval 并继续。"""
        events = [
            {"event": "approval_request", "data": '{"tool_name": "write_file"}'},
            {"event": "done", "data": "{}"},
        ]
        renderer = MagicMock()
        with patch("app.cli.repl.handle_approval", new_callable=AsyncMock) as mock_handle:
            result = await consume_events(_aiter(events), renderer, "test-thread")
            assert result is True
            mock_handle.assert_awaited_once_with("test-thread")

    @pytest.mark.asyncio
    async def test_approval_request_rendered_before_handler(self):
        """approval_request 事件先被渲染，再调用 handle_approval。"""
        events = [
            {"event": "approval_request", "data": '{"tool_name": "write_file"}'},
            {"event": "done", "data": "{}"},
        ]
        renderer = MagicMock()
        with patch("app.cli.repl.handle_approval", new_callable=AsyncMock):
            await consume_events(_aiter(events), renderer, "test-thread")
        # approval_request 和 done 都被渲染
        assert renderer.render.call_count == 2

    @pytest.mark.asyncio
    async def test_approval_request_does_not_stop_flow(self):
        """approval_request 后续事件继续处理。"""
        events = [
            {"event": "approval_request", "data": "{}"},
            {"event": "token", "data": "after-approval"},
            {"event": "done", "data": "{}"},
        ]
        renderer = MagicMock()
        with patch("app.cli.repl.handle_approval", new_callable=AsyncMock):
            result = await consume_events(_aiter(events), renderer, "test-thread")
            assert result is True
        assert renderer.render.call_count == 3


class TestConsumeEventsExceptions:
    """异常路径。"""

    @pytest.mark.asyncio
    async def test_general_exception_returns_false(self, capsys):
        """事件流抛出异常返回 False。"""
        renderer = MagicMock()

        async def gen():
            yield {"event": "token", "data": "x"}
            raise RuntimeError("boom")

        result = await consume_events(gen(), renderer, "test-thread")
        assert result is False
        err = capsys.readouterr().err
        assert "boom" in err

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_non_repl_returns_false(self, capsys):
        """非 REPL 模式下 KeyboardInterrupt 返回 False。"""
        renderer = MagicMock()

        async def gen():
            yield {"event": "token", "data": "x"}
            raise KeyboardInterrupt

        result = await consume_events(gen(), renderer, "test-thread", is_repl=False)
        assert result is False
        out = capsys.readouterr().out
        assert "中断" in out

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_repl_reraises(self):
        """REPL 模式下 KeyboardInterrupt 被重新抛出。"""
        renderer = MagicMock()

        async def gen():
            yield {"event": "token", "data": "x"}
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            await consume_events(gen(), renderer, "test-thread", is_repl=True)

    @pytest.mark.asyncio
    async def test_event_missing_event_key(self):
        """event 字段缺失时仍正常处理。"""
        events = [
            {"data": "no-event-key"},
            {"event": "done", "data": "{}"},
        ]
        renderer = MagicMock()
        result = await consume_events(_aiter(events), renderer, "test-thread")
        assert result is True
        assert renderer.render.call_count == 2


class TestConsumeEventsMultipleApproval:
    """多次审批请求。"""

    @pytest.mark.asyncio
    async def test_multiple_approval_requests_all_handled(self):
        events = [
            {"event": "approval_request", "data": "{}"},
            {"event": "approval_request", "data": "{}"},
            {"event": "done", "data": "{}"},
        ]
        renderer = MagicMock()
        with patch("app.cli.repl.handle_approval", new_callable=AsyncMock) as mock_handle:
            result = await consume_events(_aiter(events), renderer, "test-thread")
            assert result is True
            assert mock_handle.await_count == 2
