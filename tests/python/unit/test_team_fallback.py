"""AgentTeam fallback 与显式失败单元测试（T20）。

覆盖：
- ``_resolve_subtask_config`` 未知 agent 经 ``_DEFAULT_FALLBACK_CONFIG`` fallback 到 code runner
- ``_run_subtask_node`` 未知 agent 走 code runner 路径（不返回失败）
- ``_run_team_role_subtask`` 缺 ``system_prompt`` 显式失败（不静默降级到 coding）
- ``validate_team_subagents`` 启动校验：缺 system_prompt 抛 ValueError / 正常配置不报错
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import get_settings
from app.config.subagents import SubagentSettings, validate_team_subagents
from app.team.blackboard import TeamPlanTask, TeamSubtaskResult
from app.team.orchestrator import (
    _DEFAULT_FALLBACK_CONFIG,
    _NODE_DISPATCH,
    _resolve_subtask_config,
    _run_subtask_node,
)
from app.team.scheduler import _run_team_role_subtask


# ============================================================
# 公共 fixtures
# ============================================================


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例前后清理 settings lru_cache。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_abort_state() -> None:
    """每个用例前后清理全局 abort 状态。"""
    from app.security.approval import state as approval_state

    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()
    yield
    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()


def _make_settings_with_team(
    team: dict[str, SubagentSettings] | None = None,
) -> SimpleNamespace:
    """构造含 team_subagents 的 settings。"""
    return SimpleNamespace(
        team_subagents=team or {},
        tools_enabled={"read_file": True, "web_search": True, "rag_retrieve": True},
    )


# ============================================================
# T20-1: 默认 fallback 配置 — 未知 agent 走 code runner
# ============================================================


class TestDefaultConfigFallback:
    """未知 agent 经 ``_DEFAULT_FALLBACK_CONFIG`` fallback 到 code runner。"""

    def test_resolve_subtask_config_unknown_agent_returns_default_fallback(self) -> None:
        """``_resolve_subtask_config`` 对未知 agent 返回 ``_DEFAULT_FALLBACK_CONFIG``。"""
        settings = _make_settings_with_team(team={})
        config = _resolve_subtask_config("totally_unknown_agent", settings)
        # 应返回默认 fallback 配置
        assert config is _DEFAULT_FALLBACK_CONFIG
        # runner_type 为 code（fallback 到 code runner，不返回失败）
        assert config.runner_type == "code"
        assert config.runner_key == "code"
        # 含 pre_run_hook（记录 warning 日志）
        assert config.pre_run_hook is not None

    def test_default_fallback_config_not_in_node_dispatch_directly(self) -> None:
        """``_DEFAULT_FALLBACK_CONFIG`` 是 ``_NODE_DISPATCH["default"]`` 的别名。"""
        assert _DEFAULT_FALLBACK_CONFIG is _NODE_DISPATCH["default"]
        # runner_type 为 code（不是 "default" 也不是 "failure"）
        assert _DEFAULT_FALLBACK_CONFIG.runner_type == "code"

    async def test_run_subtask_node_unknown_agent_uses_code_runner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未知 agent 经 fallback 走 code runner 路径（不返回失败 payload）。"""
        # 构造 SubtaskState：未知 agent 任务
        state: dict[str, Any] = {
            "task": {
                "agent": "totally_unknown_agent",
                "input": "分析项目结构",
                "purpose": "",
                "deps": [],
            },
            "task_index": 0,
            "parent_thread_id": "t-fallback",
            "todos": [{"content": "未知任务", "status": "pending"}],
            "remaining_levels": [],
            "history": [],
            "permission_mode": "standard",
            "profile_prompt": "",
            "workspace_path": None,  # 不触发 _inherit_workspace
            "chat_model": None,
            "subtask_runners": None,
            "team_semaphore": None,
            "subtask_timeout": 300,
        }

        # mock stream writer（_run_subtask_node 发射 delegation / todo_in_progress 事件）
        monkeypatch.setattr(
            "app.team.orchestrator.get_stream_writer", lambda: lambda _e: None
        )
        # mock abort_event（未中止）— 用真实 asyncio.Event 避免 wait() 返回非协程
        fake_abort = asyncio.Event()
        monkeypatch.setattr(
            "app.team.orchestrator.get_abort_event",
            AsyncMock(return_value=fake_abort),
        )

        # mock code runner：记录被调用 + 返回成功结果
        runner_called: dict[str, bool] = {}

        async def _fake_runner(message: str, thread_id: str, **_: Any) -> AsyncIterator[dict]:
            runner_called["code"] = True
            yield {"event": "token", "data": "代码分析结果"}

        # mock _get_runner 返回 fake runner
        monkeypatch.setattr(
            "app.team.orchestrator._get_runner", lambda _key, _runners: _fake_runner
        )

        result = await _run_subtask_node(state)

        # code runner 被调用（fallback 路径生效）
        assert runner_called.get("code") is True

        # 返回的是成功结果（findings 含结果），不是 errors
        # D6: findings key 使用原始 agent 名（保留前端展示真实意图），
        # 实际 runner 走 code，但 key 为 "totally_unknown_agent-0"
        assert "findings" in result
        assert "totally_unknown_agent-0" in result["findings"]
        assert "代码分析结果" in result["findings"]["totally_unknown_agent-0"]
        # 不应含 errors
        assert "errors" not in result or not result.get("errors", {})


# ============================================================
# T20-2: team_role 显式失败 — system_prompt 为空不静默降级
# ============================================================


