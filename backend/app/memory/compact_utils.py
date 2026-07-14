"""Compact 消息切分辅助。

``/api/chat/compact`` 端点与 CLI ``/compact`` 命令共用 ``split_messages_for_compact``
切分逻辑，确保 ToolMessage / AIMessage 配对不被压缩破坏。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

__all__ = ["split_messages_for_compact"]


def split_messages_for_compact(messages: list) -> tuple[list, list]:
    """将 messages 切分为 ``(to_compress, keep_recent)``，附带 ToolMessage 配对保护。

    ``keep_recent`` 默认取最后 2 条；若 ``keep_recent`` 的首条是 ``ToolMessage``，
    则从 ``to_compress`` 中向前查找发起对应 ``tool_call`` 的 ``AIMessage``，
    并把它一并移入 ``keep_recent``，避免压缩后留下孤立的 ``ToolMessage``
    （LangGraph 还原状态时要求 ``ToolMessage`` 紧跟发起 ``tool_call`` 的 ``AIMessage``）。
    """
    keep_recent = messages[-2:]
    to_compress = messages[:-2]
    if keep_recent and isinstance(keep_recent[0], ToolMessage):
        tool_call_id = keep_recent[0].tool_call_id
        # 向前找对应的 AIMessage（含匹配的 tool_call id）
        for i in range(len(to_compress) - 1, -1, -1):
            msg = to_compress[i]
            if isinstance(msg, AIMessage) and msg.tool_calls:
                if any(tc.get("id") == tool_call_id for tc in msg.tool_calls):
                    keep_recent = [msg] + keep_recent
                    to_compress = to_compress[:i] + to_compress[i + 1 :]
                    break
    return to_compress, keep_recent
