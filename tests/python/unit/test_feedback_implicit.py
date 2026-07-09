"""隐式反馈信号测试（FR-9 / T4.9）。

3 种隐式信号：
- implicit_ok（auto_approve + success）
- implicit_bad（aborted）
- implicit_bad（rejected_dangerous_tool）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.observability.feedback import record_implicit_bad, record_implicit_ok
from app.observability.observation import SqliteObservationSink


@pytest.fixture
def tmp_sink(tmp_path: Path) -> SqliteObservationSink:
    return SqliteObservationSink(db_path=tmp_path / "test_implicit.db")


@pytest.fixture
def patched_sink(tmp_sink: SqliteObservationSink):
    with patch(
        "app.observability.feedback.get_observation_sink", return_value=tmp_sink
    ):
        yield tmp_sink


class TestImplicitOk:
    """implicit_ok 信号测试。"""

    @pytest.mark.asyncio
    async def test_implicit_ok_writes_feedback(
        self, patched_sink: SqliteObservationSink
    ) -> None:
        patched_sink.start_run_sync(
            run_id="r_ok", trace_id="r_ok", thread_id="t1",
            agent_mode="work", permission_mode="standard",
            user_message="hi", workspace_path=None,
        )
        await record_implicit_ok("r_ok", reason="auto_approved+success")
        rows = patched_sink.list_feedback_sync("r_ok")
        assert len(rows) == 1
        assert rows[0]["kind"] == "implicit_ok"
        assert rows[0]["comment"] == "auto_approved+success"

    @pytest.mark.asyncio
    async def test_implicit_ok_default_reason(
        self, patched_sink: SqliteObservationSink
    ) -> None:
        patched_sink.start_run_sync(
            run_id="r_ok2", trace_id="r_ok2", thread_id="t1",
            agent_mode="work", permission_mode="standard",
            user_message="hi", workspace_path=None,
        )
        await record_implicit_ok("r_ok2")
        rows = patched_sink.list_feedback_sync("r_ok2")
        assert len(rows) == 1
        assert rows[0]["kind"] == "implicit_ok"
        assert "auto_approved" in rows[0]["comment"]


class TestImplicitBadAborted:
    """implicit_bad (aborted) 信号测试。"""

    @pytest.mark.asyncio
    async def test_implicit_bad_aborted(
        self, patched_sink: SqliteObservationSink
    ) -> None:
        patched_sink.start_run_sync(
            run_id="r_abort", trace_id="r_abort", thread_id="t2",
            agent_mode="work", permission_mode="standard",
            user_message="hi", workspace_path=None,
        )
        await record_implicit_bad("r_abort", reason="aborted")
        rows = patched_sink.list_feedback_sync("r_abort")
        assert len(rows) == 1
        assert rows[0]["kind"] == "implicit_bad"
        assert rows[0]["comment"] == "aborted"


class TestImplicitBadRejected:
    """implicit_bad (rejected_dangerous_tool) 信号测试。"""

    @pytest.mark.asyncio
    async def test_implicit_bad_rejected_tool(
        self, patched_sink: SqliteObservationSink
    ) -> None:
        patched_sink.start_run_sync(
            run_id="r_reject", trace_id="r_reject", thread_id="t3",
            agent_mode="work", permission_mode="standard",
            user_message="hi", workspace_path=None,
        )
        await record_implicit_bad("r_reject", reason="rejected_dangerous_tool")
        rows = patched_sink.list_feedback_sync("r_reject")
        assert len(rows) == 1
        assert rows[0]["kind"] == "implicit_bad"
        assert rows[0]["comment"] == "rejected_dangerous_tool"


class TestImplicitSignalFailureIsolation:
    """隐式信号失败不阻塞主流程（FR-9.3 静默采集）。"""

    @pytest.mark.asyncio
    async def test_sink_failure_does_not_raise(self, patched_sink: SqliteObservationSink) -> None:
        """sink.write_feedback 抛异常时，record_implicit_* 不应抛出。"""
        with patch.object(
            patched_sink, "write_feedback", side_effect=RuntimeError("DB error")
        ):
            # 不应抛出异常
            await record_implicit_ok("r_fail", reason="test")
            await record_implicit_bad("r_fail", reason="test")
