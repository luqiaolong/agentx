"""Regression tests for bounded execution context binding (RED phase).

Covers OpenSpec change ``deepagent-maintainability-refactor`` task 2.7.

Expected behavior (per spec):
- ``bind_agent_context(thread_id, parent_thread_id)`` is a context manager that
  binds ``current_thread_id`` and ``current_parent_thread_id`` ContextVars for
  the lifetime of an agent run only.
- ContextVar tokens MUST be restored on:
  - Normal completion
  - Exception
  - Cancellation (asyncio.CancelledError)
  - Async-generator ``aclose()``
- Concurrent runs with different thread/parent IDs observe only their own IDs.

Current bug: ``context.py`` has NO ``bind_agent_context`` function — only bare
``ContextVar`` definitions. The approval runner sets ``current_parent_thread_id``
directly via ``.set()`` without token restoration, and ``bind_trace`` is called
as a bare function (not entered with ``with``).

RED: ``ImportError`` — ``bind_agent_context`` does not exist in
``app.deepagent.context``.
"""

from __future__ import annotations

import asyncio

import pytest

from app.deepagent.context import current_parent_thread_id, current_thread_id


# ============================================================
# 2.7a — Concurrent thread/parent isolation
# ============================================================


@pytest.mark.asyncio
async def test_concurrent_runs_isolate_thread_ids() -> None:
    """Two concurrent agent runs with different thread/parent IDs MUST observe
    only their own IDs in ContextVars.

    RED: ``bind_agent_context`` does not exist → ImportError.
    """
    from app.deepagent.context import bind_agent_context  # noqa: F401

    observed: dict[str, dict[str, str | None]] = {}

    async def _run(thread_id: str, parent_thread_id: str | None) -> None:
        with bind_agent_context(thread_id, parent_thread_id):
            # Yield control to let the other coroutine interleave
            await asyncio.sleep(0.01)
            observed[thread_id] = {
                "thread_id": current_thread_id.get(),
                "parent_thread_id": current_parent_thread_id.get(),
            }

    await asyncio.gather(
        _run("thread-A", "parent-A"),
        _run("thread-B", "parent-B"),
    )

    assert observed["thread-A"]["thread_id"] == "thread-A"
    assert observed["thread-A"]["parent_thread_id"] == "parent-A"
    assert observed["thread-B"]["thread_id"] == "thread-B"
    assert observed["thread-B"]["parent_thread_id"] == "parent-B"


# ============================================================
# 2.7b — Normal restoration after exit
# ============================================================


@pytest.mark.asyncio
async def test_normal_exit_restores_contextvars() -> None:
    """After ``bind_agent_context`` exits normally, the previous ContextVar
    values MUST be restored.

    RED: ``bind_agent_context`` does not exist → ImportError.
    """
    from app.deepagent.context import bind_agent_context  # noqa: F401

    # Set baseline values
    current_thread_id.set("baseline-thread")
    current_parent_thread_id.set("baseline-parent")

    with bind_agent_context("run-thread", "run-parent"):
        assert current_thread_id.get() == "run-thread"
        assert current_parent_thread_id.get() == "run-parent"

    assert current_thread_id.get() == "baseline-thread", (
        "previous thread_id must be restored after normal exit"
    )
    assert current_parent_thread_id.get() == "baseline-parent", (
        "previous parent_thread_id must be restored after normal exit"
    )


# ============================================================
# 2.7c — Exception restoration
# ============================================================


@pytest.mark.asyncio
async def test_exception_exit_restores_contextvars() -> None:
    """If an exception is raised inside the ``bind_agent_context`` block, the
    previous ContextVar values MUST still be restored.

    RED: ``bind_agent_context`` does not exist → ImportError.
    """
    from app.deepagent.context import bind_agent_context  # noqa: F401

    current_thread_id.set("baseline-thread")
    current_parent_thread_id.set("baseline-parent")

    with pytest.raises(RuntimeError, match="boom"):
        with bind_agent_context("run-thread", "run-parent"):
            raise RuntimeError("boom")

    assert current_thread_id.get() == "baseline-thread", (
        "previous thread_id must be restored after exception"
    )
    assert current_parent_thread_id.get() == "baseline-parent", (
        "previous parent_thread_id must be restored after exception"
    )


# ============================================================
# 2.7d — Cancellation restoration
# ============================================================


