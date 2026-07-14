"""AgentTeam v2 危险任务分类器（T19 / D6）。

封装 ``DangerousTaskClassifier`` 类，替代旧 ``planner._looks_like_dangerous_task``
子串匹配。支持 LLM 结构化输出路径 + 关键词降级 + 缓存。

设计：
- ``__init__``: 接受可选 ``chat_model``，若支持 ``with_structured_output`` 则
  缓存 ``self._structured`` 供 LLM 路径使用；不支持则降级到关键词匹配。
- ``classify``: 主入口，按 缓存 → LLM → 关键词 顺序尝试。
- ``_keyword_fallback``: 复用 ``utils.text.compile_keyword_patterns``，
  匹配 ``_DANGEROUS_KEYWORDS``（从 planner.py 迁移）。

与旧 ``_looks_like_dangerous_task`` 的差异：
- 返回 ``ClassificationResult``（含 ``is_dangerous`` / ``reason`` / ``suggested_agent``）
  而非裸 ``bool``
- 支持 LLM 路径（结构化输出，比关键词更准确）
- 按 description hash 缓存，避免同一 task 多次调用 LLM
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.llm import make_structured_llm
from app.observability.logger import logger
from app.utils.text import compile_keyword_patterns, matches_any

from app.team.state import ClassificationResult, TeamTask

__all__ = ["DangerousTaskClassifier", "_DANGEROUS_KEYWORDS"]


# 危险关键词（从 planner.py 迁移，统一在 classifier.py 维护）
_DANGEROUS_KEYWORDS: list[str] = [
    "写入",
    "写文件",
    "write",
    "编辑",
    "修改",
    "edit",
    "执行命令",
    "shell",
    "运行脚本",
]
_DANGEROUS_PATTERNS = compile_keyword_patterns(_DANGEROUS_KEYWORDS)


# LLM 分类 prompt 模板
_CLASSIFIER_PROMPT = """你是任务安全分类器。判断以下任务是否涉及危险操作（写文件/编辑/执行命令/删除等）。

任务描述：{description}

可用工具：{available_tools}

判断规则：
- 涉及文件写入、编辑、删除、移动、重命名 → is_dangerous=True, suggested_agent="deep"
- 涉及 shell 命令执行、脚本运行 → is_dangerous=True, suggested_agent="deep"
- 仅读取、查询、搜索、分析 → is_dangerous=False, suggested_agent="code"

输出 ClassificationResult：
- is_dangerous: bool
- reason: 简短中文说明（≤50 字）
- suggested_agent: "deep"（危险）或 "code"（安全）
"""


class DangerousTaskClassifier:
    """LLM 危险任务分类器，替代 ``_looks_like_dangerous_task`` 子串匹配。

    优先级：缓存 → LLM 结构化输出 → 关键词降级。
    """

    def __init__(self, chat_model: Any | None = None) -> None:
        """初始化分类器。

        Args:
            chat_model: 可选的 ChatModel。若支持 ``with_structured_output`` 则
                使用 LLM 路径；不支持或 None 时降级到关键词匹配。
        """
        self._chat_model = chat_model
        self._structured: Any | None = None
        if chat_model is not None:
            try:
                # 强制使用非流式副本，避免 OpenAI SDK 解析空 chunk 崩溃
                self._structured = make_structured_llm(
                    chat_model, ClassificationResult
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "DangerousTaskClassifier LLM does not support structured output, "
                    "fallback to keyword",
                    error=str(exc),
                )

        # 缓存：description hash → ClassificationResult
        self._cache: dict[str, ClassificationResult] = {}

    async def classify(
        self,
        task: TeamTask,
        available_tools: list[str] | None = None,
    ) -> ClassificationResult:
        """分类任务是否危险。

        Args:
            task: 待分类的任务（含 ``description`` / ``agent`` 字段）。
            available_tools: 可选的可用工具列表，供 LLM 参考。

        Returns:
            ``ClassificationResult``：含 ``is_dangerous`` / ``reason`` / ``suggested_agent``。
        """
        # 1. 缓存命中
        cache_key = self._cache_key(task.description)
        if cached := self._cache.get(cache_key):
            return cached

        # 2. LLM 路径
        if self._structured is not None:
            try:
                result = await self._structured.ainvoke(
                    self._build_prompt(task.description, available_tools or [])
                )
                if isinstance(result, ClassificationResult):
                    self._cache[cache_key] = result
                    return result
                # 兼容某些 LLM 返回 dict 的情况
                if isinstance(result, dict):
                    result = ClassificationResult(**result)
                    self._cache[cache_key] = result
                    return result
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "DangerousTaskClassifier LLM failed, fallback to keyword",
                    error=str(exc),
                )

        # 3. 关键词降级
        result = self._keyword_fallback(task)
        self._cache[cache_key] = result
        return result

    def _keyword_fallback(self, task: TeamTask) -> ClassificationResult:
        """关键词匹配降级路径。

        复用 ``utils.text.compile_keyword_patterns`` 匹配 ``_DANGEROUS_KEYWORDS``。
        ASCII 关键词用 ``\\b`` 词边界，CJK 用子串匹配。
        """
        if matches_any(task.description, _DANGEROUS_PATTERNS):
            return ClassificationResult(
                is_dangerous=True,
                reason="匹配危险关键词",
                suggested_agent="deep",
            )
        return ClassificationResult(
            is_dangerous=False,
            reason="无危险关键词",
            suggested_agent="code",
        )

    @staticmethod
    def _cache_key(description: str) -> str:
        """构造缓存 key（description 的 SHA256 hash，截断到 16 字节）。"""
        return hashlib.sha256(description.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _build_prompt(description: str, available_tools: list[str]) -> str:
        """构造 LLM 分类 prompt。"""
        return _CLASSIFIER_PROMPT.format(
            description=description,
            available_tools=", ".join(available_tools) or "无",
        )
