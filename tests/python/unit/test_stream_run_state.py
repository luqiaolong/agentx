"""Regression tests for shared StreamRunState and exact-once streaming (RED phase).

Covers OpenSpec change ``deepagent-maintainability-refactor`` tasks 2.5–2.6.

Expected behavior (per spec):
- A shared ``StreamRunState`` persists across initial execution and all resume
  streams, tracking ``seen_message_keys``, ``last_todos``, ``processed_message_count``,
  and ``observation_sequence``.
- Parallel ToolMessages with equal content but different ``tool_call_id`` MUST
  each emit a distinct ``tool_result`` SSE event.
- Unchanged todo snapshots replayed on resume MUST NOT produce a duplicate
  ``todo_update`` event.
- ``stream_mode="custom"`` events are forwarded unchanged and do not mutate
  message deduplication state.
- Observation sequence numbers remain strictly increasing across the resume
  boundary; pre-resume observation rows survive.

Current bugs:
- ``_msg_signature`` for ToolMessage uses ``hash(content)`` only (no
  ``tool_call_id``), so parallel ToolMessages with equal content are deduplicated.
- ``_last_todos`` is per-call closure state, not shared across resume → duplicate
  ``todo_update`` on resume.
- ``_seq_counter`` resets to 0 on each ``stream_agent_events`` call → observation
  sequence is NOT monotonic across resume.
- Custom events bypass ``_obs()`` → not recorded in observation sequence.
"""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.deepagent.streaming import stream_agent_events


# ============================================================
# Helpers
# ============================================================


def _make_tool_message(
    name: str, content: str, tool_call_id: str
) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=tool_call_id, name=name)


class _FakeAgent:
    """Mock agent whose ``astream`` yields pre scripted chunks."""

    def __init__(self, chunks: list[tuple[str, Any]]) -> None:
        self._chunks = chunks

    async def astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        for chunk in self._chunks:
            yield chunk


def _values_state(
    messages: list[Any] | None = None,
    todos: list[dict] | None = None,
) -> dict[str, Any]:
    """Build a ``stream_mode="values"`` state payload."""
    state: dict[str, Any] = {}
    if messages is not None:
        state["messages"] = messages
    if todos is not None:
        state["todos"] = todos
    return state


@pytest.fixture(autouse=True)
def _isolate_approval_state() -> None:
    """Clear abort/pause state to avoid cross-test leakage."""
    from app.security.approval import state as approval_state

    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()
    approval_state._pause_flags.clear()
    approval_state._pause_events.clear()
    yield
    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()
    approval_state._pause_flags.clear()
    approval_state._pause_events.clear()


# ============================================================
# 2.5a — Parallel ToolMessages with equal content, different tool_call_id
# ============================================================


