"""AgentTeam v2 replan/routing/warnings 测试（T22/T23/T37/T39）。

覆盖：
- T39: ``_route_after_aggregate`` 条件边（质量门通过 → END / 失败 → replan）
- T22/T23: ``replan_node`` 实现（replan_count 限制 / Planner.replan 调用 / 无新任务路由）
- T37: ``state.warnings`` channel（aggregate_node 发射 warning SSE）
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.team.nodes import (
    _route_after_aggregate,
    _route_after_replan,
    aggregate_node,
    replan_node,
)
from app.team.state import Finding, TeamPlan, TeamState, TeamTask


def _task(
    task_id: str = "t1",
    agent: str = "code",
    description: str = "do something",
) -> TeamTask:
    return TeamTask(id=task_id, agent=agent, description=description)


def _mock_settings(**overrides: Any) -> Any:
    defaults = {
        "team_max_tasks": 5,
        "team_max_concurrency": 5,
        "team_result_max_chars": 2000,
        "team_subtask_timeout": 300,
        "team_max_replan_attempts": 1,
        "team_max_retries": 2,
        "llm_temperature_orchestrator": 0.3,
        "llm_temperature_aggregator": 0.3,
        "team_subagents": {},
        "subagents": {},
        "custom_subagents": {},
        "tools_enabled": {},
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ============================================================
# T39: _route_after_aggregate
# ============================================================


class TestRouteAfterAggregate:
    """``_route_after_aggregate`` 条件边路由测试（D16）。"""

    def test_quality_gate_passed_routes_to_end(self) -> None:
        """质量门通过 → END。"""
        state: TeamState = {"quality_gate_passed": True}  # type: ignore[typeddict-item]
        assert _route_after_aggregate(state) == "__end__"

    def test_quality_gate_failed_routes_to_replan(self) -> None:
        """质量门失败 → replan。"""
        state: TeamState = {"quality_gate_passed": False}  # type: ignore[typeddict-item]
        assert _route_after_aggregate(state) == "replan"

    def test_quality_gate_default_passed(self) -> None:
        """未设置 quality_gate_passed 时默认通过（True）。"""
        state: TeamState = {}  # type: ignore[typeddict-item]
        assert _route_after_aggregate(state) == "__end__"


# ============================================================
# T22/T23: replan_node
# ============================================================


class TestReplanNode:
    """``replan_node`` 实现测试。"""

    @pytest.mark.asyncio
    async def test_replan_limit_reached_routes_to_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """replan_count >= max_replan_attempts 时返回空 pending_waves（路由到 END）。"""
        monkeypatch.setattr("app.team.nodes.get_settings", lambda: _mock_settings(team_max_replan_attempts=1))
        monkeypatch.setattr("app.team.nodes.get_stream_writer", lambda: (lambda _e: None))

        state: TeamState = {
            "message": "test",
            "thread_id": "t1",
            "plan": [_task()],
            "findings": {},
            "errors": [],
            "replan_count": 1,  # 已达上限
        }  # type: ignore[typeddict-item]

        result = await replan_node(state)
        assert result["pending_waves"] == []

    @pytest.mark.asyncio
    async def test_replan_no_new_tasks_routes_to_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Planner.replan 返回空 tasks 时路由到 END。"""
        monkeypatch.setattr("app.team.nodes.get_settings", lambda: _mock_settings(team_max_replan_attempts=2))
        writer_mock = MagicMock()
        monkeypatch.setattr("app.team.nodes.get_stream_writer", lambda: writer_mock)

        mock_planner = MagicMock()
        mock_planner.replan = AsyncMock(return_value=TeamPlan(tasks=[]))
        monkeypatch.setattr("app.team.nodes.Planner", lambda _cm: mock_planner)

        state: TeamState = {
            "message": "test",
            "thread_id": "t1",
            "plan": [_task()],
            "findings": {"code:t1:0": Finding(agent="code", task_id="t1", wave_index=0, content="ok")},
            "errors": [],
            "replan_count": 0,
        }  # type: ignore[typeddict-item]

        result = await replan_node(state)
        assert result["pending_waves"] == []

    @pytest.mark.asyncio
    async def test_replan_with_new_tasks_appends_to_plan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Planner.replan 返回新任务时追加到 plan + 返回 waves。"""
        monkeypatch.setattr("app.team.nodes.get_settings", lambda: _mock_settings(team_max_replan_attempts=2))
        writer_mock = MagicMock()
        monkeypatch.setattr("app.team.nodes.get_stream_writer", lambda: writer_mock)

        new_task = _task(task_id="t2", agent="code", description="new task")
        mock_planner = MagicMock()
        mock_planner.replan = AsyncMock(return_value=TeamPlan(tasks=[new_task]))
        monkeypatch.setattr("app.team.nodes.Planner", lambda _cm: mock_planner)

        old_task = _task(task_id="t1")
        state: TeamState = {
            "message": "test",
            "thread_id": "t1",
            "plan": [old_task],
            "findings": {},
            "errors": [],
            "replan_count": 0,
        }  # type: ignore[typeddict-item]

        result = await replan_node(state)
        # 追加新任务到 plan
        assert len(result["plan"]) == 2
        assert result["plan"][1].id == "t2"
        # 返回非空 waves
        assert len(result["pending_waves"]) >= 1
        # replan_count +1
        assert result["replan_count"] == 1
        # 发射 replan SSE
        sse_calls = writer_mock.call_args_list
        assert any(
            call.args[0].get("event") == "replan" for call in sse_calls
        )


# ============================================================
# T37: warnings channel
# ============================================================


class TestWarningsChannel:
    """``state.warnings`` channel 基础设施测试（D14）。"""

    @pytest.mark.asyncio
    async def test_aggregate_node_emits_warning_sse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """aggregate_node 发射 state.warnings 累积的 warning SSE。"""
        monkeypatch.setattr("app.team.nodes.get_settings", lambda: _mock_settings())
        emitted: list[dict] = []
        monkeypatch.setattr("app.team.nodes.get_stream_writer", lambda: (lambda e: emitted.append(e)))

        # mock _run_aggregator 返回空流
        async def _empty_agg(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            yield {"event": "token", "data": "summary"}

        monkeypatch.setattr("app.team.nodes._run_aggregator", _empty_agg)

        # mock get_abort_event 返回未触发的 event
        mock_abort = MagicMock()
        mock_abort.is_set.return_value = False
        monkeypatch.setattr("app.team.nodes.get_abort_event", AsyncMock(return_value=mock_abort))

        findings = {"code:t1:0": Finding(agent="code", task_id="t1", wave_index=0, content="result")}
        state: TeamState = {
            "message": "test",
            "thread_id": "t1",
            "findings": findings,
            "errors": [],
            "warnings": ["未知 agent 'foo' fallback 到 code runner", "replan triggered"],
        }  # type: ignore[typeddict-item]

        await aggregate_node(state)

        # 验证 warning SSE 发射（data 为 JSON 字符串）
        import json

        warning_events = [e for e in emitted if e.get("event") == "warning"]
        assert len(warning_events) == 2
        msg0 = json.loads(warning_events[0]["data"])["message"]
        msg1 = json.loads(warning_events[1]["data"])["message"]
        assert "fallback" in msg0
        assert "replan" in msg1


# ============================================================
# _route_after_replan
# ============================================================


class TestRouteAfterReplan:
    """``_route_after_replan`` 路由测试。"""

    def test_has_waves_routes_to_dispatch(self) -> None:
        """有 pending_waves → dispatch。"""
        state: TeamState = {"pending_waves": [[_task()]]}  # type: ignore[typeddict-item]
        assert _route_after_replan(state) == "dispatch"

    def test_no_waves_routes_to_end(self) -> None:
        """无 pending_waves → END。"""
        state: TeamState = {"pending_waves": []}  # type: ignore[typeddict-item]
        assert _route_after_replan(state) == "__end__"
