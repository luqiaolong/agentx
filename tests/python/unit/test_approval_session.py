"""Regression tests for approval session HITL semantics (RED phase).

Covers OpenSpec change ``deepagent-maintainability-refactor`` tasks 2.8–2.10.

Expected behavior (per spec):
- One LangGraph HITL decision per pending tool call, preserving per-call
  approval results.
- Approving a ``request_permission`` call MUST NOT implicitly approve a sibling
  dangerous tool call (e.g. ``write_file``) that was not presented for approval.
- When sandbox authorization fails after user approved ``request_permission``,
  the permission tool outcome MUST report the failure (NOT ``路径已授权``).
- Reaching ``max_iterations`` emits an error ONLY when the run is still
  interrupted at the limit, NOT when the run completed on the final iteration.

Current bugs:
- The permission_calls branch calls
  ``_make_hitl_resume_decisions(pending_calls, decision_type="approve")`` which
  approves ALL pending calls including sibling dangerous calls (2.8).
- After ``authorize_temp`` raises, the code injects an error ToolMessage but
  still resumes with ``approve``, causing the ``request_permission`` tool body
  to execute and return ``路径已授权`` (2.9).
- The ``if iteration >= max_iterations`` check emits ``达到最大迭代上限`` even
  when the graph completed (broke out of the loop via ``break``) on the last
  allowed iteration (2.10).
"""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.deepagent.approval_runner import run_agent_with_approval
from app.security.approval import ApprovalDecision, ApprovalResult
from app.security.approval.request import ApprovalRequest

import app.deepagent.approval_runner as exec_module


# ============================================================
# Shared helpers
# ============================================================


def _make_approval_req(approval_id: str = "apr-1", run_id: str = "run-1") -> ApprovalRequest:
    return ApprovalRequest(
        approval_id=approval_id,
        thread_id="t1",
        run_id=run_id,
        request_kind="directory_extension",
        tool_call_id="tc-1",
        created_at=0.0,
        expires_at=999999.0,
        consumed_at=None,
    )


def _make_agent() -> MagicMock:
    agent = MagicMock()
    agent.aget_state = AsyncMock(return_value=None)
    return agent


def _make_sandbox() -> MagicMock:
    sandbox = MagicMock()
    sandbox.is_path_authorized = AsyncMock(return_value=False)
    sandbox.authorize_temp = AsyncMock()
    sandbox.authorize = AsyncMock()
    sandbox.clear_temp = AsyncMock()
    sandbox.set_full_trust = AsyncMock()
    return sandbox


def _common_kwargs(agent: Any, sandbox: Any, **overrides: Any) -> dict:
    kwargs = {
        "thread_id": "t1",
        "workspace_path": None,
        "permission_mode": "standard",
        "runtime_dangerous": set(),
        "source": "deep",
        "inputs": {"messages": []},
        "sandbox": sandbox,
    }
    kwargs.update(overrides)
    return kwargs


def _apply_common_mocks(monkeypatch: pytest.MonkeyPatch, approval_req: ApprovalRequest | None = None):
    req = approval_req or _make_approval_req()
    monkeypatch.setattr(exec_module, "register_approval_request", AsyncMock(return_value=req))
    monkeypatch.setattr(exec_module, "is_paused", AsyncMock(return_value=False))
    monkeypatch.setattr(exec_module, "is_aborted", AsyncMock(return_value=False))
    monkeypatch.setattr(exec_module, "_resolve_max_wait", MagicMock(return_value=300.0))
    return req


def _make_tracked_stream():
    """Track stream_fn calls and their inputs."""
    call_inputs: list[Any] = []

    async def stream_fn(agent: Any, inputs: Any, config: dict, source: str) -> AsyncIterator[dict[str, str]]:
        call_inputs.append(inputs)
        if len(call_inputs) == 1:
            yield {"event": "token", "data": "ok"}

    return stream_fn, call_inputs


# ============================================================
# 2.8 — Mixed request_permission + write_file batch
# ============================================================


