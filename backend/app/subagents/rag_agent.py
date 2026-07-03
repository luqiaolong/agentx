"""RAG 子代理：检索知识库并回答。

工具集: rag_retrieve（向量检索）
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from app.llm import get_chat_model


def _make_rag_tools(thread_id: str) -> list:
    """构建绑定 ``thread_id`` 的 RAG 检索工具列表。

    ``rag_retrieve`` 的 ``thread_id`` 用于 trace，不暴露给 LLM。
    """
    from app.tools.rag_retrieve import rag_retrieve as _rag_retrieve

    @tool
    async def rag_retrieve(query: str, top_k: int = 5) -> str:
        """检索知识库，返回带来源与相似度的上下文。"""
        return await _rag_retrieve(query, thread_id=thread_id, top_k=top_k)

    return [rag_retrieve]


def build_rag_agent(thread_id: str) -> Any:
    """构建 RAG 子代理 ReAct 子图，返回 CompiledStateGraph。"""
    model = get_chat_model(temperature=0.2, streaming=True)
    tools = _make_rag_tools(thread_id)
    return create_react_agent(model, tools, name="rag_agent")


async def run_rag_agent(thread_id: str, message: str) -> AsyncIterator[dict]:
    """运行 RAG 子代理，yield 标准化事件流。

    事件类型:
    - ``{"type": "token", "content": str}``: 模型流式输出 token
    - ``{"type": "tool_call", "name": str, "args": dict}``: 工具调用开始
    - ``{"type": "tool_result", "name": str, "result": Any}``: 工具调用结束
    """
    agent = build_rag_agent(thread_id)
    inputs = {"messages": [{"role": "user", "content": message}]}
    async for event in agent.astream_events(inputs, version="v2"):
        kind = event["event"]
        name = event.get("name", "")
        data = event.get("data", {}) or {}
        if kind == "on_chat_model_stream":
            content = _extract_text(data.get("chunk"))
            if content:
                yield {"type": "token", "content": content}
        elif kind == "on_tool_start":
            yield {"type": "tool_call", "name": name, "args": data.get("input")}
        elif kind == "on_tool_end":
            yield {"type": "tool_result", "name": name, "result": data.get("output")}


def _extract_text(chunk: Any) -> str:
    """从流式 chunk 中提取纯文本内容（兼容 str / list 内容块）。"""
    if chunk is None:
        return ""
    content = getattr(chunk, "content", chunk)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


__all__ = ["build_rag_agent", "run_rag_agent", "_make_rag_tools"]
