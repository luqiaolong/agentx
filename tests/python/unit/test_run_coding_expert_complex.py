"""run_coding_expert × ComplexityClassifier 集成测试（REQ-CP-1 / REQ-CP-2 / REQ-CP-4）。

覆盖 ``run_coding_expert`` 入口对 ``ComplexityClassifier`` 的集成行为：
1. 复杂任务 → ``build_coding_expert`` 收到 ``force_todo=True`` + ``include_team_roles=True``
2. 简单任务 → ``build_coding_expert`` 收到 ``force_todo=False`` + ``include_team_roles=False``
3. ``coding_complexity_enabled=False`` → 不构造 ``ComplexityClassifier``，
   ``build_coding_expert`` 收到 ``force_todo=False``
4. ``ComplexityClassifier.classify`` 抛异常 → 降级到简单路径，异常不传播，
   ``build_coding_expert`` 收到 ``force_todo=False``

策略：
- mock ``build_coding_expert`` 捕获 ``force_todo`` / ``include_team_roles`` 参数；
- mock ``ComplexityClassifier``（patch at ``app.scenarios.coding.agent.ComplexityClassifier``）
  控制 ``classify`` 返回值或副作用；
- mock ``run_agent_with_approval`` 产出单个 done 事件，避免运行真实 agent；
- mock ``trigger_profile_auto_extract`` 防止流结束后触发画像提取副作用。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.team.complexity_classifier import ComplexityResult


async def _mock_approval_loop(*args, **kwargs):
    """mock run_agent_with_approval：yield 单个 done 事件。"""
    yield {"event": "done", "data": ""}


class TestRunCodingExpertComplexityIntegration:
    """run_coding_expert × ComplexityClassifier 集成测试。"""

    @pytest.mark.asyncio
    async def test_complex_task_triggers_force_todo_and_team_roles(self) -> None:
        """复杂任务 → build_coding_expert 收到 force_todo=True + include_team_roles=True。"""
        from app.scenarios.coding.agent import run_coding_expert

        complex_result = ComplexityResult(
            is_complex=True,
            reason="多步骤多领域任务",
            suggested_subagents=["frontend_dev"],
        )
        mock_classifier = MagicMock()
        mock_classifier.classify = AsyncMock(return_value=complex_result)

        message = "帮我重构前端组件并写后端 API，分三步完成"

        with patch("app.scenarios.coding.agent.ComplexityClassifier", return_value=mock_classifier), \
             patch("app.scenarios.coding.agent.make_deep_tools", return_value=[]), \
             patch("app.scenarios.coding.agent.load_mcp_tools", new_callable=AsyncMock, return_value=([], set())), \
             patch("app.scenarios.coding.agent.build_coding_expert", new_callable=AsyncMock) as mock_build, \
             patch("app.scenarios.coding.agent.run_agent_with_approval", side_effect=_mock_approval_loop), \
             patch("app.scenarios.coding.agent.trigger_profile_auto_extract", new_callable=AsyncMock):
            events = []
            async for sse in run_coding_expert(message, "test-thread-complex"):
                events.append(sse)

        # build_coding_expert 被调用一次，force_todo / include_team_roles 均为 True
        mock_build.assert_called_once()
        kwargs = mock_build.call_args.kwargs
        assert kwargs.get("force_todo") is True
        assert kwargs.get("include_team_roles") is True

        # classifier.classify 被调用，参数为 (message, 0)
        mock_classifier.classify.assert_called_once()
        call_args = mock_classifier.classify.call_args.args
        assert call_args[0] == message
        assert call_args[1] == 0

        # 流正常产出事件
        assert len(events) >= 1
        assert events[0]["event"] == "done"

    @pytest.mark.asyncio
    async def test_simple_task_does_not_force_todo(self) -> None:
        """简单任务 → build_coding_expert 收到 force_todo=False + include_team_roles=False。"""
        from app.scenarios.coding.agent import run_coding_expert

        simple_result = ComplexityResult(
            is_complex=False,
            reason="简单查询",
            suggested_subagents=[],
        )
        mock_classifier = MagicMock()
        mock_classifier.classify = AsyncMock(return_value=simple_result)

        message = "hi"

        with patch("app.scenarios.coding.agent.ComplexityClassifier", return_value=mock_classifier), \
             patch("app.scenarios.coding.agent.make_deep_tools", return_value=[]), \
             patch("app.scenarios.coding.agent.load_mcp_tools", new_callable=AsyncMock, return_value=([], set())), \
             patch("app.scenarios.coding.agent.build_coding_expert", new_callable=AsyncMock) as mock_build, \
             patch("app.scenarios.coding.agent.run_agent_with_approval", side_effect=_mock_approval_loop), \
             patch("app.scenarios.coding.agent.trigger_profile_auto_extract", new_callable=AsyncMock):
            events = []
            async for sse in run_coding_expert(message, "test-thread-simple"):
                events.append(sse)

        mock_build.assert_called_once()
        kwargs = mock_build.call_args.kwargs
        assert kwargs.get("force_todo") is False
        assert kwargs.get("include_team_roles") is False

        mock_classifier.classify.assert_called_once()
        assert mock_classifier.classify.call_args.args[0] == message

        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_coding_complexity_disabled_skips_classifier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """coding_complexity_enabled=False → 不构造 ComplexityClassifier，build_coding_expert 收到 force_todo=False。"""
        from app.config import get_settings
        from app.scenarios.coding.agent import run_coding_expert

        # 通过 env var 关闭总开关（pydantic-settings 标准 override 路径）
        monkeypatch.setenv("AGENTX_CODING_COMPLEXITY_ENABLED", "false")
        get_settings.cache_clear()

        message = "帮我重构前端组件并写后端 API"

        with patch("app.scenarios.coding.agent.ComplexityClassifier") as mock_classifier_cls, \
             patch("app.scenarios.coding.agent.make_deep_tools", return_value=[]), \
             patch("app.scenarios.coding.agent.load_mcp_tools", new_callable=AsyncMock, return_value=([], set())), \
             patch("app.scenarios.coding.agent.build_coding_expert", new_callable=AsyncMock) as mock_build, \
             patch("app.scenarios.coding.agent.run_agent_with_approval", side_effect=_mock_approval_loop), \
             patch("app.scenarios.coding.agent.trigger_profile_auto_extract", new_callable=AsyncMock):
            events = []
            async for sse in run_coding_expert(message, "test-thread-disabled"):
                events.append(sse)

        # 总开关关闭时 ComplexityClassifier 类未被构造
        mock_classifier_cls.assert_not_called()

        mock_build.assert_called_once()
        kwargs = mock_build.call_args.kwargs
        assert kwargs.get("force_todo") is False
        assert kwargs.get("include_team_roles") is False

        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_classifier_exception_fallback(self) -> None:
        """ComplexityClassifier.classify 抛异常 → 降级到简单路径，异常不传播。"""
        from app.scenarios.coding.agent import run_coding_expert

        mock_classifier = MagicMock()
        mock_classifier.classify = AsyncMock(side_effect=RuntimeError("LLM down"))

        message = "帮我重构前端组件并写后端 API"

        with patch("app.scenarios.coding.agent.ComplexityClassifier", return_value=mock_classifier), \
             patch("app.scenarios.coding.agent.make_deep_tools", return_value=[]), \
             patch("app.scenarios.coding.agent.load_mcp_tools", new_callable=AsyncMock, return_value=([], set())), \
             patch("app.scenarios.coding.agent.build_coding_expert", new_callable=AsyncMock) as mock_build, \
             patch("app.scenarios.coding.agent.run_agent_with_approval", side_effect=_mock_approval_loop), \
             patch("app.scenarios.coding.agent.trigger_profile_auto_extract", new_callable=AsyncMock):
            events = []
            # 不应抛异常：run_coding_expert 内部 try/except 捕获后降级
            async for sse in run_coding_expert(message, "test-thread-exception"):
                events.append(sse)

        # classify 被调用但抛异常
        mock_classifier.classify.assert_called_once()

        # 降级到简单路径
        mock_build.assert_called_once()
        kwargs = mock_build.call_args.kwargs
        assert kwargs.get("force_todo") is False
        assert kwargs.get("include_team_roles") is False

        # 主流程未被阻断
        assert len(events) >= 1
        assert events[0]["event"] == "done"
