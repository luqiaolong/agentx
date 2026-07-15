"""Approval session state machine (OpenSpec Decision 6 & 7).

Holds run-scoped dependencies and loop state for the approval runner:

- Run dependencies (agent, config, sandbox, settings, etc.) — passed at
  construction time and immutable for the session lifetime.
- Stream state (``StreamRunState`` reference shared across initial + resume
  streams for cross-call dedup and monotonic observation sequence).
- Repeat history (for stall/loop detection — records recent pending_calls
  signatures to detect repeating tool-call patterns).
- Stall counters (consecutive iterations where the graph did not advance).
- Terminal status (completed / terminal / still-interrupted — Decision 7).

The actual approval loop logic lives in ``approval_runner._run_approval_loop``
which uses an ``ApprovalSession`` instance for state management. This keeps
the loop function testable (tests patch module-level names on
``approval_runner``) while making the session state explicit and inspectable.

Security policy remains in ``app.security.approval.flow``.
LangGraph HITL mechanics live in ``hitl.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.deepagent.stream_events import StreamRunState

__all__ = ["ApprovalSession", "ExitState", "LoopExitReason"]


# ============================================================
# Loop exit classification (Decision 7)
# ============================================================


class LoopExitReason:
    """Loop exit reason constants (Decision 7).

    The approval loop exits in one of three states:

    1. ``COMPLETED`` — the graph finished normally (``_is_int`` returned False
       → ``break``). NO error is emitted.
    2. ``TERMINAL`` — the graph terminated due to error, abort, pause, or
       repeat-loop detection. NO iteration-limit error is emitted.
    3. ``STILL_INTERRUPTED`` — the graph is still interrupted at the configured
       approval iteration limit. The max-iteration error is emitted ONCE.
    """

    COMPLETED = "completed"
    TERMINAL = "terminal"
    STILL_INTERRUPTED = "still_interrupted"


@dataclass
class ExitState:
    """Records why the approval loop exited (Decision 7)."""

    reason: str = ""
    iteration: int = 0

    @property
    def is_completed(self) -> bool:
        return self.reason == LoopExitReason.COMPLETED

    @property
    def is_terminal(self) -> bool:
        return self.reason == LoopExitReason.TERMINAL

    @property
    def is_still_interrupted(self) -> bool:
        return self.reason == LoopExitReason.STILL_INTERRUPTED


# ============================================================
# ApprovalSession (Decision 6)
# ============================================================


@dataclass
class ApprovalSession:
    """Run-scoped approval state machine.

    Holds the mutable loop state that was previously scattered across closure
    variables in ``_run_approval_loop``. The session is constructed at the
    start of each ``run_agent_with_approval`` call and discarded after
    completion.

    Fields:
        stream_state: Shared ``StreamRunState`` across initial + resume streams
            (Decision 4). Holds ``seen_message_keys``, ``last_todos``,
            ``processed_message_count``, ``observation_sequence``.
        recent_calls_history: Sliding window of recent ``pending_calls`` lists
            for repeat-loop detection. Only records when the LLM produced new
            messages (``current_msg_count > yielded_msg_count``).
        stalled_count: Consecutive iterations where the graph did not advance
            (``yielded_msg_count <= current_msg_count`` and still interrupted).
        yielded_msg_count: Last known ``state.values.messages`` length after a
            stream completed. Used as the baseline for "did the graph advance?"
        exit_state: Records why the loop exited (Decision 7).
    """

    stream_state: StreamRunState = field(default_factory=StreamRunState)
    recent_calls_history: list[list[dict]] = field(default_factory=list)
    stalled_count: int = 0
    yielded_msg_count: int = -1
    exit_state: ExitState = field(default_factory=ExitState)

    # Repeat/stall detection constants
    REPEAT_DETECTION_WINDOW: int = 3
    REPEAT_THRESHOLD: int = 2
    STALL_THRESHOLD: int = 2

    def record_pending_calls(self, pending_calls: list[dict]) -> None:
        """Record pending calls in the repeat-history window.

        Trims the window to ``REPEAT_DETECTION_WINDOW`` entries.
        """
        self.recent_calls_history.append(pending_calls)
        if len(self.recent_calls_history) > self.REPEAT_DETECTION_WINDOW:
            self.recent_calls_history.pop(0)

    def is_repeating_loop(self) -> bool:
        """Check if recent pending_calls show a repeating tool-call loop.

        Compares call signatures (name + args) across the history window.
        Returns True when ``REPEAT_THRESHOLD`` consecutive matches are found.
        """
        if len(self.recent_calls_history) < self.REPEAT_DETECTION_WINDOW:
            return False

        def _call_signature(calls: list[dict]) -> str:
            import json

            return "|".join(
                f"{c.get('name', '')}:{json.dumps(c.get('args', {}), sort_keys=True, separators=(',', ':'))}"
                for c in calls
            )

        signatures = [_call_signature(c) for c in self.recent_calls_history]
        repeat_count = sum(
            1 for i in range(1, len(signatures)) if signatures[i] == signatures[i - 1]
        )
        return repeat_count >= self.REPEAT_THRESHOLD

    def reset_stall_counter(self) -> None:
        """Reset the consecutive-stall counter (called when graph advances)."""
        self.stalled_count = 0

    def increment_stall(self) -> int:
        """Increment the stall counter and return the new value."""
        self.stalled_count += 1
        return self.stalled_count

    def mark_exit(self, reason: str, iteration: int = 0) -> None:
        """Record the loop exit reason (Decision 7)."""
        self.exit_state.reason = reason
        self.exit_state.iteration = iteration
