"""消息摘要压缩：``/compact`` 命令后端。

调 LLM 把消息列表压缩成一段摘要，用于替换 checkpoint 中的早期消息，
释放上下文预算。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from app.llm import get_chat_model
from app.observability.logger import logger

__all__ = ["summarize_messages"]


async def summarize_messages(messages: list[Any]) -> str:
    """调 LLM 把消息列表压缩成一段摘要。

    Args:
        messages: 待压缩的消息列表（可含 dict / BaseMessage）。

    Returns:
        摘要文本（纯字符串，不超过 500 字）。LLM 失败时抛异常，调用方负责兜底。
    """
    # 把 messages 序列化为可读文本
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, BaseMessage):
            role = msg.type.upper()  # human / ai / system / tool
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
        elif isinstance(msg, dict):
            role = msg.get("role", "unknown").upper()
            content = str(msg.get("content", ""))
        else:
            continue
        lines.append(f"[{role}] {content}")

    messages_text = "\n".join(lines)

    prompt = (
        "请将以下对话历史压缩成一段简洁的摘要，保留关键信息"
        "（用户意图、已完成的操作、重要结论）：\n"
        f"{messages_text}\n\n"
        "输出格式：纯文本摘要，不超过 500 字。"
    )

    llm = get_chat_model(temperature=0.0)
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    text = response.content if hasattr(response, "content") else str(response)
    if isinstance(text, list):
        # 兼容 list 内容块
        text = "".join(
            block if isinstance(block, str)
            else block.get("text", "") if isinstance(block, dict)
            else ""
            for block in text
        )
    logger.info("summarize_messages done", input_count=len(messages), output_len=len(text))
    return str(text)
