"""I4.3: DangerousTaskClassifier 接入 execute_node 集成测试（REQ-TEAM-SAFETY-1）。

覆盖：
- 非 deep 任务含危险关键词时被分类器升级为 deep + 发射 classification SSE
- 非 deep 任务安全描述时保持原 agent + 不发射 classification SSE
- deep 任务跳过分类（不调 classifier）
- classification SSE payload 含 task_id / is_dangerous / reason / suggested_agent

使用真实 ``DangerousTaskClassifier(chat_model=None)``（关键词降级路径），不 mock LLM。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.team.nodes import execute_node
from app.team.state import Finding, SubtaskState, TeamTask


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
        "team_max_retries": 0,
        "llm_temperature_orchestrator": 0.3,
        "llm_temperature_aggregator": 0.3,
        "team_subagents": {},
        "subagents": {},
        "custom_subagents": {},
        "tools_enabled": {},
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_state(
    task: TeamTask,
    **overrides: Any,
) -> SubtaskState:
    """构造 SubtaskState（用于 execute_node 测试）。"""
    state: SubtaskState = {
        "task": task,
        "upstream_findings": {},
        "wave_index": 0,
        "parent_thread_id": "parent-1",
        "remaining_waves": [],
        "history": [],
        "permission_mode": "standard",
        "profile_prompt": "",
        "scene_prompt": "",
        "workspace_path": None,
        "chat_model": None,
        "subtask_runners": {},
        "subtask_timeout": 30,
        "abort_event": asyncio.Event(),
    }  # type: ignore[typeddict-item]
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


# ============================================================
# I4.3: 危险任务被分类器升级为 deep
# ============================================================


class TestDangerousTaskUpgraded:
    """非 deep 任务含危险关键词时被 ``DangerousTaskClassifier`` 升级为 deep。"""

    @pytest.mark.asyncio
    async def test_dangerous_task_upgraded_to_deep(self) -> None:
        """含 '修改' 关键词的 code 任务被升级为 deep。"""
        task = TeamTask(
            id="t1",
            agent="code",
            description="修改配置文件",
            expected_output="配置已更新",
        )
        state = _make_state(task)

        emitted: list[dict] = []
        captured: dict[str, Any] = {}

        async def fake_run_subtask_stream(runner: Any, **kwargs: Any) -> Any:
            from app.team.blackboard import TeamSubtaskResult

            captured["agent_name"] = kwargs.get("agent_name")
            return TeamSubtaskResult(
                agent=kwargs.get("agent_name", "code"),
                success=True,
                payload="done",
            )

        with patch("app.team.nodes._run_subtask_stream", new=AsyncMock(side_effect=fake_run_subtask_stream)), \
             patch("app.team.nodes._inherit_workspace", new=AsyncMock(return_value=None)), \
             patch("app.team.nodes.get_abort_event", new=AsyncMock(return_value=asyncio.Event())), \
             patch("app.team.nodes.get_stream_writer", return_value=lambda e: emitted.append(e)), \
             patch("app.team.nodes.get_settings", return_value=_mock_settings()):
            await execute_node(state)

        # 1. agent_name 传给 runner 的是 "deep"（升级后）
        assert captured["agent_name"] == "deep", \
            f"危险任务应升级为 deep，实际 agent_name={captured['agent_name']}"
        # 2. task.agent 被改写为 "deep"
        assert task.agent == "deep", f"task.agent 应为 deep，实际 {task.agent}"
        # 3. classification SSE 被发射
        classification_events = [e for e in emitted if e.get("event") == "classification"]
        assert len(classification_events) == 1, "应发射 1 个 classification SSE 事件"
        payload = json.loads(classification_events[0]["data"])
        assert payload["is_dangerous"] is True
        assert payload["task_id"] == "t1"
        assert payload["suggested_agent"] == "deep"
        assert "reason" in payload

    @pytest.mark.asyncio
    async def test_dangerous_task_classification_sse_has_original_agent(self) -> None:
        """classification SSE payload 含 original_agent 字段。"""
        task = TeamTask(
            id="t2",
            agent="code",
            description="写入新文件",
            expected_output="文件已创建",
        )
        state = _make_state(task)

        emitted: list[dict] = []

        async def fake_run_subtask_stream(runner: Any, **kwargs: Any) -> Any:
            from app.team.blackboard import TeamSubtaskResult

            return TeamSubtaskResult(agent="deep", success=True, payload="done")

        with patch("app.team.nodes._run_subtask_stream", new=AsyncMock(side_effect=fake_run_subtask_stream)), \
             patch("app.team.nodes._inherit_workspace", new=AsyncMock(return_value=None)), \
             patch("app.team.nodes.get_abort_event", new=AsyncMock(return_value=asyncio.Event())), \
             patch("app.team.nodes.get_stream_writer", return_value=lambda e: emitted.append(e)), \
             patch("app.team.nodes.get_settings", return_value=_mock_settings()):
            await execute_node(state)

        classification_events = [e for e in emitted if e.get("event") == "classification"]
        assert len(classification_events) == 1
        payload = json.loads(classification_events[0]["data"])
        assert payload["original_agent"] == "code"


# ============================================================
# I4.3: 安全任务不被改写
# ============================================================


class TestSafeTaskUnchanged:
    """非 deep 任务安全描述时保持原 agent，不发射 classification SSE。"""

    @pytest.mark.asyncio
    async def test_safe_task_not_upgraded(self) -> None:
        """不含危险关键词的 code 任务保持原 agent。"""
        task = TeamTask(
            id="t3",
            agent="code",
            description="读取文件列表",
            expected_output="文件列表",
        )
        state = _make_state(task)

        emitted: list[dict] = []
        captured: dict[str, Any] = {}

        async def fake_run_subtask_stream(runner: Any, **kwargs: Any) -> Any:
            from app.team.blackboard import TeamSubtaskResult

            captured["agent_name"] = kwargs.get("agent_name")
            return TeamSubtaskResult(
                agent=kwargs.get("agent_name", "code"),
                success=True,
                payload="done",
            )

        with patch("app.team.nodes._run_subtask_stream", new=AsyncMock(side_effect=fake_run_subtask_stream)), \
             patch("app.team.nodes._inherit_workspace", new=AsyncMock(return_value=None)), \
             patch("app.team.nodes.get_abort_event", new=AsyncMock(return_value=asyncio.Event())), \
             patch("app.team.nodes.get_stream_writer", return_value=lambda e: emitted.append(e)), \
             patch("app.team.nodes.get_settings", return_value=_mock_settings()):
            await execute_node(state)

        # agent_name 仍是 "code"
        assert captured["agent_name"] == "code"
        # task.agent 未改变
        assert task.agent == "code"
        # 无 classification SSE（安全任务不发射）
        classification_events = [e for e in emitted if e.get("event") == "classification"]
        assert len(classification_events) == 0, "安全任务不应发射 classification SSE"


# ============================================================
# I4.3: deep 任务跳过分类
# ============================================================


class TestDeepTaskSkipsClassification:
    """agent='deep' 的任务跳过 DangerousTaskClassifier。"""

    @pytest.mark.asyncio
    async def test_deep_task_no_classification(self) -> None:
        """deep 任务不触发分类器，不发射 classification SSE。"""
        task = TeamTask(
            id="t4",
            agent="deep",
            description="修改文件并执行命令",
            expected_output="完成",
        )
        state = _make_state(task)

        emitted: list[dict] = []
        captured: dict[str, Any] = {}

        async def fake_run_subtask_stream(runner: Any, **kwargs: Any) -> Any:
            from app.team.blackboard import TeamSubtaskResult

            captured["agent_name"] = kwargs.get("agent_name")
            return TeamSubtaskResult(
                agent=kwargs.get("agent_name", "deep"),
                success=True,
                payload="done",
            )

        with patch("app.team.nodes._run_subtask_stream", new=AsyncMock(side_effect=fake_run_subtask_stream)), \
             patch("app.team.nodes._inherit_workspace", new=AsyncMock(return_value=None)), \
             patch("app.team.nodes.get_abort_event", new=AsyncMock(return_value=asyncio.Event())), \
             patch("app.team.nodes.get_stream_writer", return_value=lambda e: emitted.append(e)), \
             patch("app.team.nodes.get_settings", return_value=_mock_settings()):
            await execute_node(state)

        # agent_name 仍是 "deep"
        assert captured["agent_name"] == "deep"
        # 无 classification SSE
        classification_events = [e for e in emitted if e.get("event") == "classification"]
        assert len(classification_events) == 0, "deep 任务不应触发分类器"