@pytest.mark.asyncio
async def test_mixed_permission_and_write_call_does_not_implicitly_approve_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When pending calls contain both ``request_permission`` and ``write_file``
    in one model message, approving the permission request MUST NOT implicitly
    approve the sibling ``write_file`` call.

    The ``write_file`` call should be rejected or deferred with an explicit
    retry message, and MUST NOT execute without a subsequent approval check.

    Current bug: the permission_calls branch calls
    ``_make_hitl_resume_decisions(pending_calls, decision_type="approve")`` which
    approves ALL pending calls — including the unpresented ``write_file``.

    RED: the resume Command approves all pending_calls, so write_file is
    implicitly approved.
    """
    _apply_common_mocks(monkeypatch)
    monkeypatch.setattr(
        exec_module,
        "_await_approval",
        AsyncMock(return_value=ApprovalResult(decision=ApprovalDecision.APPROVE)),
    )

    agent = _make_agent()
    sandbox = _make_sandbox()
    stream_fn, stream_inputs = _make_tracked_stream()
    inject_call = AsyncMock()

    call_count = 0

    async def is_int_fn(a: Any, c: dict) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count == 1

    pending_calls = [
        {
            "name": "request_permission",
            "args": {"path": "/tmp/data", "writable": True, "reason": "need write"},
            "id": "tc-perm",
        },
        {
            "name": "write_file",
            "args": {"path": "/tmp/data/file.txt", "content": "hello"},
            "id": "tc-write",
        },
    ]

    events = [
        evt
        async for evt in run_agent_with_approval(
            agent,
            {"configurable": {"thread_id": "t1"}},
            **_common_kwargs(
                agent,
                sandbox,
                stream_fn=stream_fn,
                is_interrupted_fn=is_int_fn,
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                inject_tool_error_for_call_fn=inject_call,
                runtime_dangerous={"write_file"},
            ),
        )
    ]

    # The resume Command should NOT approve the write_file call.
    # It should either reject or defer it with a retry message.
    assert len(stream_inputs) >= 2, "stream_fn should be called for initial + resume"
    resume_command = stream_inputs[1]

    from langgraph.types import Command

    assert isinstance(resume_command, Command), "resume input should be a Command"
    resume_value = resume_command.resume
    if isinstance(resume_value, dict):
        decisions = resume_value.get("decisions", [])
    else:
        decisions = getattr(resume_value, "decisions", [])

    assert len(decisions) == 2, f"expected 2 decisions (one per pending call), got {len(decisions)}"

    # The write_file decision (index 1) must NOT be "approve"
    write_decision = decisions[1]
    write_decision_type = write_decision.get("type") if isinstance(write_decision, dict) else getattr(write_decision, "type", "")
    assert write_decision_type != "approve", (
        f"sibling write_file call must NOT be implicitly approved; "
        f"got decision type='{write_decision_type}'. "
        f"It should be 'reject' or deferred with a retry message."
    )

    # write_file should not execute without a subsequent approval check.
    # The inject_call should have been called for the write_file call with a
    # retry/defer message.
    inject_calls_made = inject_call.call_args_list
    write_injected = any(
        ca.args[2].get("id") == "tc-write"
        for ca in inject_calls_made
        if len(ca.args) >= 3
    )
    assert write_injected, (
        "write_file call should have an injected error/retry ToolMessage, "
        "not be silently approved"
    )


# ============================================================
# 2.9 — Authorization failure reports failure, not 路径已授权
# ============================================================


@pytest.mark.asyncio
async def test_authorization_failure_reports_failure_not_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When sandbox authorization raises after user approved ``request_permission``,
    the permission tool outcome MUST report the authorization failure.

    The agent MUST NOT receive a successful ``路径已授权`` result for that call.

    Current bug: after ``authorize_temp`` raises, the code injects an error
    ToolMessage BUT still resumes with ``approve``, causing the
    ``request_permission`` tool body to execute and return
    ``路径已授权: {path}``.

    RED: the tool body executes and returns ``路径已授权`` despite authorization
    failure.
    """
    _apply_common_mocks(monkeypatch)
    monkeypatch.setattr(
        exec_module,
        "_await_approval",
        AsyncMock(return_value=ApprovalResult(decision=ApprovalDecision.APPROVE)),
    )

    agent = _make_agent()
    sandbox = _make_sandbox()
    # authorize_temp raises → authorization failure
    sandbox.authorize_temp = AsyncMock(side_effect=RuntimeError("disk quota exceeded"))

    stream_fn, stream_inputs = _make_tracked_stream()
    inject_call = AsyncMock()

    call_count = 0

    async def is_int_fn(a: Any, c: dict) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count == 1

    pending_calls = [
        {
            "name": "request_permission",
            "args": {"path": "/tmp/protected", "writable": True, "reason": "test"},
            "id": "tc-perm",
        },
    ]

    events = [
        evt
        async for evt in run_agent_with_approval(
            agent,
            {"configurable": {"thread_id": "t1"}},
            **_common_kwargs(
                agent,
                sandbox,
                stream_fn=stream_fn,
                is_interrupted_fn=is_int_fn,
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                inject_tool_error_for_call_fn=inject_call,
            ),
        )
    ]

    # The injected error ToolMessage should report the authorization failure
    inject_call.assert_awaited()
    injected_msg = inject_call.call_args.args[3]
    assert "失败" in injected_msg or "授权" in injected_msg, (
        f"injected ToolMessage should report authorization failure, got: {injected_msg}"
    )

    # The resume should NOT approve the tool call (which would cause the tool
    # body to return 路径已授权). Instead it should be rejected or the tool
    # should not execute.
    assert len(stream_inputs) >= 2, "stream_fn should be called for initial + resume"
    resume_command = stream_inputs[1]

    from langgraph.types import Command

    assert isinstance(resume_command, Command), "resume input should be a Command"
    resume_value = resume_command.resume
    if isinstance(resume_value, dict):
        decisions = resume_value.get("decisions", [])
    else:
        decisions = getattr(resume_value, "decisions", [])

    perm_decision = decisions[0] if decisions else {}
    perm_decision_type = perm_decision.get("type") if isinstance(perm_decision, dict) else getattr(perm_decision, "type", "")
    assert perm_decision_type != "approve", (
        f"request_permission should NOT be resumed as 'approve' when authorization "
        f"failed (would cause tool body to return 路径已授权); "
        f"got decision type='{perm_decision_type}'"
    )

    # Verify temporary grants are cleared after the failure
    sandbox.clear_temp.assert_awaited()


