"""Coding 模式自适应规划复杂度分类器（REQ-CP-1 / REQ-CP-2 / REQ-CP-3）。

封装 ``ComplexityClassifier`` 类，判断用户消息是否属于复杂任务，从而决定是否
强制触发 todo 规划 + 团队角色 subagent 委派。支持 LLM 结构化输出路径 + 启发式
信号降级 + 缓存。

设计：
- ``__init__``: 接受可选 ``chat_model``，若支持 ``with_structured_output`` 则
  缓存 ``self._structured`` 供 LLM 路径使用；不支持则降级到启发式信号。
- ``classify``: 主入口，按 缓存 → LLM → 启发式 顺序尝试。
- ``_heuristic_fallback``: 实现 REQ-CP-3 的 4 个信号：
  1. 消息长度超阈值（默认 200）
  2. 多领域关键词共现（2+ 组命中）
  3. 多步骤信号（首先/然后、第一步、多个文件等）
  4. 历史轮次超阈值（默认 10）

与 ``DangerousTaskClassifier`` 的差异：
- 返回 ``ComplexityResult``（含 ``is_complex`` / ``reason`` / ``suggested_subagents``）
- 关注「复杂度」而非「危险性」
- ``suggested_subagents`` 提供角色 key 列表，供下游 ScenarioTeam 委派
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field

from app.config import get_settings
from app.llm import make_structured_llm
from app.observability.logger import logger

__all__ = ["ComplexityClassifier", "ComplexityResult"]


# 多领域关键词分组（REQ-CP-3 信号 2）
# ASCII 关键词大小写不敏感匹配；CJK 子串匹配。一组中任一关键词命中即视为该组命中。
_DOMAIN_KEYWORD_GROUPS: dict[str, list[str]] = {
    "frontend_dev": ["前端", "frontend", "react", "vue", "angular", "css", "html", "UI 组件"],
    "backend_dev": ["后端", "backend", "api", "数据库", "database", "microservice", "微服务"],
    "tester": ["测试", "test", "pytest", "jest", "coverage", "覆盖率", "e2e"],
    "refactor": ["重构", "refactor", "重写", "优化结构"],
    "architect": ["架构", "architecture", "设计模式", "ddd", "分层"],
    "devops": ["部署", "deploy", "ci/cd", "docker", "k8s", "监控"],
}

# 重构组无对应角色 key，按 REQ-CP-3 要求在 suggested_subagents 中省略
_GROUP_TO_ROLE: dict[str, str] = {
    "frontend_dev": "frontend_dev",
    "backend_dev": "backend_dev",
    "tester": "tester",
    "architect": "architect",
    "devops": "devops",
}


# LLM 分类 prompt 模板
_COMPLEXITY_PROMPT = """你是 Coding 模式任务复杂度分类器。判断以下用户消息是否属于复杂任务，需要：
(a) 3 步及以上的规划（多步骤、多文件、分阶段）
(b) subagent 委派（涉及多个领域/角色协作）

用户消息：{message}

历史轮次：{history_count}

判断规则：
- 涉及多步骤、多文件、分阶段执行 → is_complex=True
- 涉及多领域协作（前端+后端、后端+测试、架构+重构等）→ is_complex=True
- 历史轮次较多（上下文复杂）→ 倾向 is_complex=True
- 单步、单领域、简单查询 → is_complex=False

