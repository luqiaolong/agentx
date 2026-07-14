"""I3.1 + I3.2: TeamOutcome 类型与成功判定测试。

覆盖：
- ``TeamOutcome`` 枚举（success / partial / error / aborted）
- ``_compute_team_outcome`` 基于 ``Finding.success`` 判定 outcome
- ``aggregate_node`` 在 team_done payload 中输出权威 ``outcome`` 字段
- agents 列表补齐 ``task_id`` / ``success`` / ``retries`` 字段
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.team.aggregator import _compute_team_outcome
from app.team.nodes import aggregate_node
from app.team.state import Finding, TeamOutcome, TeamState


# ============================================================
# 公共 helpers
# ============================================================


def _finding(
    agent: str = "code",
    task_id: str = "t1",
    content: str = "result",
    success: bool = True,
    retries: int = 0,
) -> Finding:
    return Finding(
        agent=agent,
        task_id=task_id,
        wave_index=0,
        content=content,
        success=success,
        retries=retries,
    )


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
# I3.1: TeamOutcome 枚举
# ============================================================


class TestTeamOutcomeEnum:
    """``TeamOutcome`` 枚举值验证。"""

    def test_outcome_values(self) -> None:
        assert TeamOutcome.SUCCESS.value == "success"
        assert TeamOutcome.PARTIAL.value == "partial"
        assert TeamOutcome.ERROR.value == "error"
        assert TeamOutcome.ABORTED.value == "aborted"

    def test_outcome_is_str_enum(self) -> None:
        """TeamOutcome 应为 str Enum（便于 JSON 序列化）。"""
        assert isinstance(TeamOutcome.SUCCESS, str)
        assert TeamOutcome.SUCCESS == "success"


# ============================================================
# I3.2: _compute_team_outcome 成功判定
# ============================================================


class TestComputeTeamOutcome:
    """``_compute_team_outcome`` 基于 ``Finding.success`` 判定 outcome。"""

    def test_all_success(self) -> None:
        """全部 Finding.success=True → SUCCESS。"""
        findings = {
            "code:t1:0": _finding("code", "t1", success=True),
            "rag:t2:0": _finding("rag", "t2", success=True),
        }
        assert _compute_team_outcome(findings) == TeamOutcome.SUCCESS

    def test_partial_success(self) -> None:
        """部分成功部分失败 → PARTIAL。"""
        findings = {
            "code:t1:0": _finding("code", "t1", success=True, content="ok"),
            "deep:t2:0": _finding("deep", "t2", success=False, content=""),
        }
        assert _compute_team_outcome(findings) == TeamOutcome.PARTIAL

    def test_all_failed_not_success(self) -> None:
        """全部 Finding.success=False → ERROR（不得因存在 finding 文本标为成功）。"""
        findings = {
            "code:t1:0": _finding("code", "t1", success=False, content="失败文本"),
            "deep:t2:0": _finding("deep", "t2", success=False, content="另一失败"),
        }
        assert _compute_team_outcome(findings) == TeamOutcome.ERROR

    def test_no_findings_is_error(self) -> None:
        """无 findings → ERROR。"""
        assert _compute_team_outcome({}) == TeamOutcome.ERROR

    def test_aborted(self) -> None:
        """abort → ABORTED。"""
        findings = {
            "code:t1:0": _finding("code", "t1", success=True),
        }
        assert _compute_team_outcome(findings, aborted=True) == TeamOutcome.ABORTED

    def test_aborted_overrides_all_failed(self) -> None:
        """即使全部失败，abort 仍优先返回 ABORTED。"""
        findings = {
            "code:t1:0": _finding("code", "t1", success=False),
        }
        assert _compute_team_outcome(findings, aborted=True) == TeamOutcome.ABORTED

    def test_list_findings_flattened(self) -> None:
        """findings 值为 list[Finding]（BE-N 同 key 合并）时正确展平统计。"""
        findings = {
            "code:t1:0": [
                _finding("code", "t1", success=True),
                _finding("code", "t1", success=False),
            ],
        }
        # 1 成功 + 1 失败 → PARTIAL
        assert _compute_team_outcome(findings) == TeamOutcome.PARTIAL


# ============================================================
# I3.2: aggregate_node 输出 outcome 字段
# ============================================================


class TestAggregateNodeOutcome:
    """``aggregate_node`` 在 team_done payload 中输出权威 ``outcome`` 字段。"""

    @pytest.mark.asyncio
    async def test_team_done_has_outcome_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """team_done payload 必须包含 outcome 字段。"""
        monkeypatch.setattr("app.team.nodes.get_settings", lambda: _mock_settings())
        emitted: list[dict] = []
        monkeypatch.setattr("app.team.nodes.get_stream_writer", lambda: (lambda e: emitted.append(e)))

        async def _empty_agg(*args: Any, **kwargs: Any) -> Any:
            yield {"event": "token", "data": "summary"}

        monkeypatch.setattr("app.team.nodes._run_aggregator", _empty_agg)
        mock_abort = MagicMock()
        mock_abort.is_set.return_value = False
        monkeypatch.setattr("app.team.nodes.get_abort_event", AsyncMock(return_value=mock_abort))

        findings = {
            "code:t1:0": _finding("code", "t1", success=True, content="result"),
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
        assert len(team_done) == 1
        payload = json.loads(team_done[0]["data"])
        assert "outcome" in payload
        assert payload["outcome"] == "success"

    @pytest.mark.asyncio
    async def test_all_success_outcome(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """全部成功 → outcome=success。"""
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
            "code:t1:0": _finding("code", "t1", success=True, content="result A"),
            "rag:t2:0": _finding("rag", "t2", success=True, content="result B"),
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
        assert payload["outcome"] == "success"

    @pytest.mark.asyncio
    async def test_partial_success_outcome(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """部分成功 → outcome=partial。"""
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
            "code:t1:0": _finding("code", "t1", success=True, content="ok"),
            "deep:t2:0": _finding("deep", "t2", success=False, content="fail"),
        }
        state: TeamState = {
            "message": "test",
            "thread_id": "t1",
            "findings": findings,
            "errors": ["deep:t2:0: fail"],
            "warnings": [],
        }  # type: ignore[typeddict-item]

        await aggregate_node(state)

        team_done = [e for e in emitted if e.get("event") == "team_done"]
        payload = json.loads(team_done[0]["data"])
        assert payload["outcome"] == "partial"

    @pytest.mark.asyncio
    async def test_all_failed_not_success_outcome(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """全失败 → outcome=error（不得标为 success/done）。"""
        monkeypatch.setattr("app.team.nodes.get_settings", lambda: _mock_settings())
        emitted: list[dict] = []
        monkeypatch.setattr("app.team.nodes.get_stream_writer", lambda: (lambda e: emitted.append(e)))

        mock_abort = MagicMock()
        mock_abort.is_set.return_value = False
        monkeypatch.setattr("app.team.nodes.get_abort_event", AsyncMock(return_value=mock_abort))

        findings = {
            "code:t1:0": _finding("code", "t1", success=False, content="失败"),
            "deep:t2:0": _finding("deep", "t2", success=False, content="另一失败"),
        }
        state: TeamState = {
            "message": "test",
            "thread_id": "t1",
            "findings": findings,
            "errors": ["code:t1:0: 失败", "deep:t2:0: 另一失败"],
            "warnings": [],
        }  # type: ignore[typeddict-item]

        await aggregate_node(state)

        team_done = [e for e in emitted if e.get("event") == "team_done"]
        assert len(team_done) == 1
        payload = json.loads(team_done[0]["data"])
        assert payload["outcome"] == "error"
        # 不得标为 success
        assert payload["outcome"] != "success"

    @pytest.mark.asyncio
    async def test_abort_outcome(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """abort → outcome=aborted。"""
        monkeypatch.setattr("app.team.nodes.get_settings", lambda: _mock_settings())
        emitted: list[dict] = []
        monkeypatch.setattr("app.team.nodes.get_stream_writer", lambda: (lambda e: emitted.append(e)))

        mock_abort = MagicMock()
        mock_abort.is_set.return_value = True
        monkeypatch.setattr("app.team.nodes.get_abort_event", AsyncMock(return_value=mock_abort))

        findings = {
            "code:t1:0": _finding("code", "t1", success=True, content="result"),
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
        assert payload["outcome"] == "aborted"

    @pytest.mark.asyncio
    async def test_aggregate_exception_outcome_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """聚合器异常 → outcome=error。"""
        monkeypatch.setattr("app.team.nodes.get_settings", lambda: _mock_settings())
        emitted: list[dict] = []
        monkeypatch.setattr("app.team.nodes.get_stream_writer", lambda: (lambda e: emitted.append(e)))

        async def _exploding_agg(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("aggregator crashed")
            yield  # pragma: no cover

        monkeypatch.setattr("app.team.nodes._run_aggregator", _exploding_agg)
        mock_abort = MagicMock()
        mock_abort.is_set.return_value = False
        monkeypatch.setattr("app.team.nodes.get_abort_event", AsyncMock(return_value=mock_abort))

        findings = {
            "code:t1:0": _finding("code", "t1", success=True, content="result"),
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
        assert len(team_done) == 1
        payload = json.loads(team_done[0]["data"])
        assert payload["outcome"] == "error"

    @pytest.mark.asyncio
    async def test_empty_findings_outcome_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """findings 为空 → outcome=error。"""
        monkeypatch.setattr("app.team.nodes.get_settings", lambda: _mock_settings())
        emitted: list[dict] = []
        monkeypatch.setattr("app.team.nodes.get_stream_writer", lambda: (lambda e: emitted.append(e)))

        mock_abort = MagicMock()
        mock_abort.is_set.return_value = False
        monkeypatch.setattr("app.team.nodes.get_abort_event", AsyncMock(return_value=mock_abort))

        state: TeamState = {
            "message": "test",
            "thread_id": "t1",
            "findings": {},
            "errors": [],
            "warnings": [],
        }  # type: ignore[typeddict-item]

        await aggregate_node(state)

        team_done = [e for e in emitted if e.get("event") == "team_done"]
        payload = json.loads(team_done[0]["data"])
        assert payload["outcome"] == "error"


# ============================================================
# I3.2 + I3.3: agents 列表补齐 task_id / success / retries
# ============================================================


class TestTeamDoneAgentsFields:
    """team_done 的 agents 列表必须含 task_id / success / retries。"""

    @pytest.mark.asyncio
    async def test_agents_have_task_id_success_retries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """每个 agent 项必须包含 task_id / success / retries 字段。"""
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
            "code:t1:0": _finding("code", "t1", success=True, content="ok", retries=0),
            "deep:t2:0": _finding("deep", "t2", success=False, content="fail", retries=2),
        }
        state: TeamState = {
            "message": "test",
            "thread_id": "t1",
            "findings": findings,
            "errors": ["deep:t2:0: fail"],
            "warnings": [],
        }  # type: ignore[typeddict-item]

        await aggregate_node(state)

        team_done = [e for e in emitted if e.get("event") == "team_done"]
        payload = json.loads(team_done[0]["data"])
        agents = payload["agents"]
        assert len(agents) == 2

        by_task = {a["task_id"]: a for a in agents}
        # t1 成功
        assert by_task["t1"]["success"] is True
        assert by_task["t1"]["retries"] == 0
        assert by_task["t1"]["agent"] == "code"
        # t2 失败 + 2 次重试
        assert by_task["t2"]["success"] is False
        assert by_task["t2"]["retries"] == 2
        assert by_task["t2"]["agent"] == "deep"