# ============================================================
# 2.10a — Run completes on the last allowed iteration → no limit error
# ============================================================


@pytest.mark.asyncio
async def test_run_completes_on_last_iteration_no_limit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the graph clears its interrupt during the final permitted approval
    iteration, the run completes WITHOUT emitting ``达到最大迭代上限``.

    Scenario with max_iterations=1:
    - Iteration 1: interrupted → process approval → resume → graph completes
    - After resume, ``_is_int`` returns False → ``break`` out of the loop
    - ``iteration == 1 == max_iterations`` but the run COMPLETED, so no error

    Current bug: ``if iteration >= max_iterations`` emits the error regardless
    of whether the loop broke due to completion or due to hitting the limit.

    RED: the error ``达到最大迭代上限`` is emitted even though the run completed.
    """
    _apply_common_mocks(monkeypatch)
    monkeypatch.setattr(
        exec_module,
        "_await_approval",
        AsyncMock(return_value=ApprovalResult(decision=ApprovalDecision.APPROVE)),
    )
    monkeypatch.setattr(
        exec_module,
        "_handle_directory_extension",
        AsyncMock(return_value=MagicMock(events=[], denied=False, timed_out=False)),
    )

    agent = _make_agent()
    sandbox = _make_sandbox()

    # is_interrupted: True on iteration 1 (trigger approval), then False (completed)
    int_call_count = 0

    async def is_int_fn(a: Any, c: dict) -> bool:
        nonlocal int_call_count
        int_call_count += 1
        # Iteration 1: interrupted (first check returns True)
        # After resume: check again returns False → break
        return int_call_count == 1

    pending_calls = [
        {"name": "read_file", "args": {"path": "/tmp/a.txt"}, "id": "tc-1"},
    ]

    stream_fn, _ = _make_tracked_stream()

    events = [
        evt
        async for evt in run_agent_with_approval(
            agent,
            {"configurable": {"thread_id": "t1"}},
            **_common_kwargs(
                agent,
                sandbox,
                stream_fn=stream_fn,
                is_interrupted_fn=is_int_fn,
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                max_iterations=1,
            ),
        )
    ]

    # No max-iteration error should be emitted because the run completed
    error_events = [e for e in events if e.get("event") == "error"]
    limit_errors = [
        e
        for e in error_events
        if "达到最大迭代上限" in e.get("data", "") or "达到最大迭代上限" in str(e.get("data", ""))
    ]
    assert len(limit_errors) == 0, (
        f"run completed on the last allowed iteration but still emitted "
        f"max-iteration error: {limit_errors}"
    )


# Note: A 2.10b test (run remains interrupted at limit → error emitted) was
# removed during RED phase because the current code already emits the error
# correctly in that scenario — it is not a bug. The actual bug is covered by
# 2.10a (error emitted even when the run COMPLETED on the last iteration).
