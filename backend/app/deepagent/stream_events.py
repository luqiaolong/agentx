"""Stateful event mapper for DeepAgent SSE streaming (OpenSpec Decision 4 & 5).

Splits the streaming logic out of ``streaming.py`` into:

- ``StreamRunState``: dataclass shared across initial/resume streams tracking
  ``seen_message_keys``, ``last_todos``, ``processed_message_count``,
  ``observation_sequence``.
- Pure helpers: ``normalize_message_content``, ``make_message_key``.
- ``StreamEventMapper``: stateful mapper that converts LangGraph chunks
  (custom/values/messages) into SSE events, recording observations.
- ``adapt_seen_signatures``: compatibility adapter binding legacy
  ``seen_signatures`` set to a ``StreamRunState`` for cross-call sharing.

The public driver ``stream_agent_events`` in ``streaming.py`` remains a thin
wrapper that owns the ``agent.astream`` loop and abort handling, delegating
per-chunk conversion to ``StreamEventMapper``.

Bug fixes (tasks 2.5–2.6):
- ``make_message_key`` uses ``hashlib.sha256`` (deterministic) instead of
  ``hash()`` (process-random), and includes ``ToolMessage.tool_call_id`` so
  parallel ToolMessages with equal content but different IDs produce distinct
  keys (fixes collapsed parallel tool results).
- ``last_todos`` lives on ``StreamRunState`` (shared across resume) instead of
  a per-call closure, preventing duplicate ``todo_update`` on resume.
- ``observation_sequence`` lives on ``StreamRunState`` (shared across resume)
  instead of a per-call nonlocal, preserving strict monotonicity across the
  resume boundary.
- Custom stream events now go through ``_obs()`` before yielding, closing
  observation sequence gaps.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable
from uuid import uuid4

from loguru import logger

from app.config import get_settings
from app.observability.observation import get_observation_sink
from app.sse.events import (
    make_sse_event,
    make_todo_update_event,
    make_tool_call_event,
    make_tool_result_event,
)
from app.utils.text import (
    ThinkFilter,
    extract_chunk_text,
    split_think,
    strip_think,
    strip_tool_call_xml,
)

__all__ = [
    "StreamRunState",
    "StreamEventMapper",
    "normalize_message_content",
    "make_message_key",
    "adapt_seen_signatures",
]


# ============================================================
# StreamRunState (Decision 4)
# ============================================================


@dataclass
class StreamRunState:
    """Shared state across initial execution and all resume streams.

    Fields (per OpenSpec Decision 4):
    - seen_message_keys: dedup set for message keys (replaces legacy
      ``seen_signatures``).
    - last_todos: last seen todo snapshot (tuple for safe equality), used
      for diff-based ``todo_update`` emission.
    - processed_message_count: tracks processed messages to detect new ones
      when LangGraph emits cumulative ``messages`` list.
    - observation_sequence: monotonic counter for ``observation_event`` rows;
      incremented before each ``append_event`` call.
    """

    seen_message_keys: set[str] = field(default_factory=set)
    last_todos: tuple[Any, ...] = ()
    processed_message_count: int = 0
    observation_sequence: int = 0


# ============================================================
# Pure helpers (Decision 5)
# ============================================================


def normalize_message_content(content: Any) -> str:
    """Normalize message content to a plain string for digest and display.

    Handles ``str``, ``list`` of str/dict blocks (LangChain multimodal), and
    other types by ``str()``-ifying. Empty/None returns ``""``.
    """
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block if isinstance(block, str)
            else block.get("text", "") if isinstance(block, dict)
            else ""
            for block in content
        )
    return str(content)


def make_message_key(msg: Any) -> str:
    """Deterministic dedup key for a message (replaces ``_msg_signature``).

    Key parts (per Decision 4):
    - message type name
    - message ``id`` when present
    - ``ToolMessage.tool_call_id`` (distinguishes parallel tool results with
      equal content — regression: original ``hash(content)`` collapsed them)
    - ``AIMessage.tool_calls`` IDs (distinguishes AI tool-call batches)
    - ``sha256`` digest of normalized content (deterministic, NOT
      process-random ``hash()``)
    """
    msg_type = type(msg).__name__
    msg_id = getattr(msg, "id", None) or ""
    parts: list[str] = [msg_type, str(msg_id)]

    # ToolMessage.tool_call_id — distinguishes parallel tool results
    tool_call_id = getattr(msg, "tool_call_id", "")
    if tool_call_id:
        parts.append(f"tc:{tool_call_id}")

    # AIMessage.tool_calls — distinguish AI tool-call batches
    tcs = getattr(msg, "tool_calls", None) or []
    if tcs:
        tc_ids = "|".join(
            str(tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", ""))
            for tc in tcs
        )
        if tc_ids:
            parts.append(f"tcs:{tc_ids}")

    content_str = normalize_message_content(getattr(msg, "content", ""))
    if content_str:
        digest = hashlib.sha256(content_str.encode("utf-8")).hexdigest()[:16]
        parts.append(f"c:{digest}")

    return ":".join(parts)


# ============================================================
# Compatibility adapter (Decision 4 — migration path)
# ============================================================


# Module-level cache keyed by ``id(seen_signatures)`` for test compatibility.
# Tests share state across ``stream_agent_events`` calls via ``seen_signatures``
# only (no ``stream_run_state`` kwarg). The cache binds a ``seen_signatures``
# set to a ``StreamRunState`` so ``observation_sequence`` and ``last_todos``
# persist across calls.
# In production, ``approval_runner`` passes ``stream_run_state`` explicitly,
# so this cache is unused.
# The cache holds a reference to the set (via ``state.seen_message_keys``),
# preventing GC and id reuse while the entry exists.
_seen_signatures_state_cache: dict[int, StreamRunState] = {}


def adapt_seen_signatures(
    seen_signatures: set[str] | None,
    stream_run_state: StreamRunState | None,
) -> StreamRunState:
    """Resolve the effective ``StreamRunState`` from new kwarg or legacy arg.

    Priority:
    1. ``stream_run_state`` provided → use it directly (production path).
    2. ``seen_signatures`` provided → look up cache by ``id(seen_signatures)``:
       - Hit: reuse cached state (shares ``observation_sequence``,
         ``last_todos``, ``processed_message_count``).
       - Miss: create new state with ``seen_message_keys = seen_signatures``,
         cache it.
    3. Neither provided → create a fresh one-shot state (not cached).
    """
    if stream_run_state is not None:
        return stream_run_state

    if seen_signatures is not None:
        key = id(seen_signatures)
        cached = _seen_signatures_state_cache.get(key)
        if cached is not None:
            return cached
        state = StreamRunState(seen_message_keys=seen_signatures)
        _seen_signatures_state_cache[key] = state
        return state

    return StreamRunState()


# ============================================================
# StreamEventMapper (Decision 5)
# ============================================================


class StreamEventMapper:
    """Stateful mapper: converts LangGraph chunks to SSE events.

    Owns:
    - Per-AIMessage ``ThinkFilter`` (reset after each complete AIMessage).
    - Delegation to ``StreamRunState`` for cross-call dedup and observation seq.

    The driver (``stream_agent_events``) owns the ``astream`` loop and abort
    handling; this class only converts each chunk.

    Args:
        source: SSE event source identifier (e.g. ``"work"``, ``"coding"``).
        thread_id: Current thread ID (used as ``task_id`` in todo_update).
        state: Shared ``StreamRunState`` for cross-call dedup/observation.
        run_id: Trace/run ID for observation sink. Empty disables recording.
        sink_lookup: Callable returning the observation sink. Defaults to
            ``get_observation_sink``; the driver passes its own import so
            test monkeypatches on ``app.deepagent.streaming`` take effect.
    """

    def __init__(
        self,
        source: str,
        thread_id: str,
        state: StreamRunState,
        run_id: str = "",
        sink_lookup: Callable[[], Any] | None = None,
    ) -> None:
        self.source = source
        self.thread_id = thread_id
        self.state = state
        self.run_id = run_id
        self._sink_lookup = sink_lookup or get_observation_sink

        # Per-AIMessage state — reset after each complete AIMessage
        self._content_filter: ThinkFilter | None = None
        self._messages_mode_seen = False
        self._token_pushed = False

        # Diagnostics
        self._astream_start = asyncio.get_event_loop().time()
        self._first_state_seen = False

    # ---- Observation recording ----

    async def _obs(self, event_type: str, payload: dict[str, Any]) -> None:
        """Append to observation sink; never blocks SSE on failure.

        Increments ``state.observation_sequence`` BEFORE append so the sequence
        stays monotonic across resume (Decision 4). Failures are swallowed at
        diagnostic level to preserve SSE isolation (FR-2.3).
        """
        if not self.run_id:
            return
        self.state.observation_sequence += 1
        try:
            await self._sink_lookup().append_event(
                self.run_id,
                self.state.observation_sequence,
                event_type,
                payload,
            )
        except Exception:  # noqa: BLE001 — observation failure must not block SSE
            pass

    # ---- Per-AIMessage state reset ----

    def _reset_per_aimessage(self) -> None:
        """Reset ``ThinkFilter`` and token-pushed flag after a complete AIMessage.

        Per Decision 5: ThinkFilter is per-AIMessage state that must reset
        after each complete AIMessage so the next message starts fresh.
        """
        self._content_filter = None
        self._token_pushed = False

    # ---- Custom stream events ----

    async def process_custom(
        self, payload: Any
    ) -> AsyncIterator[dict[str, str]]:
        """Forward a ``stream_mode="custom"`` event unchanged AND record observation.

        Bug fix (task 2.5c): original code did ``yield payload; continue``,
        skipping ``_obs()``. This created sequence gaps and broke monotonicity
        when custom events interleaved with message events.
        """
        if isinstance(payload, dict) and "event" in payload:
            await self._obs(
                payload.get("event", "custom"),
                {"data": payload.get("data", ""), "source": self.source},
            )
            yield payload

    # ---- messages stream mode ----

    async def process_messages_chunk(
        self, payload: Any
    ) -> AsyncIterator[dict[str, str]]:
        """Process a ``stream_mode="messages"`` chunk (``AIMessageChunk``).

        Splits ``<think>...</think>`` blocks via ``ThinkFilter``:
        - think content → ``reasoning_delta`` event (live incremental)
        - visible text → ``token`` event (live incremental, ``max_hold``
          buffered to avoid leaking ``<think>`` tag prefixes that span chunks)

        The tail buffer is flushed by the values-mode ``AIMessage`` handler
        (``_process_ai_message``).
        """
        from langchain_core.messages import AIMessageChunk

        self._messages_mode_seen = True

        if isinstance(payload, tuple) and len(payload) >= 1:
            msg_chunk = payload[0]
        else:
            msg_chunk = payload
        if not isinstance(msg_chunk, AIMessageChunk):
            return

        raw_text = extract_chunk_text(msg_chunk, strip=False)
        if not raw_text:
            return

        if self._content_filter is None:
            self._content_filter = ThinkFilter(
                max_hold=get_settings().think_filter_max_hold,
                retain_think=True,
            )

        visible_delta = self._content_filter.feed(raw_text)
        reasoning_delta = self._content_filter.take_think()
        if reasoning_delta:
            await self._obs(
                "reasoning_delta",
                {"delta": reasoning_delta, "source": self.source},
            )
            yield make_sse_event(
                "reasoning_delta",
                {"delta": reasoning_delta, "source": self.source},
            )
        if visible_delta:
            self._token_pushed = True
            await self._obs("token", {"content": visible_delta, "live": True})
            yield make_sse_event("token", visible_delta)

    # ---- values stream mode ----

    async def process_values_state(
        self, state: Any
    ) -> AsyncIterator[dict[str, str]]:
        """Process a ``stream_mode="values"`` state snapshot.

        - Emits ``todo_update`` when ``state.todos`` changes vs ``last_todos``.
        - Processes new messages (since ``processed_message_count``) and emits
          ``token`` / ``reasoning`` / ``tool_call`` / ``tool_result`` /
          ``token_rollback`` events.
        - Deduplicates via ``state.seen_message_keys`` (cross-call shared).
        """
        from langchain_core.messages import AIMessage, ToolMessage

        if not self._first_state_seen:
            self._first_state_seen = True
            elapsed = asyncio.get_event_loop().time() - self._astream_start
            logger.info(
                "stream_agent_events: first state arrived after {elapsed:.2f}s",
                thread_id=self.thread_id,
                elapsed=elapsed,
                source=self.source,
            )

        current_todos = state.get("todos", []) if hasattr(state, "get") else []
        current_todos_tuple = tuple(current_todos)
        if current_todos_tuple != self.state.last_todos:
            await self._obs(
                "todo_update",
                {
                    "todos": current_todos,
                    "task_id": self.thread_id,
                    "source": self.source,
                },
            )
            yield make_todo_update_event(
                current_todos, task_id=self.thread_id, source=self.source
            )
            self.state.last_todos = current_todos_tuple

        messages = state.get("messages", []) if hasattr(state, "get") else []
        if not messages:
            logger.debug("stream_agent_events: empty messages, skipping")
            return

        # Process new messages (from processed_message_count onwards).
        # Defensive: if processed_count >= len(messages) (test _FakeAgent
        # returns single-message state, not LangGraph cumulative), fall back
        # to messages[-1].
        if self.state.processed_message_count >= len(messages):
            new_messages = [messages[-1]]
        else:
            new_messages = messages[self.state.processed_message_count:]
        self.state.processed_message_count = len(messages)

        if not new_messages:
            logger.debug(
                "stream_agent_events: no new messages msg_count={msg_count} source={source}",
                msg_count=len(messages),
                source=self.source,
            )
            return

        for msg in new_messages:
            key = make_message_key(msg)
            if key in self.state.seen_message_keys:
                logger.debug(
                    "stream_agent_events: duplicate message skipped key={key} msg_type={msg_type}",
                    key=key,
                    msg_type=type(msg).__name__,
                )
                continue
            self.state.seen_message_keys.add(key)

            msg_type = type(msg).__name__
            _tool_name = ""
            _tc_count = 0
            if isinstance(msg, ToolMessage):
                _tool_name = getattr(msg, "name", "") or ""
            elif isinstance(msg, AIMessage):
                _tc_count = len(getattr(msg, "tool_calls", []) or [])
            logger.info(
                "stream_agent_events: msg_type={msg_type} msg_count={msg_count}"
                " tool_name={tool_name} tc_count={tc_count} source={source}",
                msg_type=msg_type,
                msg_count=len(messages),
                tool_name=_tool_name,
                tc_count=_tc_count,
                source=self.source,
            )

            if isinstance(msg, ToolMessage):
                async for event in self._process_tool_message(msg):
                    yield event
            elif isinstance(msg, AIMessage):
                async for event in self._process_ai_message(msg):
                    yield event
                # Reset per-AIMessage state after full processing (Decision 5)
                self._reset_per_aimessage()

    async def _process_tool_message(
        self, msg: Any
    ) -> AsyncIterator[dict[str, str]]:
        """Emit ``tool_result`` SSE for a ``ToolMessage``."""
        tool_name = getattr(msg, "name", "") or ""
        tool_call_id = getattr(msg, "tool_call_id", "") or str(uuid4())
        content = getattr(msg, "content", "")
        logger.debug(
            "stream_agent_events: ToolMessage name={tool_name} tool_call_id={tool_call_id}"
            " content_len={content_len} source={source}",
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            content_len=len(content) if isinstance(content, str) else 0,
            source=self.source,
        )
        content_str = normalize_message_content(content)
        await self._obs(
            "tool_result",
            {
                "id": tool_call_id,
                "name": tool_name,
                "result": content_str,
                "source": self.source,
            },
        )
        yield make_tool_result_event(
            tool_call_id, tool_name, content_str, source=self.source
        )
        logger.info(
            "stream_agent_events: yielded tool_result",
            thread_id=self.thread_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            source=self.source,
        )

    async def _process_ai_message(
        self, msg: Any
    ) -> AsyncIterator[dict[str, str]]:
        """Emit ``reasoning`` + ``tool_call`` (if tool_calls) OR ``token`` (final answer)."""
        tc_count = len(getattr(msg, "tool_calls", []) or [])
        content_preview = str(msg.content)[:100] if msg.content else ""
        logger.debug(
            "stream_agent_events: AIMessage tc_count={tc_count}"
            " content_preview={content_preview} source={source}",
            tc_count=tc_count,
            content_preview=content_preview,
            source=self.source,
        )
        if getattr(msg, "tool_calls", None):
            # AIMessage with tool_calls → reasoning + tool_call(s)
            plan_text = strip_tool_call_xml(normalize_message_content(msg.content))
            reasoning, visible = split_think(plan_text)
            if self._messages_mode_seen:
                # <think> already pushed via reasoning_delta, visible text
                # pushed via token. If token was mis-pushed (model emitted
                # visible text before tool_calls), rollback and re-emit as
                # reasoning.
                if self._token_pushed:
                    await self._obs("token_rollback", {})
                    yield make_sse_event("token_rollback", {})
                    self._token_pushed = False
                display_plan = visible.strip()
            else:
                # No messages mode (test stub or old model): prefer think
                # content for display, else visible plan text.
                display_plan = (
                    reasoning.strip() if reasoning.strip() else visible.strip()
                )
            if display_plan:
                await self._obs(
                    "reasoning",
                    {"content": display_plan, "source": self.source},
                )
                yield make_sse_event(
                    "reasoning",
                    {"content": display_plan, "source": self.source},
                )
            for tc in msg.tool_calls:
                if isinstance(tc, dict):
                    tc_name = tc.get("name", tc.get("tool", "unknown"))
                    tc_args = tc.get("args", {}) or {}
                    tc_id = tc.get("id") or str(uuid4())
                else:
                    tc_name = getattr(tc, "name", "unknown")
                    tc_args = getattr(tc, "args", {}) or {}
                    tc_id = getattr(tc, "id", None) or str(uuid4())
                await self._obs(
                    "tool_call",
                    {
                        "id": tc_id,
                        "name": tc_name,
                        "args": tc_args,
                        "source": self.source,
                    },
                )
                yield make_tool_call_event(
                    tc_id, tc_name, tc_args, source=self.source
                )
        elif getattr(msg, "content", ""):
            # AIMessage without tool_calls → final answer token
            if self._messages_mode_seen and self._content_filter is not None:
                # messages mode already pushed most visible text; flush tail
                tail = self._content_filter.flush()
                if tail:
                    tail = strip_tool_call_xml(tail)
                    if tail:
                        await self._obs("token", {"content": tail, "live": False})
                        yield make_sse_event("token", tail)
            else:
                # No messages mode (test stub): emit full content as one token
                text = strip_think(normalize_message_content(msg.content))
                text = strip_tool_call_xml(text)
                if text:
                    await self._obs("token", {"content": text})
                    yield make_sse_event("token", text)
