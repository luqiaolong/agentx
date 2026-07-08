"""DeepAgent 消息历史修复工具。

deepagents 0.6+ 的 ``PatchToolCallsMiddleware`` 在中间件层自动修复悬空
tool_calls，无需自研 ``_inject_tool_error_messages`` /
``_sanitize_message_history``（已删除）。

保留:
- ``_collect_unpaired_tool_call_ids``：扫描消息列表，返回未配对的 tool_call id
  （``_inject_tool_error_for_call`` 在审批拒绝时仍使用）
- ``_to_serializable``：将 LangChain Message 等不可序列化对象转为可序列化类型
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "_collect_unpaired_tool_call_ids",
    "_to_serializable",
]


def _to_serializable(value: Any) -> Any:
    """将可能不可 JSON 序列化的值（如 LangChain Message 对象）转为可序列化类型。"""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_serializable(item) for item in value]
    if isinstance(value, dict):
        return {k: _to_serializable(v) for k, v in value.items()}
    # 对 LangChain BaseMessage 对象提取 content（常见不可序列化场景）
    if hasattr(value, "content"):
        return _to_serializable(value.content)
    # 兜底：转字符串
    return str(value)


def _collect_unpaired_tool_call_ids(messages: list) -> list[str]:
    """扫描消息列表，返回所有未配对 tool_call 的 id。

    遍历全部 AIMessage 的 ``tool_calls``，与已有 ``ToolMessage.tool_call_id``
    比对，返回缺失配对的 id 列表。用于在 checkpoint 异常残留或 inputs
    含历史未配对 tool_calls 时，补齐 ToolMessage 以通过 LangGraph 的
    ``_validate_chat_history`` 校验。

    MUST 扫描全部消息——只看最后一条会漏掉历史中更早的未配对 AIMessage
    （例如中断后 checkpoint 已写入新 HumanMessage，但前面的 AIMessage
    仍未配对）。
    """
    from langchain_core.messages import AIMessage, ToolMessage

    existing_ids = {
        getattr(m, "tool_call_id", None)
        for m in messages
        if isinstance(m, ToolMessage)
    }
    missing: list[str] = []
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in tool_calls:
            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            if tc_id and tc_id not in existing_ids:
                missing.append(tc_id)
    return missing
