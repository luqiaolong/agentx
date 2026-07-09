"""LangSmith 双写 + 降级测试（T2.5）。

覆盖 spec FR-3.1 ~ FR-3.6：
- 双写路径（凭证存在 → 本地 start_run/end_run + remote trace_span 各调用）
- 降级路径（凭证缺失 → 仅本地 + 1 条 warning）
- 凭证缺失时 langsmith_tracing=False → 仅本地，不记 warning
- remote 异常 → 本地数据保留
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from app.observability.langsmith import dual_trace
from app.observability.observation import SqliteObservationSink, reset_observation_sink


@pytest.fixture
def tmp_sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SqliteObservationSink:
    """临时 sink + patch get_observation_sink 返回它。"""
    sink = SqliteObservationSink(db_path=tmp_path / "dual_test.db")
    # patch 模块级 get_observation_sink，使 dual_trace 用临时 sink
    import app.observability.langsmith as mod

    monkeypatch.setattr(mod, "get_observation_sink", lambda: sink)
    monkeypatch.setattr(
        "app.observability.observation.get_observation_sink", lambda: sink
    )
    yield sink
    reset_observation_sink()


@pytest.fixture
def no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟无 LangSmith 凭证。"""
    monkeypatch.setenv("AGENTX_LANGSMITH_API_KEY", "")
    monkeypatch.setenv("AGENTX_LANGSMITH_TRACING", "false")
    # 强制重新加载 settings
    from app.config import get_settings

    get_settings.cache_clear()


