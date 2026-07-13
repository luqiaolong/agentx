"""AgentTeam v2 DangerousTaskClassifier 单元测试（T30）。

覆盖 7 个场景：
1. 关键词降级：危险关键词命中（中文）
2. 关键词降级：危险关键词命中（英文 ASCII 词边界）
3. 关键词降级：安全任务不命中
4. LLM 路径：结构化输出正常返回
5. LLM 路径：LLM 异常时降级到关键词
6. LLM 路径：chat_model 不支持 with_structured_output 时降级
7. 缓存：同 description 多次调用只 LLM 一次
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.team.classifier import DangerousTaskClassifier
from app.team.state import ClassificationResult, TeamTask


def _task(description: str, agent: str = "code") -> TeamTask:
    """构造 TeamTask（便于测试）。"""
    return TeamTask(id="t1", agent=agent, description=description)


# ============================================================
# 关键词降级路径
# ============================================================


class TestKeywordFallback:
    """关键词匹配降级路径测试。"""

    @pytest.mark.asyncio
    async def test_keyword_hit_chinese(self) -> None:
        """中文危险关键词命中。"""
        classifier = DangerousTaskClassifier(chat_model=None)
        task = _task("写入配置文件")
        result = await classifier.classify(task)
        assert result.is_dangerous is True
        assert result.suggested_agent == "deep"

    @pytest.mark.asyncio
    async def test_keyword_hit_english_word_boundary(self) -> None:
        """英文危险关键词命中（ASCII 词边界）。

        'write' 应命中，但 'writer' / 'rewrite' 不应命中（词边界保护）。
        """
        classifier = DangerousTaskClassifier(chat_model=None)

        # 'write' 命中
        result = await classifier.classify(_task("write the file"))
        assert result.is_dangerous is True

        # 'writer' 不命中（词边界保护）
        result = await classifier.classify(_task("the writer is good"))
        assert result.is_dangerous is False

    @pytest.mark.asyncio
    async def test_keyword_miss_safe_task(self) -> None:
        """安全任务不命中关键词。"""
        classifier = DangerousTaskClassifier(chat_model=None)
        result = await classifier.classify(_task("读取文件内容并分析"))
        assert result.is_dangerous is False
        assert result.suggested_agent == "code"


# ============================================================
# LLM 路径
# ============================================================


class TestLLMPath:
    """LLM 结构化输出路径测试。"""

    @pytest.mark.asyncio
    async def test_llm_structured_output_success(self) -> None:
        """LLM 结构化输出正常返回。"""
        expected = ClassificationResult(
            is_dangerous=True,
            reason="涉及文件写入",
            suggested_agent="deep",
        )
        mock_structured = MagicMock()
        mock_structured.ainvoke = AsyncMock(return_value=expected)

        mock_chat_model = MagicMock()
        mock_chat_model.with_structured_output.return_value = mock_structured

        classifier = DangerousTaskClassifier(chat_model=mock_chat_model)
        task = _task("写文件到磁盘")
        result = await classifier.classify(task, available_tools=["write_file", "read_file"])

        assert result is expected
        assert result.is_dangerous is True
        mock_structured.ainvoke.assert_awaited_once()
        # 验证 prompt 含 available_tools
        prompt_arg = mock_structured.ainvoke.await_args.args[0]
        assert "write_file" in prompt_arg
        assert "read_file" in prompt_arg

    @pytest.mark.asyncio
    async def test_llm_failure_fallback_to_keyword(self) -> None:
        """LLM 异常时降级到关键词匹配。"""
        mock_structured = MagicMock()
        mock_structured.ainvoke = AsyncMock(side_effect=RuntimeError("LLM timeout"))

        mock_chat_model = MagicMock()
        mock_chat_model.with_structured_output.return_value = mock_structured

        classifier = DangerousTaskClassifier(chat_model=mock_chat_model)
        # description 含 '编辑' 关键词，降级后应命中
        result = await classifier.classify(_task("编辑配置文件"))

        assert result.is_dangerous is True
        assert result.suggested_agent == "deep"

    @pytest.mark.asyncio
    async def test_llm_not_support_structured_output(self) -> None:
        """chat_model 不支持 with_structured_output 时降级到关键词。"""
        mock_chat_model = MagicMock()
        mock_chat_model.with_structured_output.side_effect = NotImplementedError(
            "not supported"
        )

        classifier = DangerousTaskClassifier(chat_model=mock_chat_model)
        result = await classifier.classify(_task("执行命令"))
        assert result.is_dangerous is True
        assert result.suggested_agent == "deep"


# ============================================================
# 缓存
# ============================================================


class TestCache:
    """缓存行为测试。"""

    @pytest.mark.asyncio
    async def test_cache_hit_only_one_llm_call(self) -> None:
        """同 description 多次调用只触发一次 LLM。"""
        expected = ClassificationResult(
            is_dangerous=False,
            reason="safe",
            suggested_agent="code",
        )
        mock_structured = MagicMock()
        mock_structured.ainvoke = AsyncMock(return_value=expected)

        mock_chat_model = MagicMock()
        mock_chat_model.with_structured_output.return_value = mock_structured

        classifier = DangerousTaskClassifier(chat_model=mock_chat_model)
        task = _task("读取文件并分析")

        # 第一次调用：触发 LLM
        result1 = await classifier.classify(task)
        assert result1 is expected
        assert mock_structured.ainvoke.await_count == 1

        # 第二次调用：缓存命中，不触发 LLM
        result2 = await classifier.classify(task)
        assert result2 is expected
        assert mock_structured.ainvoke.await_count == 1  # 仍是 1
