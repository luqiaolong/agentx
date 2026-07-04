"""消息分类器：规则前置过滤 + LLM 分类。

策略（与 project_memory 一致）：
1. 规则过滤（零延迟）：命令 / 短消息 / 明确关键词 → 直接分类
2. LLM 分类：规则未命中时调用 LLM，temperature=0.0
3. 降级：LLM 不可用时返回 "CHAT"（安全默认）
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.llm import get_chat_model
from app.observability.langsmith import trace_span
from app.observability.logger import logger

# 工具关键词：命中即 → SINGLE_TOOL
_SINGLE_TOOL_KEYWORDS: tuple[str, ...] = (
    "读文件",
    "写文件",
    "搜索",
    "查找文件",
    "列出目录",
    "执行命令",
)

# 深度任务关键词：命中即 → DEEP_TASK
# 其中"帮我做"/"帮我写"语义较弱，需要消息长度 > 20 才命中，避免短消息被误分类
_DEEP_TASK_KEYWORDS: tuple[str, ...] = (
    "分析",
    "规划",
    "设计",
    "实现",
    "重构",
    "帮我做",
    "帮我写",
)

# 弱信号关键词：需要消息长度 > 20 才命中
_DEEP_TASK_WEAK_KEYWORDS: tuple[str, ...] = ("帮我做", "帮我写")

# 合法标签集合（用于 LLM 输出标准化校验）
_VALID_LABELS: frozenset[str] = frozenset({"CHAT", "SINGLE_TOOL", "DEEP_TASK"})

# LLM 分类 system prompt
_CLASSIFIER_SYSTEM_PROMPT = (
    "你是消息分类器。将用户消息分为三类之一：\n"
    "- CHAT: 闲聊、问候、简单问答\n"
    "- SINGLE_TOOL: 需要调用单个工具（读文件、搜索等）\n"
    "- DEEP_TASK: 需要多步规划+工具调用的复杂任务\n\n"
    "只输出分类标签，不要解释。"
)


def _rule_classify(message: str) -> str | None:
    """规则前置过滤：命中返回标签，未命中返回 None。

    规则顺序（先命中先返回）：
    1. ``/`` 开头 → CHAT（命令类，main.py 特殊处理 /reset）
    2. 长度 < 10 且不含问号 → CHAT（短问候/确认）
    3. 含工具关键词 → SINGLE_TOOL
    4. 含深度任务关键词 → DEEP_TASK
       - 强信号关键词（分析/规划/设计/实现/重构）：不限长度
       - 弱信号关键词（帮我做/帮我写）：需长度 > 20
    """
    # 1. 命令类（/reset /help 等）
    if message.startswith("/"):
        return "CHAT"

    # 2. 短消息且不含问号 → 闲聊（半角/全角问号均排除）
    if len(message) < 10 and "?" not in message and "？" not in message:
        return "CHAT"

    # 3. 工具关键词 → SINGLE_TOOL
    for kw in _SINGLE_TOOL_KEYWORDS:
        if kw in message:
            return "SINGLE_TOOL"

    # 4a. 强信号深度关键词（不限长度）→ DEEP_TASK
    for kw in _DEEP_TASK_KEYWORDS:
        if kw in _DEEP_TASK_WEAK_KEYWORDS:
            continue
        if kw in message:
            return "DEEP_TASK"

    # 4b. 弱信号深度关键词（需长度 > 20）→ DEEP_TASK
    if len(message) > 20:
        for kw in _DEEP_TASK_WEAK_KEYWORDS:
            if kw in message:
                return "DEEP_TASK"

    return None


async def _llm_classify(message: str) -> str:
    """LLM 分类：调用 ChatModel，失败时返回 "CHAT"。

    Args:
        message: 原始用户消息。

    Returns:
        "CHAT" / "SINGLE_TOOL" / "DEEP_TASK"；LLM 不可用或输出无法解析时返回 "CHAT"。
    """
    # 1. 构造 LLM：无 API key 等配置错误 → 降级 CHAT
    try:
        llm = get_chat_model(temperature=0.0, streaming=False)
    except ValueError as exc:
        logger.warning("LLM 不可用，分类降级为 CHAT", error=str(exc))
        return "CHAT"

    # 2. 调用 LLM：网络/解析异常 → 降级 CHAT
    try:
        messages = [
            SystemMessage(content=_CLASSIFIER_SYSTEM_PROMPT),
            HumanMessage(content=message),
        ]
        result = await llm.ainvoke(messages)
    except Exception as exc:  # noqa: BLE001 — LLM 调用兜底，任何异常都降级
        logger.warning("LLM 分类调用失败，降级为 CHAT", error=str(exc))
        return "CHAT"

    # 3. 解析输出：取首行非空文本，转大写，校验是否在合法标签集合
    content = getattr(result, "content", "") or ""
    # 推理模型会带 <think>...</think>，剥离后再取标签
    from app.utils.text import strip_think

    stripped = strip_think(content) if isinstance(content, str) else ""
    if not stripped:
        logger.warning("LLM 分类输出为空，降级为 CHAT")
        return "CHAT"
    label = stripped.splitlines()[0].strip().upper()
    if label not in _VALID_LABELS:
        logger.warning("LLM 分类输出无法解析，降级为 CHAT", raw=stripped[:200])
        return "CHAT"
    return label


async def classify_message(message: str) -> str:
    """主入口：规则前置过滤 + LLM 分类。

    Args:
        message: 用户原始消息。

    Returns:
        "CHAT" / "SINGLE_TOOL" / "DEEP_TASK"。
    """
    with trace_span("router.classify", message_len=len(message)):
        # 1. 规则过滤（零延迟）
        rule_label = _rule_classify(message)
        if rule_label is not None:
            logger.debug(
                "分类命中规则",
                label=rule_label,
                message_len=len(message),
            )
            return rule_label

        # 2. LLM 分类
        label = await _llm_classify(message)
        logger.info("LLM 分类完成", label=label, message_len=len(message))
        return label


__all__ = [
    "classify_message",
    "_rule_classify",
    "_llm_classify",
    "_SINGLE_TOOL_KEYWORDS",
    "_DEEP_TASK_KEYWORDS",
]
