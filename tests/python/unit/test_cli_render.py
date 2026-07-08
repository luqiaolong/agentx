"""cli_render 模块单元测试。"""

from __future__ import annotations

import json
from unittest.mock import patch


from app.cli_render import EventRenderer


class TestEventRendererToken:
    """token 事件渲染。"""

    def test_token_incremental_output(self):
        renderer = EventRenderer()
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "token", "data": "Hello"})
            mock_print.assert_called_once_with("Hello", end="", flush=True)

    def test_token_multiple_chunks(self):
        renderer = EventRenderer()
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "token", "data": "Hello"})
            renderer.render({"event": "token", "data": " World"})
            assert mock_print.call_count == 2


class TestEventRendererReasoning:
    """reasoning 事件渲染。"""

    def test_reasoning_hidden_by_default(self):
        renderer = EventRenderer(verbose=False)
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "reasoning", "data": '{"text": "thinking..."}`}'})
            mock_print.assert_not_called()

    def test_reasoning_shown_in_verbose(self):
        renderer = EventRenderer(verbose=True)
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "reasoning", "data": json.dumps({"text": "thinking..."})})
            mock_print.assert_called_once()


class TestEventRendererToolCall:
    """tool_call 事件渲染。"""

    def test_tool_call_basic(self):
        renderer = EventRenderer()
        data = json.dumps({"name": "web_search", "args": {"query": "test"}, "source": "deep"})
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "tool_call", "data": data})
            assert mock_print.call_count >= 1

    def test_tool_call_verbose_shows_args(self):
        renderer = EventRenderer(verbose=True)
        data = json.dumps({"name": "write_file", "args": {"path": "/tmp/test.py"}, "source": "coding"})
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "tool_call", "data": data})
            # verbose 模式应该打印 args
            assert mock_print.call_count >= 2


class TestEventRendererToolResult:
    """tool_result 事件渲染。"""

    def test_tool_result_truncated(self):
        renderer = EventRenderer(verbose=False)
        long_result = "x" * 3000
        data = json.dumps({"name": "read_file", "result": long_result, "source": "deep"})
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "tool_result", "data": data})
            # 检查输出包含截断标记
            printed_text = str(mock_print.call_args)
            assert "截断" in printed_text or "3000" in printed_text

    def test_tool_result_verbose_no_truncate(self):
        renderer = EventRenderer(verbose=True)
        short_result = "file content"
        data = json.dumps({"name": "read_file", "result": short_result, "source": "deep"})
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "tool_result", "data": data})
            printed_text = str(mock_print.call_args)
            assert "file content" in printed_text


class TestEventRendererApproval:
    """approval_request 事件渲染。"""

    def test_approval_request_shows_tool_name(self):
        renderer = EventRenderer()
        data = json.dumps({
            "tool_name": "write_file",
            "args": {"path": "/tmp/test.py", "content": "print('hello')"},
            "path": "/tmp/test.py",
        })
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "approval_request", "data": data})
            # 应该多次打印（分隔线 + 工具名 + 参数等）
            assert mock_print.call_count >= 3

    def test_approval_request_with_preview(self):
        renderer = EventRenderer()
        data = json.dumps({
            "tool_name": "cli_execute",
            "args": {"command": "rm -rf /"},
            "preview": "This will delete everything",
        })
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "approval_request", "data": data})
            printed = str(mock_print.call_args_list)
            assert "cli_execute" in printed


class TestEventRendererDone:
    """done 事件渲染。"""

    def test_done_prints_newline(self):
        renderer = EventRenderer()
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "done", "data": "{}"})
            mock_print.assert_called_once_with(flush=True)

    def test_done_resets_first_token(self):
        renderer = EventRenderer()
        renderer.render({"event": "token", "data": "hello"})
        assert renderer._first_token is False
        renderer.render({"event": "done", "data": "{}"})
        assert renderer._first_token is True


class TestEventRendererError:
    """error 事件渲染。"""

    def test_error_prints_message(self):
        renderer = EventRenderer()
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "error", "data": "Something went wrong"})
            printed = str(mock_print.call_args)
            assert "Something went wrong" in printed
            assert "[错误]" in printed


class TestEventRendererJsonMode:
    """JSON 输出模式。"""

    def test_json_mode_collects_events(self):
        renderer = EventRenderer(json_mode=True)
        renderer.render({"event": "token", "data": "hello"})
        renderer.render({"event": "done", "data": "{}"})
        assert len(renderer._json_events) == 2

    def test_json_mode_flush(self):
        renderer = EventRenderer(json_mode=True)
        renderer.render({"event": "token", "data": "hello"})
        renderer.render({"event": "done", "data": "{}"})
        with patch("builtins.print") as mock_print:
            renderer.flush_json()
            mock_print.assert_called_once()
            output = mock_print.call_args[0][0]
            parsed = json.loads(output)
            assert len(parsed) == 2
            assert parsed[0]["event"] == "token"
            assert parsed[1]["event"] == "done"

    def test_json_mode_does_not_print_individual_events(self):
        renderer = EventRenderer(json_mode=True)
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "token", "data": "hello"})
            mock_print.assert_not_called()


class TestEventRendererReset:
    """reset 方法。"""

    def test_reset_clears_state(self):
        renderer = EventRenderer(json_mode=True)
        renderer.render({"event": "token", "data": "hello"})
        renderer.reset()
        assert renderer._first_token is True
        assert len(renderer._json_events) == 0


class TestEventRendererUnknownEvent:
    """未知事件类型。"""

    def test_unknown_event_silent_by_default(self):
        renderer = EventRenderer(verbose=False)
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "unknown_type", "data": "test"})
            mock_print.assert_not_called()

    def test_unknown_event_shown_in_verbose(self):
        renderer = EventRenderer(verbose=True)
        with patch("builtins.print") as mock_print:
            renderer.render({"event": "unknown_type", "data": "test"})
            mock_print.assert_called_once()
