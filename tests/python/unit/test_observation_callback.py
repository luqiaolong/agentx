"""ObservationCallback 单元测试（FR-2 / NFR-5）。

测试覆盖：
- Callback 钩子触发条件（on_llm_start / on_tool_start / on_tool_end / on_chain_end）
- args redact 行为（敏感字段被替换）
- 异常隔离（callback 抛错不影响调用方）
- seq 自增（同 run 内递增）
- tool_call 聚合行写入
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.observability.observation import (
    ObservationCallback,
    SqliteObservationSink,
)


@pytest.fixture
def tmp_sink(tmp_path: Path) -> SqliteObservationSink:
    """临时 SQLite sink（独立 db 文件）。"""
    return SqliteObservationSink(db_path=tmp_path / "test_callback.db")


@pytest.fixture
def callback(tmp_sink: SqliteObservationSink) -> ObservationCallback:
    """绑定到 tmp_sink 的 ObservationCallback，run_id='test_run_001'。"""
    return ObservationCallback(sink=tmp_sink, run_id="test_run_001")


def _count_events(sink: SqliteObservationSink, run_id: str) -> int:
    """查 observation_event 中指定 run_id 的行数。"""
    conn = sqlite3.connect(str(sink._db_path))
    cur = conn.execute(
        "SELECT COUNT(*) FROM observation_event WHERE run_id=?", (run_id,)
    )
    count = cur.fetchone()[0]
    conn.close()
    return count


def _get_events(sink: SqliteObservationSink, run_id: str) -> list[dict]:
    """查 observation_event 中指定 run_id 的所有行。"""
    conn = sqlite3.connect(str(sink._db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT * FROM observation_event WHERE run_id=? ORDER BY seq ASC",
        (run_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ============================================================
# FR-2.2: 钩子触发条件
# ============================================================


class TestCallbackTriggers:
    """Callback 钩子触发条件测试。"""

    def test_on_llm_start_writes_event(
        self, callback: ObservationCallback, tmp_sink: SqliteObservationSink
    ) -> None:
        """on_llm_start 触发后 observation_event 写入 1 行 llm_start。"""
        callback.on_llm_start(
            serialized={"name": "ChatOpenAI"},
            prompts=["Hello, world!"],
            run_id="llm_run_1",
        )
        events = _get_events(tmp_sink, "test_run_001")
        assert len(events) == 1
        assert events[0]["event_type"] == "llm_start"
        assert "prompts" in events[0]["payload_json"]

    def test_on_llm_end_writes_event(
        self, callback: ObservationCallback, tmp_sink: SqliteObservationSink
    ) -> None:
        """on_llm_end 触发后 observation_event 写入 1 行 llm_end。"""
        response = MagicMock()
        response.llm_output = {"token_usage": {"total_tokens": 42}}
        callback.on_llm_end(response, run_id="llm_run_1")
        events = _get_events(tmp_sink, "test_run_001")
        assert len(events) == 1
        assert events[0]["event_type"] == "llm_end"

    def test_on_tool_start_writes_event_and_tool_call_row(
        self, callback: ObservationCallback, tmp_sink: SqliteObservationSink
    ) -> None:
        """on_tool_start 触发后 observation_event 写入 tool_start + observation_tool_call 写入聚合行。"""
        callback.on_tool_start(
            serialized={"name": "read_file"},
            input_str="/tmp/test.py",
            run_id="tool_run_1",
        )
        events = _get_events(tmp_sink, "test_run_001")
        assert any(e["event_type"] == "tool_start" for e in events)
        # 验证 tool_call 聚合行
        conn = sqlite3.connect(str(tmp_sink._db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM observation_tool_call WHERE run_id=?", ("test_run_001",)
        )
        row = cur.fetchone()
        conn.close()
        assert row is not None
        assert row["tool_name"] == "read_file"

    def test_on_tool_end_updates_tool_call_row(
        self, callback: ObservationCallback, tmp_sink: SqliteObservationSink
    ) -> None:
        """on_tool_end 触发后 observation_tool_call 聚合行被更新（result_preview + ended_at）。"""
        callback.on_tool_start(
            serialized={"name": "read_file"},
            input_str="/tmp/test.py",
            run_id="tool_run_1",
        )
        callback.on_tool_end("file content here", run_id="tool_run_1")
        conn = sqlite3.connect(str(tmp_sink._db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM observation_tool_call WHERE tool_call_id=?",
            ("tool_run_1",),
        )
        row = cur.fetchone()
        conn.close()
        assert row is not None
        assert row["result_preview"] == "file content here"
        assert row["ended_at"] is not None

    def test_on_chain_start_writes_event(
        self, callback: ObservationCallback, tmp_sink: SqliteObservationSink
    ) -> None:
        """on_chain_start 触发后 observation_event 写入 1 行 chain_start。"""
        callback.on_chain_start(
            serialized={"name": "LangGraph:agent"},
            inputs={"messages": []},
            run_id="chain_run_1",
        )
        events = _get_events(tmp_sink, "test_run_001")
        assert len(events) == 1
        assert events[0]["event_type"] == "chain_start"

    def test_on_chain_end_writes_event(
        self, callback: ObservationCallback, tmp_sink: SqliteObservationSink
    ) -> None:
        """on_chain_end 触发后 observation_event 写入 1 行 chain_end。"""
        callback.on_chain_end(
            outputs={"messages": []},
            run_id="chain_run_1",
        )
        events = _get_events(tmp_sink, "test_run_001")
        assert len(events) == 1
        assert events[0]["event_type"] == "chain_end"


# ============================================================
# FR-1.5 / NFR-9: args redact 行为
# ============================================================


class TestCallbackRedact:
    """Callback args redact 行为测试。"""

    def test_chain_inputs_with_api_key_redacted(
        self, callback: ObservationCallback, tmp_sink: SqliteObservationSink
    ) -> None:
        """on_chain_start 的 inputs 含 api_key 字段时，payload_json 中应被 redact。"""
        callback.on_chain_start(
            serialized={"name": "test"},
            inputs={"api_key": "sk-secret123", "normal_field": "ok"},
            run_id="chain_1",
        )
        events = _get_events(tmp_sink, "test_run_001")
        import json

        payload = json.loads(events[0]["payload_json"])
        # redact 把 api_key 值替换为 <redacted>
        assert payload["inputs"]["api_key"] == "<redacted>"
        assert payload["inputs"]["normal_field"] == "ok"


# ============================================================
# FR-2.3: 异常隔离
# ============================================================


class TestCallbackExceptionIsolation:
    """Callback 异常隔离测试 — callback 抛错不影响调用方。"""

    def test_on_llm_start_sink_failure_does_not_raise(
        self, callback: ObservationCallback, tmp_sink: SqliteObservationSink
    ) -> None:
        """sink._append_event_sync 抛异常时，on_llm_start 不应抛出。"""
        # 替换 sink 的写方法为抛异常
        original = tmp_sink._append_event_sync
        tmp_sink._append_event_sync = MagicMock(side_effect=RuntimeError("DB locked"))
        try:
            # 不应抛出异常
            callback.on_llm_start(
                serialized={"name": "test"},
                prompts=["hello"],
                run_id="r1",
            )
        finally:
            tmp_sink._append_event_sync = original

    def test_on_tool_start_sink_failure_does_not_raise(
        self, callback: ObservationCallback, tmp_sink: SqliteObservationSink
    ) -> None:
        """sink.append_tool_call_sync 抛异常时，on_tool_start 不应抛出。"""
        tmp_sink.append_tool_call_sync = MagicMock(side_effect=RuntimeError("DB error"))
        callback.on_tool_start(
            serialized={"name": "test_tool"},
            input_str="test",
            run_id="r2",
        )

    def test_on_tool_end_with_unknown_tool_call_id_does_not_raise(
        self, callback: ObservationCallback
    ) -> None:
        """on_tool_end 收到未知 tool_call_id 时不应抛出（fallback 到 _last_tool_call_id）。"""
        # 没有 on_tool_start 前置，直接调 on_tool_end
        callback.on_tool_end("result", run_id=None)


# ============================================================
# FR-5.2: seq 自增
# ============================================================


class TestCallbackSeqIncrement:
    """seq 同 run 内自增测试。"""

    def test_seq_increments_across_callbacks(
        self, callback: ObservationCallback, tmp_sink: SqliteObservationSink
    ) -> None:
        """多个 callback 调用后，seq 应递增且唯一。"""
        callback.on_llm_start(serialized={"name": "m1"}, prompts=["a"], run_id="r1")
        callback.on_chain_start(serialized={"name": "c1"}, inputs={}, run_id="r2")
        callback.on_llm_end(MagicMock(), run_id="r1")
        events = _get_events(tmp_sink, "test_run_001")
        seqs = [e["seq"] for e in events]
        # seq 应为 1, 2, 3（递增）
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)  # 唯一
        assert seqs[0] == 1
        assert seqs[-1] == 3


# ============================================================
# tool_call 聚合行
# ============================================================


class TestCallbackToolCallAggregation:
    """tool_call 聚合行写入测试。"""

    def test_tool_call_lifecycle_recorded(
        self, callback: ObservationCallback, tmp_sink: SqliteObservationSink
    ) -> None:
        """on_tool_start + on_tool_end 完整生命周期被记录到 observation_tool_call。"""
        callback.on_tool_start(
            serialized={"name": "write_file"},
            input_str="path=/tmp/a.py, content=hello",
            run_id="tc_001",
        )
        time.sleep(0.01)  # 确保 duration > 0
        callback.on_tool_end("written 5 bytes", run_id="tc_001")
        conn = sqlite3.connect(str(tmp_sink._db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM observation_tool_call WHERE tool_call_id=?", ("tc_001",)
        )
        row = cur.fetchone()
        conn.close()
        assert row is not None
        assert row["tool_name"] == "write_file"
        assert row["result_preview"] == "written 5 bytes"
        assert row["duration_ms"] is not None
        assert row["duration_ms"] >= 0
        assert row["started_at"] is not None
        assert row["ended_at"] is not None

    def test_tool_error_recorded(
        self, callback: ObservationCallback, tmp_sink: SqliteObservationSink
    ) -> None:
        """on_tool_error 触发后 error_message 被写入聚合行。"""
        callback.on_tool_start(
            serialized={"name": "dangerous_tool"},
            input_str="rm -rf /",
            run_id="tc_002",
        )
        callback.on_tool_error(ValueError("permission denied"), run_id="tc_002")
        conn = sqlite3.connect(str(tmp_sink._db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM observation_tool_call WHERE tool_call_id=?", ("tc_002",)
        )
        row = cur.fetchone()
        conn.close()
        assert row is not None
        assert row["error_message"] == "permission denied"
        assert row["ended_at"] is not None
