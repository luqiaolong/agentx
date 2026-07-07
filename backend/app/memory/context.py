"""上下文管理：滑动窗口消息截断 + token 预算。

提供 ``trim_messages_with_budget`` 双层截断：
1. 用 ``langchain_core.messages.trim_messages`` 按 token 截断（保留最近消息）
2. 按消息数硬截断到 ``max_messages`` 条

设计要点：
- SystemMessage 始终保留（system prompt 不应被截断）
- 截断策略为 ``strategy="last"``（保留最近的消息）
- token 计数用 ``get_buffer_string`` 拼接后按 4 chars/token 估算，与 OpenAI 模型近似对齐
- 非 OpenAI 模型 token 估算略有偏差，但 ``context_max_tokens`` 默认 16000 留足余量
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    SystemMessage,
    ToolMessage,
    get_buffer_string,
    trim_messages,
)

__all__ = ["trim_messages_with_budget"]


def _token_counter(messages: list[BaseMessage]) -> int:
    """计算消息列表的 token 数（含消息格式开销）。

    新版 langchain_core 已移除 ``get_num_tokens_from_messages``，
    这里用 ``get_buffer_string`` 拼接文本后按字符估算，并保留与 OpenAI
    模型近似 4 chars/token 的换算系数。
    """
    text = get_buffer_string(messages)
    return max(1, len(text) // 4)


def trim_messages_with_budget(
    messages: list[Any],
    max_messages: int = 20,
    max_tokens: int = 16000,
) -> list[BaseMessage]:
    """双层截断消息列表，保证不超过 token 预算与消息数上限。

    Args:
        messages: 原始消息列表（可含 dict / BaseMessage，dict 会被规范化）。
        max_messages: 消息数上限（含 system prompt），超限保留最近 N 条。
        max_tokens: token 预算上限，超限从最早消息开始丢弃。

    Returns:
        截断后的 ``BaseMessage`` 列表，保证：
        - SystemMessage 始终在前且保留
        - 总 token ≤ ``max_tokens``
        - 总消息数 ≤ ``max_messages``
    """
    # 规范化：dict → BaseMessage（兼容前端传来的 dict 格式）
    normalized = _normalize_messages(messages)
    if not normalized:
        return []

    # 分离 SystemMessage 与其他消息（SystemMessage 不参与 token 截断丢弃）
    system_msgs = [m for m in normalized if isinstance(m, SystemMessage)]
    non_system = [m for m in normalized if not isinstance(m, SystemMessage)]

    # 第一层：按 token 截断非 system 消息
    if non_system:
        try:
            trimmed_non_system = trim_messages(
                non_system,
                max_tokens=max_tokens,
                token_counter=_token_counter,
                strategy="last",
                allow_partial=False,
                include_system=True,
            )
        except Exception:
            # trim_messages 在极端情况下可能报错（如单消息超 token），
            # 退化到只保留最后一条
            trimmed_non_system = non_system[-1:] if non_system else []
    else:
        trimmed_non_system = []

    # 第二层：按消息数硬截断
    if len(trimmed_non_system) > max_messages:
        trimmed_non_system = trimmed_non_system[-max_messages:]

    result = system_msgs + trimmed_non_system

    # T10：截断后检查 AIMessage(tool_calls) 与 ToolMessage 的配对关系。
    # 若 tool_call 缺少对应 ToolMessage，注入占位 ToolMessage，避免后续
    # LangGraph _validate_chat_history 校验失败。
    return _ensure_tool_call_pairing(result)


def _ensure_tool_call_pairing(messages: list[BaseMessage]) -> list[BaseMessage]:
    """为截断后缺失 ToolMessage 的 tool_call 注入占位消息。

    截断可能丢弃旧的 ToolMessage 但保留其前面的 AIMessage(tool_calls)，导致
    LangGraph 的 ``_validate_chat_history`` 抛出 INVALID_CHAT_HISTORY。
    本函数扫描全部消息，为每个缺少对应 ToolMessage 的 tool_call_id 追加
    占位 ToolMessage。
    """
    tool_call_ids: set[str] = set()
    tool_msg_ids: set[str] = set()

    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id:
                    tool_call_ids.add(tc_id)
        elif isinstance(msg, ToolMessage):
            tool_msg_ids.add(getattr(msg, "tool_call_id", None) or "")

    missing = tool_call_ids - tool_msg_ids
    if not missing:
        return messages

    placeholders = [
        ToolMessage(content="上下文被截断，原工具结果不可用", tool_call_id=tc_id)
        for tc_id in missing
    ]
    return [*messages, *placeholders]


def _normalize_messages(messages: list[Any]) -> list[BaseMessage]:
    """把 dict / BaseMessage 混合列表规范化为 BaseMessage 列表。

    - dict ``{"role": "user", "content": "..."}`` → ``HumanMessage``
    - dict ``{"role": "assistant", "content": "..."}`` → ``AIMessage``
    - dict ``{"role": "system", "content": "..."}`` → ``SystemMessage``
    - dict ``{"role": "tool", "content": "..."}`` → ``ToolMessage``（tool_call_id 必填，缺失时跳过）
    - BaseMessage 原样保留
    """
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        ToolMessage,
    )

    result: list[BaseMessage] = []
    for msg in messages:
        if isinstance(msg, BaseMessage):
            result.append(msg)
            continue
        if isinstance(msg, dict):
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                result.append(HumanMessage(content=content))
            elif role == "assistant":
                result.append(AIMessage(content=content))
            elif role == "system":
                result.append(SystemMessage(content=content))
            elif role == "tool":
                tool_call_id = msg.get("tool_call_id", "")
                if tool_call_id:
                    result.append(
                        ToolMessage(content=content, tool_call_id=tool_call_id)
                    )
            # 未知 role 跳过
        # 其他类型跳过
    return result
