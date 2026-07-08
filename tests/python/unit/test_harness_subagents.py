"""harness subagents / rubric 参数测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.deep.harness import create_agent


@pytest.mark.asyncio
async def test_create_agent_passes_subagents_to_create_deep_agent() -> None:
    """create_agent 将 subagents 列表透传给 create_deep_agent。"""
    with patch("app.deep.harness.create_deep_agent") as mock_create:
        mock_graph = MagicMock()
        mock_create.return_value = mock_graph

        model = MagicMock()
        tools = [MagicMock()]
        subagents = [{"name": "rag", "description": "RAG subagent", "system_prompt": "You are RAG."}]

        result = create_agent(
            model,
            tools,
            system_prompt="test",
            subagents=subagents,
        )

        assert result is mock_graph
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        assert kwargs["subagents"] is subagents


@pytest.mark.asyncio
async def test_create_agent_passes_rubric_middleware() -> None:
    """传入 rubric 时 harness 注入 RubricMiddleware。"""
    from deepagents import RubricMiddleware

    with patch("app.deep.harness.create_deep_agent") as mock_create:
        mock_create.return_value = MagicMock()

        model = MagicMock()
        grader = MagicMock()

        create_agent(
            model,
            [],
            system_prompt="test",
            rubric="Must include greeting.",
            grader_model=grader,
        )

        _, kwargs = mock_create.call_args
        middleware = kwargs.get("middleware", [])
        assert any(isinstance(m, RubricMiddleware) for m in middleware)


@pytest.mark.asyncio
async def test_create_agent_no_rubric_without_rubric_param() -> None:
    """未传 rubric 时不注入 RubricMiddleware。"""
    from deepagents import RubricMiddleware

    with patch("app.deep.harness.create_deep_agent") as mock_create:
        mock_create.return_value = MagicMock()
        create_agent(MagicMock(), [], system_prompt="test")
        _, kwargs = mock_create.call_args
        middleware = kwargs.get("middleware", [])
        assert not any(isinstance(m, RubricMiddleware) for m in middleware)
