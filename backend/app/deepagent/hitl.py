"""LangGraph HITL interrupt/resume mechanics (OpenSpec Decision 6).

Pure helpers for LangGraph HumanInTheLoopMiddleware interrupt/resume,
extracted from ``approval_runner.py`` to separate HITL mechanics from
approval policy:

- Interrupted-state detection (is the graph interrupted?)
- Pending-call extraction (extract pending tool calls from interrupted state)
- Aligned ``Command(resume=...)`` construction (one decision per pending call)
- Approve/reject consumption (consume interrupts properly)
- Message-count progress checks (detect stall: message count not advancing)

Security policy remains in ``app.security.approval.flow``.
The approval session state machine lives in ``approval_session.py``.
"""

from __future__ import annotations

import inspect
from typing import Any

from loguru import logger
from langgraph.types import Command

__all__ = [
    "is_interrupted",
    "get_pending_tool_calls",
    "make_resume_command",
    "make_uniform_resume_command",
    "state_message_count",
]


async def is_interrupted(agent: Any, config: dict) -> bool:
    """Check if agent is paused at an interrupt point.

    LangGraph 1.x ``StateSnapshot`` contains ``interrupts`` field
    (``tuple[Interrupt, ...]``). DeepAgents 0.6.x triggers interrupt via
    ``HumanInTheLoopMiddleware.after_model``; the interrupted task node name is
    ``"HumanInTheLoopMiddleware.after_model"`` (not ``"tools"``). The legacy
    ``"tools" in state.next`` check is retained for backward compatibility with
    the ``interrupt_before=["tools"]`` mechanism.
    """
    state = await agent.aget_state(config)
    if not state:
        return False
    if getattr(state, "interrupts", None):
        return True
    if state.next and "tools" in state.next:
        return True
    return False


async def get_pending_tool_calls(agent: Any, config: dict) -> list[dict[str, Any]]:
    """Extract pending tool calls from the agent's interrupted state."""
    state = await agent.aget_state(config)
    if not state or not state.values:
        return []
    messages = state.values.get("messages", [])
    if not messages:
        return []
    last_msg = messages[-1]
    tool_calls = getattr(last_msg, "tool_calls", None) or []
    return list(tool_calls)


def make_resume_command(decisions: list[dict[str, Any]]) -> Command:
    """Build an aligned ``Command(resume={"decisions": [...]})``.

    Each entry in ``decisions`` corresponds positionally to one pending tool
    call. The caller MUST provide one decision per pending call to avoid
    LangGraph HITL dropping sibling calls.
    """
    return Command(resume={"decisions": decisions})


def make_uniform_resume_command(
    pending_calls: list[dict[str, Any]],
    decision_type: str = "approve",
    message: str = "",
) -> Command:
    """Build a ``Command(resume=...)`` with the same decision for every call.

    LangGraph 1.x ``interrupt()`` must be resumed with ``Command(resume=...)``.
    Passing ``None`` does not consume the interrupt, leaving ``state.interrupts``
    present and triggering stuck-state detection.
    """
    decisions: list[dict[str, Any]] = []
    for _ in pending_calls:
        d: dict[str, Any] = {"type": decision_type}
        if message and decision_type == "reject":
            d["message"] = message
        decisions.append(d)
    return Command(resume={"decisions": decisions})


async def state_message_count(agent: Any, config: dict) -> int:
    """Read the current state's message count for stall detection.

    Returns -1 when the state cannot be read (e.g. test mocks with
    ``aget_state`` returning ``None``). Defensive against ``AsyncMock``
    returning coroutines from ``state.values.get(...)``.
    """
    try:
        state = await agent.aget_state(config)
        if not state or not getattr(state, "values", None):
            return -1
        messages = state.values.get("messages", [])
        if inspect.isawaitable(messages):
            messages = await messages
        return len(messages or [])
    except Exception:  # noqa: BLE001 — read failure must not block main flow
        logger.warning("state_message_count read failed", exc_info=True)
        return -1


def _tool_call_name(tc: dict | object) -> str:
    """Extract tool name from a tool call, handling both dict and object forms.

    LangChain ToolCall runtime type may be either ``dict`` (legacy) or an
    object with attributes (e.g. ``langchain_core.messages.ToolCall``). This
    mirrors the defensive handling already present in ``stream_events.py``.
    """
    if isinstance(tc, dict):
        return tc.get("name", tc.get("tool", ""))
    return getattr(tc, "name", "")
