"""I3.3: Team 事件 task_id 关联测试（D5 / REQ-TEAM-CORRELATION-1）。

覆盖：
- ``delegation`` SSE 事件包含 ``task_id``（稳定关联键，替代角色名）
- ``team_done.agents[]`` 包含 ``task_id``
- 两个相同角色不同 task_id 不互相覆盖
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.team.nodes import _emit_delegation, aggregate_node
from app.team.state import Finding, TeamState


# ============================================================
# 公共 helpers
# ============================================================


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
# I3.3: delegation 事件包含 task_id
# ============================================================


class TestDelegationHasTaskId:
    """``delegation`` SSE 事件必须包含 ``task_id`` 关联键（D5）。"""

    def test_delegation_has_task_id(self) -> None:
        """delegation 事件 payload 必须含 task_id 字段。"""
        emitted: list[dict] = []
        writer = lambda e: emitted.append(e)

        _emit_delegation(writer, agent="code", purpose="read file", task_id="t1")

        assert len(emitted) == 1
        ev = emitted[0]
        assert ev["event"] == "delegation"
        payload = json.loads(ev["data"])
        assert payload["task_id"] == "t1"
        assert payload["target"] == "code"

    def test_delegation_has_run_id_when_provided(self) -> None:
        """run_id 传入时出现在 delegation payload 中。"""
        emitted: list[dict] = []
        writer = lambda e: emitted.append(e)

        _emit_delegation(
            writer, agent="deep", purpose="edit", task_id="t2", run_id="run-abc"
        )

        payload = json.loads(emitted[0]["data"])
        assert payload["task_id"] == "t2"
        assert payload["run_id"] == "run-abc"

    def test_delegation_without_run_id(self) -> None:
        """未传 run_id 时 payload 不含 run_id 字段（不传 null）。"""
        emitted: list[dict] = []
        writer = lambda e: emitted.append(e)

        _emit_delegation(writer, agent="code", purpose="read", task_id="t1")

        payload = json.loads(emitted[0]["data"])
        assert "task_id" in payload
        assert "run_id" not in payload


# ============================================================
# I3.3: team_done agents 列表包含 task_id
# ============================================================


class TestTeamDoneAgentsHaveTaskId:
    """``team_done.agents[]`` 必须包含 ``task_id``（D5）。"""

    @pytest.mark.asyncio
    async def test_team_done_agents_have_task_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """每个 agent 项含 task_id。"""
        monkeypatch.setattr("app.team.nodes.get_settings", lambda: _mock_settings())
        emitted: list[dict] = []
        monkeypatch.setattr("app.team.nodes.get_stream_writer", lambda: (lambda e: emitted.append(e)))

        async def _empty_agg(*args: Any, **kwargs: Any) -> Any:
            return
            yield  # pragma: no cover

        monkeypatch.setattr("app.team.nodes._run_aggregator", _empty_agg)
        mock_abort = MagicMock()
        mock_abort.is_set.return_value = False
        monkeypatch.setattr("app.team.nodes.get_abort_event", AsyncMock(return_value=mock_abort))

        findings = {
            "frontend_dev:f1:0": Finding(
                agent="frontend_dev", task_id="f1", wave_index=0, content="result A", success=True
            ),
            "frontend_dev:f2:0": Finding(
                agent="frontend_dev", task_id="f2", wave_index=0, content="result B", success=True
            ),
        }
        state: TeamState = {
            "message": "test",
            "thread_id": "t1",
            "findings": findings,
            "errors": [],
            "warnings": [],
        }  # type: ignore[typeddict-item]

        await aggregate_node(state)

        team_done = [e for e in emitted if e.get("event") == "team_done"]
        payload = json.loads(team_done[0]["data"])
        agents = payload["agents"]
        task_ids = [a["task_id"] for a in agents]
        assert "f1" in task_ids
        assert "f2" in task_ids


# ============================================================
# I3.3: 重复角色不同 task_id 不互相覆盖
# ============================================================


class TestDuplicateRoleNoOverwrite:
    """两个相同角色不同 task_id 不互相覆盖（REQ-TEAM-CORRELATION-1）。"""

    def test_two_same_agents_different_task_ids(self) -> None:
        """两个 frontend_dev 任务（f1, f2）发射的 delegation 事件 task_id 不同。"""
        emitted: list[dict] = []
        writer = lambda e: emitted.append(e)

        _emit_delegation(writer, agent="frontend_dev", purpose="task A", task_id="f1")
        _emit_delegation(writer, agent="frontend_dev", purpose="task B", task_id="f2")

        assert len(emitted) == 2
        p1 = json.loads(emitted[0]["data"])
        p2 = json.loads(emitted[1]["data"])
        assert p1["task_id"] == "f1"
        assert p2["task_id"] == "f2"
        # 相同角色但不同 task_id
        assert p1["target"] == p2["target"] == "frontend_dev"
        assert p1["task_id"] != p2["task_id"]

    @pytest.mark.asyncio
    async def test_team_done_two_same_agents_distinct(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """team_done agents 列表中两个相同角色的任务以 task_id 区分。"""
        monkeypatch.setattr("app.team.nodes.get_settings", lambda: _mock_settings())
        emitted: list[dict] = []
        monkeypatch.setattr("app.team.nodes.get_stream_writer", lambda: (lambda e: emitted.append(e)))

        async def _empty_agg(*args: Any, **kwargs: Any) -> Any:
            return
            yield  # pragma: no cover

        monkeypatch.setattr("app.team.nodes._run_aggregator", _empty_agg)
        mock_abort = MagicMock()
        mock_abort.is_set.return_value = False
        monkeypatch.setattr("app.team.nodes.get_abort_event", AsyncMock(return_value=mock_abort))

        findings = {
            "frontend_dev:f1:0": Finding(
                agent="frontend_dev", task_id="f1", wave_index=0, content="A", success=True
            ),
            "frontend_dev:f2:0": Finding(
                agent="frontend_dev", task_id="f2", wave_index=0, content="B", success=True
            ),
        }
        state: TeamState = {
            "message": "test",
            "thread_id": "t1",
            "findings": findings,
            "errors": [],
            "warnings": [],
        }  # type: ignore[typeddict-item]

        await aggregate_node(state)

        team_done = [e for e in emitted if e.get("event") == "team_done"]
        payload = json.loads(team_done[0]["data"])
        agents = payload["agents"]
        assert len(agents) == 2
        # 两个 agent 项的 task_id 不同
        assert agents[0]["task_id"] != agents[1]["task_id"]
        # 两个 agent 项的 agent 名相同
        assert agents[0]["agent"] == agents[1]["agent"] == "frontend_dev"