输出 ComplexityResult：
- is_complex: bool
- reason: 简短中文说明（≤50 字）
- suggested_subagents: 角色 key 列表，可选值：frontend_dev, backend_dev, tester, architect, devops, ui_designer, product_manager
"""


class ComplexityResult(BaseModel):
    """ComplexityClassifier 的 LLM 结构化输出（REQ-CP-1）。"""

    is_complex: bool
    reason: str  # ≤50 字中文
    suggested_subagents: list[str] = Field(default_factory=list)


class ComplexityClassifier:
    """Coding 模式任务复杂度分类器（REQ-CP-2）。

    优先级：缓存 → LLM 结构化输出 → 启发式信号降级。
    """

    def __init__(self, chat_model: Any | None = None) -> None:
        """初始化分类器。

        Args:
            chat_model: 可选的 ChatModel。若支持 ``with_structured_output`` 则
                使用 LLM 路径；不支持或 None 时降级到启发式信号。
        """
        self._chat_model = chat_model
        self._structured: Any | None = None
        if chat_model is not None:
            try:
                # 强制使用非流式副本，避免 OpenAI SDK 解析空 chunk 崩溃
                self._structured = make_structured_llm(
                    chat_model, ComplexityResult
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ComplexityClassifier LLM does not support structured output, "
                    "fallback to heuristic",
                    error=str(exc),
                )

        # 缓存：cache_key → ComplexityResult
        self._cache: dict[str, ComplexityResult] = {}

    async def classify(
        self,
        message: str,
        history_count: int = 0,
    ) -> ComplexityResult:
        """分类任务是否复杂。

        Args:
            message: 用户消息文本。
            history_count: 当前会话历史轮次数（影响复杂度判断）。

        Returns:
            ``ComplexityResult``：含 ``is_complex`` / ``reason`` / ``suggested_subagents``。
        """
        # 1. 缓存命中
        cache_key = self._cache_key(message, history_count)
        if cached := self._cache.get(cache_key):
            return cached

        # 2. LLM 路径
        if self._structured is not None:
            try:
                result = await self._structured.ainvoke(
                    self._build_prompt(message, history_count)
                )
                if isinstance(result, ComplexityResult):
                    self._cache[cache_key] = result
                    return result
                # 兼容某些 LLM 返回 dict 的情况
                if isinstance(result, dict):
                    result = ComplexityResult(**result)
                    self._cache[cache_key] = result
                    return result
                logger.warning(
                    "ComplexityClassifier LLM returned unexpected type, fallback to heuristic",
                    result_type=type(result).__name__,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ComplexityClassifier LLM failed, fallback to heuristic",
                    error=str(exc),
                )

        # 3. 启发式信号降级
        result = self._heuristic_fallback(message, history_count)
        self._cache[cache_key] = result
        return result

    def _cache_key(self, message: str, history_count: int) -> str:
        """构造缓存 key（``message|history_count`` 的 SHA256 hash，截断到 16 字节）。

        包含 ``history_count`` 是因为历史轮次会影响复杂度判断，相同消息在不同
        历史长度下可能产生不同分类结果。
        """
        raw = f"{message}|{history_count}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _build_prompt(self, message: str, history_count: int) -> str:
        """构造 LLM 分类 prompt。"""
        return _COMPLEXITY_PROMPT.format(
            message=message,
            history_count=history_count,
        )

    def _heuristic_fallback(
        self, message: str, history_count: int
    ) -> ComplexityResult:
        """启发式信号降级路径（REQ-CP-3）。

        实现 4 个信号，任一命中即 ``is_complex=True``：
        1. 消息长度超阈值（``settings.coding_complexity_message_length``，默认 200）
        2. 多领域关键词共现（2+ 组命中）
        3. 多步骤信号（首先+然后、第一步、多个文件等）
        4. 历史轮次超阈值（``settings.coding_complexity_history_count``，默认 10）
        """
        settings = get_settings()
        length_threshold: int = getattr(
            settings, "coding_complexity_message_length", 200
        )
        history_threshold: int = getattr(
            settings, "coding_complexity_history_count", 10
        )

        hit_signals: list[str] = []
        suggested_subagents: list[str] = []

        # 信号 1：消息长度超阈值
        if len(message) > length_threshold:
            hit_signals.append("消息过长")

        # 信号 2：多领域关键词共现（2+ 组命中）
        message_lower = message.lower()
        hit_groups: list[str] = []
        for group_key, keywords in _DOMAIN_KEYWORD_GROUPS.items():
            for kw in keywords:
                if kw.lower() in message_lower:
                    hit_groups.append(group_key)
                    break
        if len(hit_groups) >= 2:
            hit_signals.append("多领域关键词共现")
            # 映射到角色 key（重构组无映射，省略）
            for group_key in hit_groups:
                role = _GROUP_TO_ROLE.get(group_key)
                if role is not None and role not in suggested_subagents:
                    suggested_subagents.append(role)

        # 信号 3：多步骤信号
        multi_step = False
        if "首先" in message and "然后" in message:
            multi_step = True
        if not multi_step:
            for token in ("第一步", "第1步", "step 1"):
                if token in message_lower:
                    multi_step = True
                    break
        if not multi_step:
            for token in ("多个文件", "分步骤", "逐步"):
                if token in message:
                    multi_step = True
                    break
        if multi_step:
            hit_signals.append("多步骤信号")

        # 信号 4：历史轮次超阈值
        if history_count > history_threshold:
            hit_signals.append("历史轮次过多")

        if hit_signals:
            reason = "命中：" + "、".join(hit_signals)
            # 截断到 50 字（中文）
            if len(reason) > 50:
                reason = reason[:50]
            return ComplexityResult(
                is_complex=True,
                reason=reason,
                suggested_subagents=suggested_subagents,
            )

        return ComplexityResult(
            is_complex=False,
            reason="无复杂信号",
            suggested_subagents=[],
        )
