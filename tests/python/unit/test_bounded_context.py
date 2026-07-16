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