@pytest.fixture
def with_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟有 LangSmith 凭证。"""
    monkeypatch.setenv("AGENTX_LANGSMITH_API_KEY", "ls-fake-key-for-test")
    monkeypatch.setenv("AGENTX_LANGSMITH_TRACING", "true")
    monkeypatch.setenv("AGENTX_LANGSMITH_PROJECT", "agentx-test")
    from app.config import get_settings

    get_settings.cache_clear()


# ============================================================
# FR-3.1 / FR-3.2: 本地必写（无凭证时仅本地）
# ============================================================


def test_dual_trace_local_only_writes_run(
    tmp_sink: SqliteObservationSink,
    no_credentials: None,
) -> None:
    """无凭证时 dual_trace 仍写本地 observation_run（start + end）。"""
    with dual_trace(
        thread_id="t-dual-1",
        agent_mode="chat",
        user_message="hello",
        run_id="r-local-0000000001",
    ) as ctx:
        assert ctx.run_id == "r-local-0000000001"
        assert ctx.trace_id == ctx.run_id  # FR-1.4 run_id = trace_id
        assert ctx.remote_enabled is False

    # 验证本地 sink 有 start_run + end_run
    run = tmp_sink.get_run_sync("r-local-0000000001")
    assert run is not None
    assert run["thread_id"] == "t-dual-1"
    assert run["agent_mode"] == "chat"
    assert run["user_message"] == "hello"
    assert run["ended_at"] is not None  # end_run 已写
    assert run["duration_ms"] is not None
    assert run["duration_ms"] >= 0


def test_dual_trace_auto_gen_run_id(
    tmp_sink: SqliteObservationSink,
    no_credentials: None,
) -> None:
    """未传 run_id 时自动生成 16 字符 hex trace_id。"""
    with dual_trace(
        thread_id="t-dual-2",
        agent_mode="deep_task",
        user_message="test",
    ) as ctx:
        assert len(ctx.run_id) == 16
        assert all(c in "0123456789abcdef" for c in ctx.run_id)

    run = tmp_sink.get_run_sync(ctx.run_id)
    assert run is not None


# ============================================================
# FR-3.3: 凭证存在时双写（本地 + remote）
# ============================================================


def test_dual_trace_remote_enabled(
    tmp_sink: SqliteObservationSink,
    with_credentials: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """凭证存在时 remote_enabled=True，本地 + remote 各写一次。"""
    remote_calls: list[str] = []

    # mock trace_span 记录 remote 调用
    import app.observability.langsmith as mod

    @contextmanager
    def mock_trace_span(name, **kwargs):
        remote_calls.append(name)
        yield {"name": name, "metadata": kwargs}

    monkeypatch.setattr(mod, "trace_span", mock_trace_span)

    with dual_trace(
        thread_id="t-dual-3",
        agent_mode="chat",
        user_message="remote test",
        run_id="r-remote-0000000003",
    ) as ctx:
        assert ctx.remote_enabled is True

    # 本地已写
    run = tmp_sink.get_run_sync("r-remote-0000000003")
    assert run is not None
    assert run["ended_at"] is not None

    # remote trace_span 被调用
    assert len(remote_calls) == 1
    assert "agentx:chat" in remote_calls[0]


# ============================================================
# FR-3.6: 降级 — tracing on 但凭证缺失 → warning
# ============================================================


def test_dual_trace_degrade_warning(
    tmp_sink: SqliteObservationSink,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """langsmith_tracing=True 但 API_KEY 缺失 → remote_enabled=False + warning。"""
    monkeypatch.setenv("AGENTX_LANGSMITH_API_KEY", "")
    monkeypatch.setenv("AGENTX_LANGSMITH_TRACING", "true")
    from app.config import get_settings

    get_settings.cache_clear()

    import app.observability.langsmith as mod

    warning_calls: list[str] = []

    def mock_warning(msg, *args, **kwargs):
        warning_calls.append(str(msg))

    monkeypatch.setattr(mod.logger, "warning", mock_warning)

    with dual_trace(
        thread_id="t-dual-4",
        agent_mode="chat",
        user_message="degrade test",
        run_id="r-degrade-00000004",
    ) as ctx:
        assert ctx.remote_enabled is False

    # 本地数据仍写入
    run = tmp_sink.get_run_sync("r-degrade-00000004")
    assert run is not None

    # 有降级 warning
    assert any("degrade" in w.lower() or "missing" in w.lower() for w in warning_calls)


# ============================================================
# FR-3.4: remote 异常 → 本地数据保留
# ============================================================


def test_dual_trace_remote_failure_preserves_local(
    tmp_sink: SqliteObservationSink,
    with_credentials: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """remote trace_span 抛异常时，本地 observation_run 数据不丢。"""
    import app.observability.langsmith as mod

    @contextmanager
    def mock_trace_span_raising(name, **kwargs):
        raise ConnectionError("LangSmith API unreachable")
        yield  # unreachable

    monkeypatch.setattr(mod, "trace_span", mock_trace_span_raising)

    # remote 异常不应导致 dual_trace 抛错（降级处理）
    with dual_trace(
        thread_id="t-dual-5",
        agent_mode="chat",
        user_message="failure test",
        run_id="r-fail-000000000005",
    ) as ctx:
        ctx.add_metadata("result_text", "completed despite remote failure")

    # 本地数据保留
    run = tmp_sink.get_run_sync("r-fail-000000000005")
    assert run is not None
    assert run["ended_at"] is not None
    assert run["result_text"] == "completed despite remote failure"


# ============================================================
# FR-3.5: metadata 传递（add_metadata → end_run 写入）
# ============================================================


def test_dual_trace_metadata_to_end_run(
    tmp_sink: SqliteObservationSink,
    no_credentials: None,
) -> None:
    """运行中 add_metadata 的 result_text / error_type 在 end_run 时写入。"""
    with dual_trace(
        thread_id="t-dual-6",
        agent_mode="single_tool",
        user_message="metadata test",
        run_id="r-meta-000000000006",
    ) as ctx:
        ctx.add_metadata("result_text", "tool output here")
        ctx.add_metadata("result_token_count", 42)
        ctx.add_metadata("error_type", None)

    run = tmp_sink.get_run_sync("r-meta-000000000006")
    assert run is not None
    assert run["result_text"] == "tool output here"
    assert run["result_token_count"] == 42


# ============================================================
# FR-1.4: run_id = trace_id 单一锚点
# ============================================================


def test_dual_trace_run_id_equals_trace_id(
    tmp_sink: SqliteObservationSink,
    no_credentials: None,
) -> None:
    """run_id 和 trace_id 必须相同（FR-1.4 单一锚点）。"""
    with dual_trace(
        thread_id="t-dual-7",
        agent_mode="chat",
        user_message="anchor test",
        run_id="r-anchor-000000007",
    ) as ctx:
        assert ctx.run_id == ctx.trace_id
        assert ctx.run_id == "r-anchor-000000007"

    run = tmp_sink.get_run_sync("r-anchor-000000007")
    assert run is not None
    assert run["trace_id"] == run["run_id"]  # DB 中也一致
