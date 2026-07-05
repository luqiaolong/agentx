"""Web 子代理：联网搜索。

工具集: web_search（Tavily Search API）
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from app.config import get_settings
from app.llm import get_chat_model

# Tavily API Key 环境变量名（config.py 未声明该字段，从 env 读取）
_TAVILY_KEY_ENV = "AGENTX_TAVILY_API_KEY"
# 缺 key 时的统一错误提示
_NO_KEY_MSG = "web_search 不可用：未配置 AGENTX_TAVILY_API_KEY"


def _get_tavily_key() -> str | None:
    """读取 Tavily API Key，缺失返回 None。"""
    return os.environ.get(_TAVILY_KEY_ENV)


def _format_tavily(result: dict) -> str:
    """将 Tavily search 返回值格式化为带来源的字符串。"""
    lines: list[str] = []
    answer = result.get("answer")
    if answer:
        lines.append(f"摘要: {answer}")
    for i, item in enumerate(result.get("results", []), start=1):
        title = item.get("title", "")
        url = item.get("url", "")
        content = item.get("content", "")
        lines.append(f"[{i}] {title}\n  链接: {url}\n  内容: {content}")
    if not lines:
        return "未检索到相关网页。"
    return "\n\n".join(lines)


def _make_web_tools(thread_id: str) -> list:
    """构建 Web 搜索工具列表（``thread_id`` 保留以与其他子代理签名对齐）。

    工具启用由 ``get_settings().tools_enabled`` 过滤（key: ``web_search``）。
    """

    @tool
    async def web_search(query: str, max_results: int = 5) -> str:
        """联网搜索，返回带标题、链接与摘要的结果。"""
        key = _get_tavily_key()
        if not key:
            return _NO_KEY_MSG
        try:
            from tavily import TavilyClient

            client = TavilyClient(api_key=key)
            # TavilyClient.search 是同步阻塞调用，放线程池避免阻塞事件循环
            result = await asyncio.to_thread(
                client.search, query, max_results=max_results
            )
        except Exception as exc:  # noqa: BLE001 — 工具层兜底，错误以字符串回流
            return f"web_search 失败: {exc}"
        return _format_tavily(result)

    tools = [web_search]
    enabled = get_settings().tools_enabled
    return [t for t in tools if enabled.get(t.name, True)]


# 子代理思考过程提示：要求模型在思考时包裹 think 标签，供前端展示 reasoning block
_THINK_PROMPT_SUFFIX = (
    "\n\n在调用工具前，请先用 " + chr(60) + "think" + chr(62) + ".." + chr(60) + "/think" + chr(62) + " 标签包裹你的思考过程，"
    "例如：" + chr(60) + "think" + chr(62) + "我需要搜索相关信息来回答这个问题" + chr(60) + "/think" + chr(62) + "。"
    "这样用户可以看到你的推理过程。"
)

def build_web_agent(thread_id: str) -> Any:
    """构建 Web 子代理 ReAct 子图，返回 CompiledStateGraph。"""
    settings = get_settings()
    cfg = settings.subagents["web"]
    model = get_chat_model(temperature=cfg.temperature, streaming=True)
    tools = _make_web_tools(thread_id)
    kwargs: dict[str, Any] = {}
    # 合并用户配置的 system_prompt 与 think 标签指令
    prompt = cfg.system_prompt or ""
    prompt = prompt + _THINK_PROMPT_SUFFIX
    kwargs["prompt"] = prompt
    return create_react_agent(model, tools, name="web_agent", **kwargs)


async def run_web_agent(
    thread_id: str,
    message: str,
    history: list | None = None,
) -> AsyncIterator[dict]:
    """运行 Web 子代理，yield 标准化事件流。

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
    agent = build_web_agent(thread_id)
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
                "source": "web",
            }
        elif kind == "on_tool_end":
            yield {
                "type": "tool_result",
                "id": run_id,
                "name": name,
                "result": data.get("output"),
                "source": "web",
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


__all__ = ["build_web_agent", "run_web_agent", "_make_web_tools"]
