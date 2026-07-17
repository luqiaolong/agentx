"""子代理工具工厂：rag / web 工具集构建。

从 ``app.deepagent.subagents.base`` 下沉到 ``app.tools`` 层，消除 ``deepagent.tool_assembly``
↔ ``deepagent.subagents.base`` 的循环依赖：

- 旧路径：``deepagent.tool_assembly`` → ``deepagent.subagents.base._make_rag_tools`` （反向耦合）
- 新路径：``deepagent.tool_assembly`` → ``app.tools.subagent_tools`` ← ``deepagent.subagents.base``

内置 fs 工具（ls/read_file/write_file/edit_file/glob/grep）由
``AuthorizedLocalShellBackend`` 自动注入，不在本模块工具列表中。
"""

from __future__ import annotations

import asyncio
import os

from langchain_core.tools import tool

from app.config import get_settings

__all__ = ["make_rag_tools", "make_web_tools"]


def make_rag_tools(thread_id: str) -> list:
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


def make_web_tools(thread_id: str) -> list:
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
            result = await asyncio.to_thread(client.search, query, max_results=max_results)
        except Exception as exc:  # noqa: BLE001 — 工具层兜底，错误以字符串回流
            return f"web_search 失败: {exc}"
        return _format_tavily(result)

    tools = [web_search]
    enabled = get_settings().tools_enabled
    return [t for t in tools if enabled.get(t.name, True)]
