"""RAG 子代理：检索知识库并回答。

工具集: rag_retrieve（向量检索）
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from app.config import get_settings
from app.llm import get_chat_model


def _make_rag_tools(thread_id: str) -> list:
    """构建绑定 ``thread_id`` 的 RAG 检索工具列表。

    ``rag_retrieve`` 的 ``thread_id`` 用于 trace，不暴露给 LLM。

    工具启用由 ``get_settings().tools_enabled`` 过滤（key: ``rag_retrieve``）。
    """
    from app.tools.rag_retrieve import rag_retrieve as _rag_retrieve

    @tool
    async def rag_retrieve(query: str, top_k: int = 5) -> str:
        """检索知识库，返回带来源与相似度的上下文。"""
        return await _rag_retrieve(query, thread_id=thread_id, top_k=top_k)

    tools = [rag_retrieve]
    enabled = get_settings().tools_enabled
    return [t for t in tools if enabled.get(t.name, True)]


# 子代理思考过程提示：要求模型在思考时包裹 think 标签，供前端展示 reasoning block
_THINK_PROMPT_SUFFIX = (
    "\n\n在调用工具前，请先用 " + chr(60) + "think" + chr(62) + ".." + chr(60) + "/think" + chr(62) + " 标签包裹你的思考过程，"
    "例如：" + chr(60) + "think" + chr(62) + "我需要检索相关文档来回答这个问题" + chr(60) + "/think" + chr(62) + "。"
    "这样用户可以看到你的推理过程。"
)

def build_rag_agent(thread_id: str) -> Any:
    """构建 RAG 子代理 ReAct 子图，返回 CompiledStateGraph。"""
    settings = get_settings()
    cfg = settings.subagents["rag"]
    model = get_chat_model(temperature=cfg.temperature, streaming=True)
    tools = _make_rag_tools(thread_id)
    kwargs: dict[str, Any] = {}
    # 合并用户配置的角色定义与 think 标签指令
    prompt = cfg.system_prompt or ""
    prompt = prompt + _THINK_PROMPT_SUFFIX
    kwargs["prompt"] = prompt
    return create_react_agent(model, tools, name="rag_agent", **kwargs)


async def run_rag_agent(
    thread_id: str,
    message: str,
    history: list | None = None,
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
    """
    agent = build_rag_agent(thread_id)
    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": message}]}
    async for event in agent.astream_events(inputs, version="v2"):
        kind = event["event"]
        name = event.get("name", "")
        data = event.get("data", {}) or {}
        run_id = event.get("run_id", "")
        if kind == "on_chat_model_stream":
            content = _extract_text(data.get("chunk"))
            if content:
                yield {"type": "token", "content": content}
        elif kind == "on_tool_start":
            yield {
                "type": "tool_call",
                "id": run_id,
                "name": name,
                "args": data.get("input"),
                "source": "rag",
            }
        elif kind == "on_tool_end":
            yield {
                "type": "tool_result",
                "id": run_id,
                "name": name,
                "result": data.get("output"),
                "source": "rag",
            }


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
