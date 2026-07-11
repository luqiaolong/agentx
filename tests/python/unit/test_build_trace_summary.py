"""_build_trace_summary 单元测试。"""
from __future__ import annotations

import json
from typing import Any

import pytest

# 预加载顺序：langsmith 必须先于 observation 加载，否则 circular import 会失败
#（langsmith.py L165 延迟导入 observation.get_observation_sink，但 observation.py L36
#  顶部导入 langsmith.redact；若 observation 先加载，L165 执行时 get_observation_sink 尚未定义）
import app.observability.langsmith  # noqa: F401
import app.observability.observation  # noqa: F401
from app.api.observation import _build_trace_summary  # noqa: E402


def _make_event(seq: int, et: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq": seq,
        "event_type": et,
        "payload_json": json.dumps(payload, ensure_ascii=False),
    }


def _make_run(**overrides: Any) -> dict[str, Any]:
    base = {
        "run_id": "test-run",
        "agent_mode": "coding",
        "user_message": "测试消息",
        "duration_ms": 5000,
        "error_type": None,
        "error_message": None,
    }
    base.update(overrides)
    return base


@pytest.fixture
def build_summary():
    """返回 _build_trace_summary 函数。"""
    return _build_trace_summary


class TestBuildTraceSummary:
    """_build_trace_summary 应正确合并碎片事件并完整输出。"""

    def test_empty_events(self, build_summary) -> None:
        summary = build_summary(None, [])
        assert "事件流（共 0 条）" in summary

    def test_run_metadata(self, build_summary) -> None:
        run = _make_run(error_type="TestError", error_message="出错了")
        summary = build_summary(run, [])
        assert "run_id: test-run" in summary
        assert "agent_mode: coding" in summary
        assert "user_message: 测试消息" in summary
        assert "error_type: TestError" in summary
        assert "error_message: 出错了" in summary

    def test_reasoning_delta_merged(self, build_summary) -> None:
        """连续 reasoning_delta 应合并为一个块。"""
        events = [
            _make_event(1, "reasoning_delta", {"delta": "第一"}),
            _make_event(2, "reasoning_delta", {"delta": "段"}),
            _make_event(3, "reasoning_delta", {"delta": "话"}),
            _make_event(4, "tool_start", {"tool": "shell"}),
        ]
        summary = build_summary(None, events)
        assert "reasoning_delta(合并3条)" in summary
        assert "第一段话" in summary
        assert "[1-3]" in summary
        assert "[4] tool_start" in summary

    def test_token_merged(self, build_summary) -> None:
        """连续 token 应合并为一个块。"""
        events = [
            _make_event(1, "token", {"content": "Hello ", "live": False}),
            _make_event(2, "token", {"content": "World", "live": False}),
            _make_event(3, "tool_end", {"result": "done"}),
        ]
        summary = build_summary(None, events)
        assert "token(合并2条)" in summary
        assert "Hello World" in summary
        assert "[1-2]" in summary

    def test_token_rollback_filters_live_tokens(self, build_summary) -> None:
        """token_rollback 应撤回之前 live=true 的 token 事件。"""
        events = [
            _make_event(1, "token", {"content": "撤回的", "live": True}),
            _make_event(2, "token_rollback", {}),
            _make_event(3, "token", {"content": "保留的", "live": False}),
        ]
        summary = build_summary(None, events)
        assert "撤回的" not in summary
        assert "保留的" in summary
        # token_rollback 本身不输出
        assert "token_rollback" not in summary

    def test_non_consecutive_reasoning_not_merged(self, build_summary) -> None:
        """非连续的 reasoning_delta 不应合并。"""
        events = [
            _make_event(1, "reasoning_delta", {"delta": "AAAA"}),
            _make_event(2, "tool_start", {"tool": "x"}),
            _make_event(3, "reasoning_delta", {"delta": "BBBB"}),
        ]
        summary = build_summary(None, events)
        # 两个独立的 reasoning_delta 块
        assert summary.count("reasoning_delta(合并1条)") == 2
        assert "AAAA" in summary
        assert "BBBB" in summary

    def test_no_truncation_for_full_trace(self, build_summary) -> None:
        """708 条事件的完整 trace 不应被截断。"""
        events = []
        for i in range(1, 709):
            events.append(_make_event(i, "reasoning_delta", {"delta": f"r{i}"}))
        summary = build_summary(None, events)
        assert "…(后续事件截断" not in summary
        assert "708" in summary

    def test_merged_block_truncation(self, build_summary) -> None:
        """合并后的块超长应截断到 _MERGED_BLOCK_LIMIT。"""
        events = [
            _make_event(1, "reasoning_delta", {"delta": "X" * 3000}),
        ]
        summary = build_summary(None, events)
        assert "…(截断)" in summary

    def test_other_event_payload_truncated(self, build_summary) -> None:
        """非合并类事件的 payload 超长应截断。"""
        events = [
            _make_event(1, "tool_result", {"result": "Y" * 1000}),
        ]
        summary = build_summary(None, events)
        assert "…(截断)" in summary

    def test_final_text_rebuilt(self, build_summary) -> None:
        """最终输出应从 token 事件重建。"""
        events = [
            _make_event(1, "token", {"content": "最终", "live": False}),
            _make_event(2, "token", {"content": "输出", "live": False}),
        ]
        summary = build_summary(None, events)
        assert "## 最终输出" in summary
        assert "最终输出" in summary

    def test_final_text_skips_rolled_back(self, build_summary) -> None:
        """最终输出应跳过被撤回的 live token。"""
        events = [
            _make_event(1, "token", {"content": "撤回的", "live": True}),
            _make_event(2, "token_rollback", {}),
            _make_event(3, "token", {"content": "保留的", "live": False}),
        ]
        summary = build_summary(None, events)
        assert "## 最终输出" in summary
        assert "保留的" in summary
        assert "撤回的" not in summary