@pytest.mark.asyncio
async def test_parallel_tool_results_same_content_distinct_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two ToolMessages with different ``tool_call_id`` but equal content MUST
    each emit a distinct ``tool_result`` SSE event.

    Current ``_msg_signature`` computes ``f"{msg_type}:{hash(content)}:{tc_ids}"``
    where ``tc_ids`` is derived from ``msg.tool_calls`` — but ToolMessage has no
    ``tool_calls`` attribute, so ``tc_ids=""`` for both. Two ToolMessages with
    equal content therefore share the same signature and the second is skipped.

    RED: only one ``tool_result`` event is emitted instead of two.
    """
    # Avoid abort_event lookup hitting real state
    monkeypatch.setattr(
        "app.deepagent.streaming.get_abort_event",
        AsyncMock(return_value=MagicMock(is_set=lambda: False)),
    )

    tool_msg_a = _make_tool_message("read_file", "file contents here", "call-A")
    tool_msg_b = _make_tool_message("read_file", "file contents here", "call-B")

    agent = _FakeAgent([
        ("values", _values_state(messages=[tool_msg_a, tool_msg_b])),
    ])

    events = [
        e
        async for e in stream_agent_events(
            agent,
            {"messages": [HumanMessage(content="hi")]},
            {"configurable": {"thread_id": "t-parallel"}},
        )
    ]

    tool_result_events = [e for e in events if e.get("event") == "tool_result"]
    assert len(tool_result_events) == 2, (
        f"expected 2 distinct tool_result events for parallel ToolMessages with "
        f"different tool_call_id, got {len(tool_result_events)} "
        f"(current dedup uses hash(content) only and collapses them)"
    )


# ============================================================
# 2.5b — Unchanged todo replay on resume produces no duplicate todo_update
# ============================================================


@pytest.mark.asyncio
async def test_unchanged_todo_replay_no_duplicate_on_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a resume stream replays the same todo snapshot, no duplicate
    ``todo_update`` event should be emitted.

    ``stream_agent_events`` is called twice (initial + resume) with a shared
    ``seen_signatures`` set (the current cross-call dedup mechanism). The first
    call emits ``todo_update``; the second call replays the same todos and
    should NOT emit ``todo_update`` again.

    Current bug: ``_last_todos`` is per-call closure state, not shared across
    resume calls, so the second call sees ``_last_todos=[]`` and re-emits the
    unchanged snapshot.

    RED: two ``todo_update`` events are emitted (one per call) instead of one.
    """
    monkeypatch.setattr(
        "app.deepagent.streaming.get_abort_event",
        AsyncMock(return_value=MagicMock(is_set=lambda: False)),
    )

    todos = [{"content": "task1", "status": "pending"}]

    # Initial stream: emits todo_update
    agent_initial = _FakeAgent([
        ("values", _values_state(messages=[AIMessage(content="ok")], todos=todos)),
    ])
    # Resume stream: replays the SAME todos
    agent_resume = _FakeAgent([
        ("values", _values_state(messages=[AIMessage(content="ok")], todos=todos)),
    ])

    shared_seen: set[str] = set()

    initial_events = [
        e
        async for e in stream_agent_events(
            agent_initial,
            {"messages": [HumanMessage(content="hi")]},
            {"configurable": {"thread_id": "t-todo"}},
            seen_signatures=shared_seen,
        )
    ]
    resume_events = [
        e
        async for e in stream_agent_events(
            agent_resume,
            None,  # resume
            {"configurable": {"thread_id": "t-todo"}},
            seen_signatures=shared_seen,
        )
    ]

    initial_todo_updates = [e for e in initial_events if e.get("event") == "todo_update"]
    resume_todo_updates = [e for e in resume_events if e.get("event") == "todo_update"]

    assert len(initial_todo_updates) == 1, "initial stream should emit one todo_update"
    assert len(resume_todo_updates) == 0, (
        f"resume stream should NOT re-emit unchanged todo_update, "
        f"got {len(resume_todo_updates)} (current _last_todos is per-call, not shared)"
    )


# ============================================================
# 2.5c — Custom stream event passthrough does not mutate dedup state
# ============================================================


