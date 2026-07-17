"""ComplexityClassifier 单元测试（REQ-CP-1 / REQ-CP-2 / REQ-CP-3）。

覆盖：
- LLM 结构化输出路径（mock make_structured_llm）
- LLM 返回 is_complex=False
- 启发式信号降级（4 个信号 + 简单消息）
- 缓存命中（同 message+history_count 仅调用 LLM 一次）
- LLM 异常 → 启发式降级
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.team.complexity_classifier import ComplexityClassifier, ComplexityResult


# ============================================================
# LLM 路径
# ============================================================


class TestLLMPath:
    """LLM 结构化输出路径（mock make_structured_llm）。"""

    @pytest.mark.asyncio
    async def test_llm_returns_complex_result(self) -> None:
        """LLM 返回 is_complex=True 时结果被返回并缓存。"""
        expected = ComplexityResult(
            is_complex=True,
            reason="多步骤任务",
            suggested_subagents=["frontend_dev"],
        )
        structured_mock = MagicMock()
        structured_mock.ainvoke = AsyncMock(return_value=expected)

        with patch(
            "app.team.complexity_classifier.make_structured_llm",
            return_value=structured_mock,
        ):
            classifier = ComplexityClassifier(MagicMock())
            result = await classifier.classify("帮我重构前端组件", 0)

        assert result.is_complex is True
        assert result.reason == "多步骤任务"
        assert result.suggested_subagents == ["frontend_dev"]
        structured_mock.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_returns_simple_result(self) -> None:
        """LLM 返回 is_complex=False 时简单结果被返回。"""
        expected = ComplexityResult(
            is_complex=False,
            reason="简单查询",
            suggested_subagents=[],
        )
        structured_mock = MagicMock()
        structured_mock.ainvoke = AsyncMock(return_value=expected)

        with patch(
            "app.team.complexity_classifier.make_structured_llm",
            return_value=structured_mock,
        ):
            classifier = ComplexityClassifier(MagicMock())
            result = await classifier.classify("hi", 0)

        assert result.is_complex is False
        assert result.suggested_subagents == []

    @pytest.mark.asyncio
    async def test_cache_hit_calls_llm_once(self) -> None:
        """同 message+history_count 第二次调用命中缓存，LLM 仅被调用一次。"""
        expected = ComplexityResult(
            is_complex=True,
            reason="缓存测试",
            suggested_subagents=["backend_dev"],
        )
        structured_mock = MagicMock()
        structured_mock.ainvoke = AsyncMock(return_value=expected)

        with patch(
            "app.team.complexity_classifier.make_structured_llm",
            return_value=structured_mock,
        ):
            classifier = ComplexityClassifier(MagicMock())
            await classifier.classify("重复消息", 3)
            await classifier.classify("重复消息", 3)

        assert structured_mock.ainvoke.call_count == 1

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back_to_heuristic(self) -> None:
        """LLM ainvoke 抛异常时降级到启发式，不传播异常。"""
        structured_mock = MagicMock()
        structured_mock.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))

        with patch(
            "app.team.complexity_classifier.make_structured_llm",
            return_value=structured_mock,
        ):
            classifier = ComplexityClassifier(MagicMock())
            # 长消息触发启发式信号 1（长度超阈值）
            long_message = "x" * 300
            result = await classifier.classify(long_message, 0)

        assert result.is_complex is True
        assert "消息过长" in result.reason
        structured_mock.ainvoke.assert_called_once()


# ============================================================
# 启发式信号降级
# ============================================================


class TestHeuristicFallback:
    """启发式信号降级路径（chat_model=None，跳过 LLM）。"""

    @pytest.mark.asyncio
    async def test_signal_length(self) -> None:
        """信号 1：消息长度超阈值 → is_complex=True，reason 提及长度。"""
        classifier = ComplexityClassifier(None)
        long_message = "x" * 300  # 默认阈值 200
        result = await classifier.classify(long_message, 0)

        assert result.is_complex is True
        assert "消息过长" in result.reason

    @pytest.mark.asyncio
    async def test_signal_multi_domain(self) -> None:
        """信号 2：前端+后端关键词共现 → is_complex=True，suggested_subagents 含 frontend_dev 和 backend_dev。"""
        classifier = ComplexityClassifier(None)
        message = "请帮我做前端和后端的工作"
        result = await classifier.classify(message, 0)

        assert result.is_complex is True
        assert "多领域关键词共现" in result.reason
        assert "frontend_dev" in result.suggested_subagents
        assert "backend_dev" in result.suggested_subagents

    @pytest.mark.asyncio
    async def test_signal_multi_step(self) -> None:
        """信号 3：消息含 '首先...然后...' → is_complex=True。"""
        classifier = ComplexityClassifier(None)
        message = "首先读取文件，然后修改内容"
        result = await classifier.classify(message, 0)

        assert result.is_complex is True
        assert "多步骤信号" in result.reason

    @pytest.mark.asyncio
    async def test_signal_history(self) -> None:
        """信号 4：history_count=15 > 阈值 10 → is_complex=True。"""
        classifier = ComplexityClassifier(None)
        result = await classifier.classify("hi", 15)

        assert result.is_complex is True
        assert "历史轮次过多" in result.reason

    @pytest.mark.asyncio
    async def test_simple_message_not_complex(self) -> None:
        """简单消息 'hi' + history_count=0 → is_complex=False，suggested_subagents 为空。"""
        classifier = ComplexityClassifier(None)
        result = await classifier.classify("hi", 0)

        assert result.is_complex is False
        assert result.reason == "无复杂信号"
        assert result.suggested_subagents == []
