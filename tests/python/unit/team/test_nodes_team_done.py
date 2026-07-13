"""aggregate_node + replan_node team_done 发射测试。"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from app.team.nodes import aggregate_node, replan_node
from app.team.state import Finding, TeamState


@pytest.mark.asyncio
async def test_aggregate_node_empty_findings_no_replan_no_team_done():
    """findings 为空时：发射 team_done(error) 一次，不路由到 replan。"""
    emitted_events = []
    writer = lambda ev: emitted_events.append(ev)

    state: TeamState = {
        "message": "test", "thread_id": "t1",
        "findings": {}, "errors": [], "warnings": [],
        "replan_count": 0,
    }
    with patch("app.team.nodes.get_stream_writer", return_value=writer), \
         patch("app.team.nodes.get_abort_event", new=AsyncMock(return_value=asyncio.Event())):
        result = await aggregate_node(state)

    team_done_events = [e for e in emitted_events if e.get("event") == "team_done"]
    assert len(team_done_events) == 1, f"应只发射 1 次 team_done，实际 {len(team_done_events)}"
    # 不应路由到 replan
    assert result.get("quality_gate_passed") is False
    # BE-C: 空结果应直接终止，不应进入 replan
    assert result.get("should_terminate") is True


@pytest.mark.asyncio
async def test_replan_limit_no_duplicate_team_done():
    """replan 达上限时不应再发 team_done（aggregate_node 已发过）。"""
    emitted_events = []
    writer = lambda ev: emitted_events.append(ev)

    state: TeamState = {
        "message": "test", "thread_id": "t1",
        "findings": {"code:t1:0": Finding(agent="code", task_id="t1", wave_index=0, content="x", success=False)},
        "errors": ["error"], "warnings": [],
        "replan_count": 5,  # 已达上限
        "plan": [],
    }
    with patch("app.team.nodes.get_stream_writer", return_value=writer), \
         patch("app.team.nodes.get_settings") as mock_settings:
        mock_settings.return_value.team_max_replan_attempts = 3
        result = await replan_node(state)

    team_done_events = [e for e in emitted_events if e.get("event") == "team_done"]
    # replan_node 不应发射 team_done（由 aggregate_node 统一发射）
    assert len(team_done_events) == 0, f"replan_node 不应发 team_done，实际发了 {len(team_done_events)}"
