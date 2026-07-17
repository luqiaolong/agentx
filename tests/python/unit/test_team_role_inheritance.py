"""I4.2: Team role 继承 chat model + prompt 上下文测试（D6 / REQ-TEAM-PROMPT-1）。

覆盖：
- ``build_custom_agent`` 模式 2 使用注入的 ``chat_model``（不回退到 ``get_chat_model``）
- ``build_custom_agent`` 接受 ``extra_system_prompt`` 并拼入 system prompt
- ``_run_team_role_subtask`` 把 ``chat_model`` / ``profile_prompt`` / ``scene_prompt`` 透传

注意：``app.deepagent.__init__`` 存在循环导入（streaming → observation → langsmith → observation），
直接 ``patch("app.deepagent.factory.create_agent")`` 会触发 ``AttributeError: module 'app' has no
attribute 'deepagent'``。本测试通过 ``patch.dict(sys.modules, ...)`` 注入 mock 模块绕过该问题。
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.deepagent.subagents.custom_agent import build_custom_agent


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


def _make_mock_factory() -> tuple[MagicMock, dict[str, Any]]:
    """构造 mock ``app.deepagent.factory`` 模块，绕过循环导入。

    Returns:
        (mock_create_agent, sys_modules_patch_dict)
    """
    mock_create = MagicMock(return_value=MagicMock(name="agent"))
    mock_factory = ModuleType("app.deepagent.factory")
    mock_factory.create_agent = mock_create
    mock_pkg = ModuleType("app.deepagent")
    mock_pkg.__path__ = []  # 标记为 package
    return mock_create, {"app.deepagent": mock_pkg, "app.deepagent.factory": mock_factory}


# ============================================================
# I4.2: build_custom_agent 模式 2 使用注入的 chat_model
# ============================================================


class TestBuildCustomAgentInheritsChatModel:
    """``build_custom_agent`` 模式 2 必须使用注入的 ``chat_model``。"""

    def test_injected_chat_model_used(self) -> None:
        """注入 chat_model 时使用它，不调 get_chat_model。"""
        injected_model = MagicMock(name="injected_model")
        mock_create, mock_modules = _make_mock_factory()

        with patch("app.deepagent.subagents.custom_agent.get_chat_model") as mock_get_model, \
             patch("app.deepagent.subagents.custom_agent.get_settings", return_value=_mock_settings()), \
             patch.dict(sys.modules, mock_modules):
            build_custom_agent(
                key="frontend_dev",
                system_prompt="you are frontend dev",
                tools=["read_file"],
                temperature=0.3,
                chat_model=injected_model,
            )

        # 不得调用 get_chat_model
        mock_get_model.assert_not_called()
        # create_agent 的第一个位置参数（model）必须是注入的 model
        assert mock_create.call_args.args[0] is injected_model

    def test_fallback_to_get_chat_model_when_not_provided(self) -> None:
        """未注入 chat_model 时回退到 get_chat_model。"""
        fallback_model = MagicMock(name="fallback_model")
        mock_create, mock_modules = _make_mock_factory()

        with patch("app.deepagent.subagents.custom_agent.get_chat_model", return_value=fallback_model) as mock_get_model, \
             patch("app.deepagent.subagents.custom_agent.get_settings", return_value=_mock_settings()), \
             patch.dict(sys.modules, mock_modules):
            build_custom_agent(
                key="backend_dev",
                system_prompt="you are backend dev",
                tools=["read_file"],
                temperature=0.2,
                chat_model=None,
            )

        mock_get_model.assert_called_once()
        assert mock_create.call_args.args[0] is fallback_model


# ============================================================
# I4.2: build_custom_agent 接受 extra_system_prompt
# ============================================================


class TestBuildCustomAgentExtraSystemPrompt:
    """``build_custom_agent`` 模式 2 接受 ``extra_system_prompt`` 并拼入。"""

    def test_extra_system_prompt_appended(self) -> None:
        """extra_system_prompt 拼接到 system_prompt 之后。"""
        mock_create, mock_modules = _make_mock_factory()

        with patch("app.deepagent.subagents.custom_agent.get_chat_model", return_value=MagicMock()), \
             patch("app.deepagent.subagents.custom_agent.get_settings", return_value=_mock_settings()), \
             patch.dict(sys.modules, mock_modules):
            build_custom_agent(
                key="tester",
                system_prompt="you are a tester",
                tools=["read_file"],
                temperature=0.2,
                extra_system_prompt="[profile] user prefers concise output",
            )

        # create_agent 的 system_prompt 参数应含 extra_system_prompt
        call_kwargs = mock_create.call_args.kwargs
        prompt = call_kwargs["system_prompt"]
        assert "you are a tester" in prompt
        assert "[profile] user prefers concise output" in prompt

    def test_no_extra_system_prompt(self) -> None:
        """未传 extra_system_prompt 时正常工作。"""
        mock_create, mock_modules = _make_mock_factory()

        with patch("app.deepagent.subagents.custom_agent.get_chat_model", return_value=MagicMock()), \
             patch("app.deepagent.subagents.custom_agent.get_settings", return_value=_mock_settings()), \
             patch.dict(sys.modules, mock_modules):
            build_custom_agent(
                key="devops",
                system_prompt="you are devops",
                tools=["read_file"],
                temperature=0.2,
            )

        call_kwargs = mock_create.call_args.kwargs
        prompt = call_kwargs["system_prompt"]
        assert "you are devops" in prompt


# ============================================================
# I4.2: _run_team_role_subtask 透传 chat_model + profile_prompt + scene_prompt
# ============================================================


class TestRunTeamRoleSubtaskInheritance:
    """``_run_team_role_subtask`` 把 chat_model / profile_prompt / scene_prompt 透传给 build_custom_agent。"""

    @pytest.mark.asyncio
    async def test_chat_model_passed_to_build_custom_agent(self) -> None:
        """注入的 chat_model 透传到 build_custom_agent。"""
        from app.team.blackboard import TeamPlanTask
        from app.team.scheduler import _run_team_role_subtask

        injected_model = MagicMock(name="injected_chat_model")
        mock_agent = MagicMock()
        mock_agent.astream_events = MagicMock(
            return_value=MagicMock(__aiter__=MagicMock(return_value=iter([])))
        )

        settings = _mock_settings()
        settings.team_subagents = {
            "frontend_dev": SimpleNamespace(
                system_prompt="you are frontend dev",
                tools=["read_file"],
                temperature=0.2,
                enabled=True,
            )
        }

        with patch("app.team.scheduler.get_settings", return_value=settings), \
             patch("app.team.scheduler._inherit_workspace", new=AsyncMock(return_value=None)), \
             patch("app.deepagent.subagents.custom_agent.build_custom_agent", return_value=mock_agent) as mock_build:
            task = TeamPlanTask(agent="frontend_dev", input="do task", purpose="")
            await _run_team_role_subtask(
                task=task,
                thread_id="th-1",
                history=[],
                permission_mode="standard",
                profile_prompt="[profile] concise",
                task_index=0,
                workspace_path=None,
                chat_model=injected_model,
                subtask_runners=None,
                abort_event=MagicMock(is_set=lambda: False),
                writer=lambda _e: None,
                subtask_timeout=5,
            )

        mock_build.assert_called_once()
        build_kwargs = mock_build.call_args.kwargs
        assert build_kwargs.get("chat_model") is injected_model

    @pytest.mark.asyncio
    async def test_profile_prompt_passed_to_build_custom_agent(self) -> None:
        """profile_prompt 拼入 extra_system_prompt 透传到 build_custom_agent。"""
        from app.team.blackboard import TeamPlanTask
        from app.team.scheduler import _run_team_role_subtask

        mock_agent = MagicMock()
        mock_agent.astream_events = MagicMock(
            return_value=MagicMock(__aiter__=MagicMock(return_value=iter([])))
        )

        settings = _mock_settings()
        settings.team_subagents = {
            "backend_dev": SimpleNamespace(
                system_prompt="you are backend dev",
                tools=["read_file"],
                temperature=0.2,
                enabled=True,
            )
        }

        with patch("app.team.scheduler.get_settings", return_value=settings), \
             patch("app.team.scheduler._inherit_workspace", new=AsyncMock(return_value=None)), \
             patch("app.deepagent.subagents.custom_agent.build_custom_agent", return_value=mock_agent) as mock_build:
            task = TeamPlanTask(agent="backend_dev", input="do task", purpose="")
            await _run_team_role_subtask(
                task=task,
                thread_id="th-1",
                history=[],
                permission_mode="standard",
                profile_prompt="[profile] user prefers Python",
                scene_prompt="[scene] coding",
                task_index=0,
                workspace_path=None,
                chat_model=MagicMock(),
                subtask_runners=None,
                abort_event=MagicMock(is_set=lambda: False),
                writer=lambda _e: None,
                subtask_timeout=5,
            )

        build_kwargs = mock_build.call_args.kwargs
        extra = build_kwargs.get("extra_system_prompt", "")
        assert "[profile]" in extra
        assert "user prefers Python" in extra
        assert "[scene]" in extra
        assert "coding" in extra
