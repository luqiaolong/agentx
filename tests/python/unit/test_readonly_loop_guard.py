"""Regression tests for ReadonlyLoopGuardMiddleware streak counting (RED phase).

Covers OpenSpec change ``deepagent-maintainability-refactor`` task 2.4.

Expected behavior (per spec):
- Loop protection SHALL evaluate real ReAct message order: alternating
  ``AIMessage(tool_calls=[...])`` → ``ToolMessage(...)`` rounds.
- A "completed read-only round" = an AIMessage that initiated read-only tool
  calls followed by the corresponding ToolMessage(s).
- The streak counts trailing completed read-only rounds, NOT consecutive
  ToolMessages.
- A non-read-only tool (write_file / edit_file / delete_file / execute / unknown)
  in any trailing round resets the streak.

The current ``ReadonlyLoopGuardMiddleware._count_readonly_streak`` counts
consecutive ``ToolMessage`` objects from the end of the list, stopping at the
first non-ToolMessage. In a real ReAct history the messages alternate
``AIMessage(tc) → ToolMessage → AIMessage(tc) → ToolMessage``, so the streak
collapses to at most 1 (each ToolMessage is preceded by an AIMessage, which
breaks the scan). This means the guard NEVER fires on realistic histories.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.deepagent.middleware import ReadonlyLoopGuardMiddleware


def _ai_with_readonly_call(tool_name: str, tc_id: str = "tc-1") -> AIMessage:
    """An AIMessage that calls a single read-only tool."""
    return AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": {}, "id": tc_id}],
    )


def _ai_with_write_call(tool_name: str = "write_file", tc_id: str = "tc-w") -> AIMessage:
    """An AIMessage that calls a write tool."""
    return AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": {"path": "/tmp/x"}, "id": tc_id}],
    )


def _tool_result(tool_name: str, tc_id: str = "tc-1") -> ToolMessage:
    return ToolMessage(content="ok", tool_call_id=tc_id, name=tool_name)


def _make_request(messages: list[Any], system_prompt: str = "你是助手") -> Any:
    """Minimal ModelRequest mock with override support."""
    captured: dict[str, Any] = {}

    def _override(**overrides: Any) -> Any:
        captured["overrides"] = overrides
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


# ============================================================
# 2.4a — Alternating read-only ReAct rounds reach the threshold
# ============================================================


class TestAlternatingReadonlyStreak:
    """Real ReAct rounds: ``AIMessage(tc) → ToolMessage`` pairs."""

    def test_alternating_readonly_rounds_reach_threshold(self) -> None:
        """Three completed read-only rounds (ls, read_file, glob) should produce
        a streak of 3, meeting threshold=3 and forcing ``tool_choice="none"``.

        Realistic message order:
            HumanMessage
            AIMessage(tc=[ls])      ← round 1 start
            ToolMessage(ls)
            AIMessage(tc=[read_file]) ← round 2 start
            ToolMessage(read_file)
            AIMessage(tc=[glob])     ← round 3 start
            ToolMessage(glob)

        Current implementation scans from the end and stops at the first
        non-ToolMessage (the AIMessage before the last ToolMessage), giving
        streak=1 instead of 3.

        RED: current streak == 1, threshold == 3 → guard does NOT fire.
        """
        mw = ReadonlyLoopGuardMiddleware(threshold=3)
        messages = [
            HumanMessage(content="find the file"),
            _ai_with_readonly_call("ls", "tc-1"),
            _tool_result("ls", "tc-1"),
            _ai_with_readonly_call("read_file", "tc-2"),
            _tool_result("read_file", "tc-2"),
            _ai_with_readonly_call("glob", "tc-3"),
            _tool_result("glob", "tc-3"),
        ]

        # The streak of completed read-only rounds should be 3
        streak = mw._count_readonly_streak(messages)
        assert streak == 3, (
            f"expected streak=3 from 3 completed read-only ReAct rounds, "
            f"got streak={streak} (current impl counts consecutive ToolMessages "
            f"and stops at the preceding AIMessage)"
        )

    @pytest.mark.asyncio
    async def test_guard_fires_on_alternating_readonly_streak(self) -> None:
        """When the alternating read-only streak meets the threshold, the guard
        MUST override ``tool_choice="none"``.

        RED: current guard does not fire because streak collapses to 1.
        """
        mw = ReadonlyLoopGuardMiddleware(threshold=3)
        messages = [
            HumanMessage(content="hi"),
            _ai_with_readonly_call("ls", "tc-1"),
            _tool_result("ls", "tc-1"),
            _ai_with_readonly_call("read_file", "tc-2"),
            _tool_result("read_file", "tc-2"),
            _ai_with_readonly_call("glob", "tc-3"),
            _tool_result("glob", "tc-3"),
        ]
        request = _make_request(messages)

        async def _handler(req: Any) -> str:
            return "forced"

        await mw.awrap_model_call(request, _handler)

        assert "overrides" in request.captured, (
            "guard should have called override to force tool_choice='none'"
        )
        assert request.captured["overrides"].get("tool_choice") == "none", (
            "guard should force tool_choice='none' when read-only streak meets threshold"
        )

# ============================================================
# 2.4b — Non-read-only tool resets the streak
# ============================================================


class TestNonReadonlyResetsStreak:
    """A write tool in the middle of read-only rounds must reset the streak."""

    def test_write_tool_in_middle_resets_streak(self) -> None:
        """Two read-only rounds, then a write round, then one read-only round.

        Realistic message order:
            AIMessage(tc=[ls])       ← read-only
            ToolMessage(ls)
            AIMessage(tc=[read_file]) ← read-only
            ToolMessage(read_file)
            AIMessage(tc=[write_file]) ← WRITE (resets streak)
            ToolMessage(write_file)
            AIMessage(tc=[glob])     ← read-only (streak starts fresh = 1)
            ToolMessage(glob)

        After the write round, the streak should be 1 (only the last glob round),
        NOT 3.

        RED: current impl counts consecutive ToolMessages from the end. The last
        4 messages are ``ToolMessage(write_file), AIMessage(glob), ToolMessage(glob)``
        — wait, actually the current impl would give streak=1 here too (stops at
        AIMessage before the last ToolMessage). So this test may pass by accident.

        To make the reset test meaningful, we put MULTIPLE read-only ToolMessages
        after the write to show the streak doesn't include pre-write rounds.
        """
        mw = ReadonlyLoopGuardMiddleware(threshold=10)
        messages = [
            HumanMessage(content="hi"),
            _ai_with_readonly_call("ls", "tc-1"),
            _tool_result("ls", "tc-1"),
            _ai_with_readonly_call("read_file", "tc-2"),
            _tool_result("read_file", "tc-2"),
            _ai_with_write_call("write_file", "tc-w"),
            _tool_result("write_file", "tc-w"),
            _ai_with_readonly_call("glob", "tc-3"),
            _tool_result("glob", "tc-3"),
            _ai_with_readonly_call("grep", "tc-4"),
            _tool_result("grep", "tc-4"),
        ]

        streak = mw._count_readonly_streak(messages)
        # After write_file round, only 2 read-only rounds remain (glob + grep)
        assert streak == 2, (
            f"expected streak=2 after write reset (glob+grep rounds only), "
            f"got streak={streak}"
        )
