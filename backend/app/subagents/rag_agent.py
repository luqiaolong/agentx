"""RAG 子代理：检索知识库并回答。

工具集: rag_retrieve（向量检索）
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from app.config import get_settings
from app.llm import get_chat_model
from app.subagents.base import (
    THINK_PROMPT_SUFFIX,
    _make_rag_tools,
    run_react_agent_stream,
)


def build_rag_agent(
    thread_id: str,
    checkpointer: Any = None,
) -> Any:
    """构建 RAG 子代理 deep_agent 子图，返回 CompiledStateGraph。

    使用 ``harness.create_agent``（即 ``deepagents.create_deep_agent``）构建，
    自动获得 ``SummarizationMiddleware`` / ``PatchToolCallsMiddleware`` / ``write_todos``
    等中间件能力。子代理无危险工具，``interrupt_on`` 不触发中断。

    ``checkpointer`` 可选的 LangGraph checkpointer，用于状态持久化。
    """
    from app.deep.harness import create_agent

    settings = get_settings()
    cfg = settings.subagents["rag"]
    model = get_chat_model(temperature=cfg.temperature, streaming=True)
    tools = _make_rag_tools(thread_id)
    # 合并用户配置的角色定义与 think 标签指令
    prompt = cfg.system_prompt or ""
    prompt = prompt + THINK_PROMPT_SUFFIX
    return create_agent(
        model,
        tools,
        system_prompt=prompt,
        checkpointer=checkpointer,
        name="rag_agent",
    )


async def run_rag_agent(
    thread_id: str,
    message: str,
    history: list | None = None,
    checkpointer: Any = None,
) -> AsyncIterator[dict]:
    """运行 RAG 子代理，yield 标准化事件流。

    事件类型:
    - ``{"type": "token", "content": str}``: 模型流式输出 token
    - ``{"type": "tool_call", "id": str, "name": str, "args": dict}``: 工具调用开始
    - ``{"type": "tool_result", "id": str, "name": str, "result": Any}``: 工具调用结束

    ``id`` 来自 astream_events v2 的 ``run_id``，同一 tool run 的 start/end 共享，
    供前端按 id 配对（chat-rendering-trace-v2 D5）。

    Args:
        thread_id: 会话 ID。
        message: 当前用户消息。
        history: 历史 messages 列表（已截断），拼到 inputs 前。
        checkpointer: 可选的 LangGraph checkpointer，用于状态持久化。
    """
    agent = build_rag_agent(thread_id, checkpointer=checkpointer)
    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": message}]}
    config = {"configurable": {"thread_id": thread_id}}
    async for event in run_react_agent_stream(agent, inputs, source="rag", config=config):
        yield event


__all__ = ["build_rag_agent", "run_rag_agent", "_make_rag_tools"]
