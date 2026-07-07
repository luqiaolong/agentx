"""RAG 子代理：检索知识库并回答。

工具集: rag_retrieve（向量检索）
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from langgraph.prebuilt import create_react_agent

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
    """构建 RAG 子代理 ReAct 子图，返回 CompiledStateGraph。

    ``checkpointer`` 可选的 LangGraph checkpointer，用于状态持久化。
    """
    settings = get_settings()
    cfg = settings.subagents["rag"]
    model = get_chat_model(temperature=cfg.temperature, streaming=True)
    tools = _make_rag_tools(thread_id)
    kwargs: dict[str, Any] = {}
    # 合并用户配置的角色定义与 think 标签指令
    prompt = cfg.system_prompt or ""
    prompt = prompt + THINK_PROMPT_SUFFIX
    kwargs["prompt"] = prompt
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    return create_react_agent(model, tools, name="rag_agent", **kwargs)


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
    async for event in run_react_agent_stream(agent, inputs, source="rag"):
        yield event


__all__ = ["build_rag_agent", "run_rag_agent", "_make_rag_tools"]