@pytest.mark.asyncio
async def test_custom_event_passthrough_forwarded_and_observed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``stream_mode="custom"`` event MUST be forwarded unchanged AND recorded
    in the observation sequence (so the sequence stays monotonic).

    Current code forwards custom events via ``yield payload; continue`` — the
    ``continue`` skips ``_obs()``, so custom events are NOT recorded in the
    observation sequence. This creates sequence gaps and breaks the monotonic
    invariant when custom events interleave with message events.

    RED: the observation sink's ``append_event`` is never called for the custom
    event.
    """
    monkeypatch.setattr(
        "app.deepagent.streaming.get_abort_event",
        AsyncMock(return_value=MagicMock(is_set=lambda: False)),
    )
    monkeypatch.setattr(
        "app.deepagent.streaming.current_trace_id", lambda: "run-custom"
    )

    captured_obs: list[tuple[str, int, str, dict]] = []
    fake_sink = MagicMock()
    fake_sink.append_event = AsyncMock(
        side_effect=lambda run_id, seq, etype, payload: captured_obs.append(
            (run_id, seq, etype, payload)
        )
    )
    monkeypatch.setattr(
        "app.deepagent.streaming.get_observation_sink", lambda: fake_sink
    )

    custom_payload = {"event": "custom_thing", "data": "hello"}

    agent = _FakeAgent([
        ("custom", custom_payload),
    ])

    events = [
        e
        async for e in stream_agent_events(
            agent,
            {"messages": [HumanMessage(content="hi")]},
            {"configurable": {"thread_id": "t-custom"}},
        )
    ]

    # Custom event is forwarded unchanged
    assert any(e.get("event") == "custom_thing" for e in events), (
        "custom event must be forwarded unchanged"
    )

    # Custom event must be recorded in observation sequence
    custom_obs = [o for o in captured_obs if o[2] != "todo_update"]
    assert len(custom_obs) >= 1, (
        f"custom event must be recorded in observation sequence, "
        f"got {len(custom_obs)} observation calls (current code skips _obs for custom)"
    )


# ============================================================
# 2.6 — Observation sequence monotonic across resume boundary
# ============================================================


@pytest.mark.asyncio
async def test_observation_sequence_monotonic_across_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observation sequence numbers MUST remain strictly increasing across the
    resume boundary (initial stream → resume stream).

    The spec requires a shared ``StreamRunState.observation_sequence`` that
    persists across initial and resume streams. Current code uses a per-call
    ``_seq_counter`` nonlocal that resets to 0 on each ``stream_agent_events``
    invocation, so the resume stream re-emits seq=1, 2, ... — violating
    monotonicity.

    RED: resume stream's first observation seq is 1 (reset), not strictly
    greater than the initial stream's last seq.
    """
    monkeypatch.setattr(
        "app.deepagent.streaming.get_abort_event",
        AsyncMock(return_value=MagicMock(is_set=lambda: False)),
    )
    monkeypatch.setattr(
        "app.deepagent.streaming.current_trace_id", lambda: "run-seq"
    )

    captured_seq: list[int] = []
    fake_sink = MagicMock()
    fake_sink.append_event = AsyncMock(
        side_effect=lambda run_id, seq, etype, payload: captured_seq.append(seq)
    )
    monkeypatch.setattr(
        "app.deepagent.streaming.get_observation_sink", lambda: fake_sink
    )

    # Initial stream: emits a tool_result (seq=1) + todo_update (seq=2)
    tool_msg = _make_tool_message("read_file", "content", "call-1")
    agent_initial = _FakeAgent([
        ("values", _values_state(messages=[tool_msg], todos=[{"content": "t", "status": "pending"}])),
    ])
    # Resume stream: emits another tool_result
    tool_msg_2 = _make_tool_message("read_file", "content-2", "call-2")
    agent_resume = _FakeAgent([
        ("values", _values_state(messages=[tool_msg_2])),
    ])

    shared_seen: set[str] = set()

    # Initial
    [
        e
        async for e in stream_agent_events(
            agent_initial,
            {"messages": [HumanMessage(content="hi")]},
            {"configurable": {"thread_id": "t-seq"}},
            seen_signatures=shared_seen,
        )
    ]
    initial_seqs = list(captured_seq)

    # Resume
    [
        e
        async for e in stream_agent_events(
            agent_resume,
            None,
            {"configurable": {"thread_id": "t-seq"}},
            seen_signatures=shared_seen,
        )
    ]
    all_seqs = list(captured_seq)

    assert len(initial_seqs) >= 1, "initial stream should have recorded observations"
    assert len(all_seqs) > len(initial_seqs), "resume stream should add observations"

    # Sequence numbers must be strictly increasing across the resume boundary
    for i in range(1, len(all_seqs)):
        assert all_seqs[i] > all_seqs[i - 1], (
            f"observation sequence must be strictly increasing: "
            f"seq[{i - 1}]={all_seqs[i - 1]} >= seq[{i}]={all_seqs[i]} "
            f"(current _seq_counter resets to 0 on resume)"
        )

    # Specifically, the first resume seq must be > last initial seq
    first_resume_seq = all_seqs[len(initial_seqs)]
    last_initial_seq = initial_seqs[-1]
    assert first_resume_seq > last_initial_seq, (
        f"first resume seq ({first_resume_seq}) must be > last initial seq "
        f"({last_initial_seq}); current counter resets to 0 on each call"
    )