@pytest.mark.asyncio
async def test_cancellation_restores_contextvars() -> None:
    """If the coroutine is cancelled inside the ``bind_agent_context`` block,
    the previous ContextVar values MUST be restored.

    RED: ``bind_agent_context`` does not exist → ImportError.
    """
    from app.deepagent.context import bind_agent_context  # noqa: F401

    current_thread_id.set("baseline-thread")
    current_parent_thread_id.set("baseline-parent")

    async def _cancelled_run() -> None:
        with bind_agent_context("run-thread", "run-parent"):
            await asyncio.sleep(10)  # will be cancelled

    task = asyncio.create_task(_cancelled_run())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert current_thread_id.get() == "baseline-thread", (
        "previous thread_id must be restored after cancellation"
    )
    assert current_parent_thread_id.get() == "baseline-parent", (
        "previous parent_thread_id must be restored after cancellation"
    )


# ============================================================
# 2.7e — Async-generator aclose() restoration
# ============================================================


@pytest.mark.asyncio
async def test_async_generator_aclose_restores_contextvars() -> None:
    """If the caller closes the async generator early (``aclose()``), the
    previous ContextVar values MUST be restored.

    This models the SSE generator being closed before the agent completes.

    RED: ``bind_agent_context`` does not exist → ImportError.
    """
    from app.deepagent.context import bind_agent_context  # noqa: F401

    current_thread_id.set("baseline-thread")
    current_parent_thread_id.set("baseline-parent")

    async def _sse_generator() -> str:
        with bind_agent_context("run-thread", "run-parent"):
            try:
                yield "event1"
                await asyncio.sleep(10)  # will be aclosed
            except asyncio.CancelledError:
                raise
            finally:
                pass

    gen = _sse_generator()
    first = await gen.__anext__()
    assert first == "event1"
    await gen.aclose()

    assert current_thread_id.get() == "baseline-thread", (
        "previous thread_id must be restored after async generator aclose()"
    )
    assert current_parent_thread_id.get() == "baseline-parent", (
        "previous parent_thread_id must be restored after async generator aclose()"
    )


# ============================================================
# 2.7f — Cross-task async-generator aclose() (regression)
# ============================================================


@pytest.mark.asyncio
async def test_async_generator_aclose_in_different_context_no_error() -> None:
    """Regression: ``aclose()`` invoked from a different ``contextvars.Context``
    (e.g. Starlette spins a fresh task to close a StreamingResponse generator
    after client disconnect) MUST NOT raise
    ``ValueError: <Token ...> was created in a different Context``.

    Previously ``bind_agent_context`` called ``ContextVar.reset(token)``
    unconditionally in ``finally``; when the token was created in the original
    task's Context but ``finally`` runs in the closer task's Context, Python
    raised ``ValueError`` and the error surfaced as 内部错误 to the user.

    After fix: ``_safe_reset`` swallows the ``ValueError``; the closer task's
    own ContextVars are untouched (the original set was scoped to the original
    Context and cannot leak across Contexts).
    """
    from app.deepagent.context import bind_agent_context

    # Closer task baseline — must remain unchanged after cross-Context aclose.
    current_thread_id.set("closer-task-thread")
    current_parent_thread_id.set("closer-task-parent")

    async def _sse_generator() -> str:
        with bind_agent_context("run-thread", "run-parent"):
            yield "event1"
            await asyncio.sleep(10)  # will be aclosed before completing

    gen = _sse_generator()

    async def _producer() -> None:
        # Runs in its own task → its own Context copy.
        # ``set`` happens here, creating a token bound to THIS Context.
        first = await gen.__anext__()
        assert first == "event1"
        # Keep generator suspended; do NOT close it here.

    producer_task = asyncio.create_task(_producer())
    await asyncio.sleep(0.01)
    await producer_task  # let producer finish stepping; generator still suspended

    # ``aclose`` runs in the current (closer) task's Context — different from
    # the producer's Context where the token was created. Before the fix this
    # raised ValueError; after the fix it completes cleanly.
    await gen.aclose()

    assert current_thread_id.get() == "closer-task-thread", (
        "closer task's own ContextVar must be untouched by cross-Context aclose"
    )
    assert current_parent_thread_id.get() == "closer-task-parent", (
        "closer task's own parent ContextVar must be untouched by cross-Context aclose"
    )
