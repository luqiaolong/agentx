"""DeepAgent 消息历史修复 / 错误注入。

从 ``app.deep.agent`` 拆出（Phase 2.3），保持公共 API 不变。

职责:
- ``_inject_tool_error_messages``：为 checkpoint 中未配对的 tool_calls 注入
  ``ToolMessage``，防止 LangGraph ``INVALID_CHAT_HISTORY`` 校验失败
- ``_sanitize_message_history``：对 ``astream(inputs)`` 的 inputs 做同样修复
- ``_collect_unpaired_tool_call_ids``：扫描消息列表，返回未配对的 tool_call id
- ``_to_serializable``：将 LangChain Message 等不可序列化对象转为可序列化类型

使用场景:
- ``interrupt_before=["tools"]`` 中断后异常退出（tool 失败、进程崩溃）→ checkpoint
  中 AIMessage 残留 tool_calls 但无对应 ToolMessage
- 用户清空历史（DELETE checkpoint）→ state 为空但 inputs["messages"] 仍含未配对
  tool_calls
- 跨轮次恢复时历史消息校验

导入方向：``agent.py`` → ``recovery.py``（单向，无循环）。
"""

from __future__ import annotations

from typing import Any

from app.observability.logger import logger

__all__ = [
    "_inject_tool_error_messages",
    "_sanitize_message_history",
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


def _sanitize_message_history(messages: list, error_text: str) -> list:
    """为消息列表中所有未配对的 tool_calls 补齐 ToolMessage。

    用于在 ``agent.astream(inputs, ...)`` 前净化 inputs：当 checkpoint
    被 DELETE 清空、或历史含中断残留的未配对 AIMessage 时，
    ``_inject_tool_error_messages``（作用于 checkpoint state）无法覆盖，
    需要在此直接对 inputs 做修复，确保 ``_validate_chat_history`` 通过。
    """
    from langchain_core.messages import ToolMessage

    missing_ids = _collect_unpaired_tool_call_ids(messages)
    if not missing_ids:
        return messages
    return [
        *messages,
        *(
            ToolMessage(content=error_text, tool_call_id=tc_id)
            for tc_id in missing_ids
        ),
    ]


async def _inject_tool_error_messages(agent: Any, config: dict, error_text: str) -> None:
    """为 checkpoint 中未配对的 tool_calls 注入 ToolMessage，防止 INVALID_CHAT_HISTORY。

    当 ``interrupt_before=["tools"]`` 中断后，如果恢复执行时抛异常（如 tool 失败、
    进程崩溃），checkpoint 中 AIMessage 有 ``tool_calls`` 但没有对应的 ``ToolMessage``。
    用户再次发消息时，LangGraph 校验历史消息会抛出 ``INVALID_CHAT_HISTORY``。

    本函数在异常退出前调用：扫描 state 中**全部** AIMessage 的 ``tool_calls``
    （不只为最后一条，因为中断后可能已有新 HumanMessage 追加到末尾），
    为每个缺失的 tool_call 构造 ``ToolMessage(content=error_text, tool_call_id=...)``，
    通过 ``aupdate_state`` 追加到 messages 列表，保持配对关系。

    Args:
        agent: 编译后的 CompiledStateGraph。
        config: 含 ``configurable.thread_id`` 的字典。
        error_text: ToolMessage 的 content，描述失败原因。
    """
    from langchain_core.messages import ToolMessage

    try:
        state = await agent.aget_state(config)
        if not state or not state.values:
            return
        messages = list(state.values.get("messages", []))
        if not messages:
            return

        missing_ids = _collect_unpaired_tool_call_ids(messages)
        if not missing_ids:
            return

        new_messages = [
            ToolMessage(content=error_text, tool_call_id=tc_id)
            for tc_id in missing_ids
        ]
        await agent.aupdate_state(config, {"messages": new_messages})
        logger.info(
            "injected_tool_error_messages",
            thread_id=config.get("configurable", {}).get("thread_id", ""),
            count=len(new_messages),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("inject_tool_error_messages failed", error=str(exc))