class TestTeamRoleExplicitFail:
    """``_run_team_role_subtask`` 缺 system_prompt 显式失败。"""

    async def test_team_role_explicit_fail_on_missing_system_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """system_prompt 为空 → 返回失败结果（不 fallback 到 coding Expert）。"""
        # settings.team_subagents 含启用角色但 system_prompt 为空
        fake_settings = SimpleNamespace(
            team_subagents={
                "frontend_dev": SubagentSettings(
                    enabled=True,
                    system_prompt="",  # 空 system_prompt
                    tools=["read_file"],
                ),
            },
        )
        monkeypatch.setattr(
            "app.team.scheduler.get_settings", lambda: fake_settings
        )

        task = TeamPlanTask(agent="frontend_dev", input="开发前端", purpose="")
        fake_abort = MagicMock()
        fake_abort.is_set.return_value = False

        result = await _run_team_role_subtask(
            task=task,
            thread_id="t-role-fail",
            history=None,
            permission_mode="standard",
            profile_prompt="",
            task_index=0,
            workspace_path=None,
            chat_model=None,
            subtask_runners=None,
            abort_event=fake_abort,
            writer=lambda _e: None,
        )

        # 显式失败（不 fallback 到 coding）
        assert isinstance(result, TeamSubtaskResult)
        assert result.success is False
        assert result.agent == "frontend_dev"
        # payload 提示 system_prompt 缺失
        assert "system_prompt" in result.payload or "frontend_dev" in result.payload

    async def test_team_role_explicit_fail_on_missing_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``team_subagents`` 中无该角色配置 → 显式失败（不 fallback 到 coding）。"""
        fake_settings = SimpleNamespace(
            team_subagents={},  # 完全没有该角色配置
        )
        monkeypatch.setattr(
            "app.team.scheduler.get_settings", lambda: fake_settings
        )

        task = TeamPlanTask(agent="backend_dev", input="开发后端", purpose="")
        fake_abort = MagicMock()
        fake_abort.is_set.return_value = False

        result = await _run_team_role_subtask(
            task=task,
            thread_id="t-role-missing",
            history=None,
            permission_mode="standard",
            profile_prompt="",
            task_index=0,
            workspace_path=None,
            chat_model=None,
            subtask_runners=None,
            abort_event=fake_abort,
            writer=lambda _e: None,
        )

        # 显式失败
        assert result.success is False
        assert result.agent == "backend_dev"


# ============================================================
# T20-3: 启动校验 validate_team_subagents
# ============================================================


class TestValidateTeamSubagents:
    """``validate_team_subagents`` 启动时校验团队角色配置。"""

    def test_validate_team_subagents_startup_raises_on_empty_system_prompt(self) -> None:
        """启用角色缺少 system_prompt → 抛 ValueError。"""
        settings = SimpleNamespace(
            team_subagents={
                "frontend_dev": SubagentSettings(
                    enabled=True,
                    system_prompt="",  # 空 → 应抛错
                    tools=["read_file"],
                ),
                "backend_dev": SubagentSettings(
                    enabled=True,
                    system_prompt="backend 默认 prompt",
                    tools=["read_file"],
                ),
            },
        )
        with pytest.raises(ValueError, match="system_prompt"):
            validate_team_subagents(settings)

    def test_validate_team_subagents_startup_skips_disabled(self) -> None:
        """禁用角色缺少 system_prompt 不报错（只校验启用的）。"""
        settings = SimpleNamespace(
            team_subagents={
                "frontend_dev": SubagentSettings(
                    enabled=False,  # 禁用
                    system_prompt="",  # 空，但因 disabled 不报错
                    tools=["read_file"],
                ),
                "backend_dev": SubagentSettings(
                    enabled=True,
                    system_prompt="backend 默认 prompt",
                    tools=["read_file"],
                ),
            },
        )
        # 不应抛错
        validate_team_subagents(settings)

    def test_validate_team_subagents_normal(self) -> None:
        """正常配置（所有启用角色都有 system_prompt）→ 不报错。"""
        settings = SimpleNamespace(
            team_subagents={
                "frontend_dev": SubagentSettings(
                    enabled=True,
                    system_prompt="前端开发专家 prompt",
                    tools=["read_file"],
                ),
                "backend_dev": SubagentSettings(
                    enabled=True,
                    system_prompt="后端开发专家 prompt",
                    tools=["read_file"],
                ),
                "tester": SubagentSettings(
                    enabled=False,  # 禁用，system_prompt 空也不报错
                    system_prompt="",
                    tools=[],
                ),
            },
        )
        # 不应抛错
        validate_team_subagents(settings)

    def test_validate_team_subagents_empty_config_ok(self) -> None:
        """空 team_subagents 配置 → 不报错（无角色需要校验）。"""
        settings = SimpleNamespace(team_subagents={})
        validate_team_subagents(settings)

    def test_validate_team_subagents_multiple_missing_lists_all(self) -> None:
        """多个启用角色缺 system_prompt → ValueError 消息列全部缺失角色。"""
        settings = SimpleNamespace(
            team_subagents={
                "frontend_dev": SubagentSettings(
                    enabled=True, system_prompt="", tools=[]
                ),
                "tester": SubagentSettings(
                    enabled=True, system_prompt="   ", tools=[]  # 空白也视为空
                ),
            },
        )
        with pytest.raises(ValueError) as exc_info:
            validate_team_subagents(settings)
        msg = str(exc_info.value)
        # 两个缺失角色都应出现在错误消息中
        assert "frontend_dev" in msg
        assert "tester" in msg
