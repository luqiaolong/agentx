"""上下文截断后 tool_calls / ToolMessage 配对测试。

覆盖：
1. trim_messages_with_budget 截断后，为缺失 ToolMessage 的 tool_call 注入占位消息
2. 已配对的 tool_call / ToolMessage 不会被重复注入
3. 无 tool_calls 的消息列表保持不变
"""

from __future__ import annotations

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.memory.context import trim_messages_with_budget


def test_trim_injects_placeholder_for_unpaired_tool_call() -> None:
    """截断后保留 AIMessage(tool_calls) 但缺失对应 ToolMessage，应注入占位。"""
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="user"),
        AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "read_file", "args": {"path": "/tmp/a.txt"}}],
        ),
        ToolMessage(content="result", tool_call_id="tc-1"),
        AIMessage(
            content="",
            tool_calls=[{"id": "tc-2", "name": "read_file", "args": {"path": "/tmp/b.txt"}}],
        ),
    ]

    # 非 system 消息为 [Human, AI1, Tool1, AI2]，保留最近 3 条得到 [Tool1, AI2]。
    # AI2 缺少对应 ToolMessage，需要注入占位。
    result = trim_messages_with_budget(messages, max_messages=3, max_tokens=16000)

    assert isinstance(result[0], SystemMessage)
    assert any(
        isinstance(m, ToolMessage) and m.tool_call_id == "tc-2" and "截断" in m.content
        for m in result
    )


def test_trim_injects_placeholder_when_tool_message_dropped() -> None:
    """截断后保留 AIMessage(tool_calls) 但丢弃对应 ToolMessage，应注入占位。"""
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="user"),
        AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "read_file", "args": {"path": "/tmp/a.txt"}}],
        ),
    ]

    result = trim_messages_with_budget(messages, max_messages=10, max_tokens=16000)

    assert isinstance(result[0], SystemMessage)
    assert isinstance(result[1], HumanMessage)
    assert isinstance(result[2], AIMessage)
    assert isinstance(result[3], ToolMessage)
    assert result[3].tool_call_id == "tc-1"
    assert "截断" in result[3].content


def test_paired_tool_call_unchanged() -> None:
    """已配对的 AIMessage / ToolMessage 不会被注入额外占位。"""
    messages = [
        SystemMessage(content="system"),
        AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "read_file", "args": {"path": "/tmp/a.txt"}}],
        ),
        ToolMessage(content="result", tool_call_id="tc-1"),
    ]

    result = trim_messages_with_budget(messages, max_messages=10, max_tokens=16000)

    assert len(result) == 3
    assert isinstance(result[0], SystemMessage)
    assert isinstance(result[1], AIMessage)
    assert isinstance(result[2], ToolMessage)


def test_no_tool_calls_no_injection() -> None:
    """无 tool_calls 的消息列表不注入占位。"""
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
    ]

    result = trim_messages_with_budget(messages, max_messages=10, max_tokens=16000)

    assert len(result) == 3
    assert all(not isinstance(m, ToolMessage) for m in result)


def test_multiple_unpaired_tool_calls_get_placeholders() -> None:
    """多个未配对 tool_call 各自注入占位。"""
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"id": "tc-1", "name": "read_file", "args": {}},
                {"id": "tc-2", "name": "read_file", "args": {}},
            ],
        ),
    ]

    result = trim_messages_with_budget(messages, max_messages=10, max_tokens=16000)

    assert len(result) == 3
    placeholders = [m for m in result if isinstance(m, ToolMessage)]
    assert len(placeholders) == 2
    assert {m.tool_call_id for m in placeholders} == {"tc-1", "tc-2"}
