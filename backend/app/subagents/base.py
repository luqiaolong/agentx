"""子代理公共基类与工具函数。

提取 code/rag/web/custom agent 共享的：
- ``make_fs_tools`` / ``make_rag_tools`` / ``make_web_tools``：工具构造（公开名，去掉下划线前缀）
- ``extract_text``：流式 chunk 文本提取
- ``THINK_PROMPT_SUFFIX``：思考过程提示常量
- ``run_react_agent_stream``：标准化事件流转换

消除三个 agent 文件的重复代码，并提供给 ``deep/tools.py`` 跨包复用。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator

from langchain_core.tools import tool

from app.config import get_settings

__all__ = [
    "make_fs_tools",
    "make_rag_tools",
    "make_web_tools",
    "extract_text",
    "THINK_PROMPT_SUFFIX",
    "run_react_agent_stream",
]


# 子代理思考过程提示：要求模型在思考时包裹 think 标签，供前端展示 reasoning block
# 用 chr(60)/chr(62) 拼接 <> 避免在源码字符串中直接出现标签导致渲染层歧义
_THINK_OPEN = chr(60) + "think" + chr(62)
_THINK_CLOSE = chr(60) + "/think" + chr(62)
THINK_PROMPT_SUFFIX = (
    "\n\n在调用工具前，请先用 " + _THINK_OPEN + ".." + _THINK_CLOSE + " 标签包裹你的思考过程，"
    "例如：" + _THINK_OPEN + "我需要查看相关信息来回答这个问题" + _THINK_CLOSE + "。"
    "这样用户可以看到你的推理过程。"
)

# 保留旧名作为向后兼容别名（deep/tools.py 等模块历史 import _make_*_tools）
_make_fs_tools = None  # 占位，下方赋值
_make_rag_tools = None
_make_web_tools = None


def make_fs_tools(thread_id: str) -> list:
    """构建绑定 ``thread_id`` 的文件系统工具列表（仅只读工具）。

    filesystem 工具的签名含 ``thread_id``（用于沙箱授权校验），该参数不应暴露给
    LLM。这里通过闭包绑定 ``thread_id``，对外只声明业务参数。

    安全约束：subagent 不返回 write_file / edit_file，避免绕过 DeepAgent 审批流。

    工具启用由 ``get_settings().tools_enabled`` 过滤；工具内部名与配置 key 的
    映射：``glob_files``→``glob``、``grep_files``→``grep``。
    """
    from app.tools import filesystem as fs

    @tool
    async def read_file(path: str) -> str:
        """读取文本文件内容。"""
        return await fs.read_file(thread_id, path)

    @tool
    async def list_dir(path: str) -> list[str]:
        """列出目录下的条目名称（不含路径前缀）。"""
        return await fs.list_dir(thread_id, path)

    @tool
    async def glob_files(pattern: str) -> list[str]:
        """glob 匹配文件路径，pattern 形如 ``d:/docs/**/*.md``。"""
        return await fs.glob(thread_id, pattern)

    @tool
    async def grep_files(pattern: str, path: str) -> list[str]:
        """在 path 目录下递归搜索匹配 pattern（正则）的行。"""
        return await fs.grep(thread_id, pattern, path)

    tools = [read_file, list_dir, glob_files, grep_files]
    # 工具内部函数名 → tools_enabled 配置 key 的映射
    tool_name_map = {
        "read_file": "read_file",
        "list_dir": "list_dir",
        "glob_files": "glob",
        "grep_files": "grep",
    }
    enabled = get_settings().tools_enabled
    return [t for t in tools if enabled.get(tool_name_map.get(t.name, t.name), True)]


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
            result = await asyncio.to_thread(
                client.search, query, max_results=max_results
            )
        except Exception as exc:  # noqa: BLE001 — 工具层兜底，错误以字符串回流
            return f"web_search 失败: {exc}"
        return _format_tavily(result)

    tools = [web_search]
    enabled = get_settings().tools_enabled
    return [t for t in tools if enabled.get(t.name, True)]


def extract_text(chunk: Any) -> str:
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


async def run_react_agent_stream(
    agent: Any,
    inputs: dict,
    source: str,
) -> AsyncIterator[dict]:
    """运行 ReAct agent 并 yield 标准化事件流。

    事件类型:
    - ``{"type": "token", "content": str}``: 模型流式输出 token
    - ``{"type": "tool_call", "id": str, "name": str, "args": dict}``: 工具调用开始
    - ``{"type": "tool_result", "id": str, "name": str, "result": Any}``: 工具调用结束

    ``id`` 来自 astream_events v2 的 ``run_id``，同一 tool run 的 start/end 共享，
    供前端按 id 配对（chat-rendering-trace-v2 D5）。

    Args:
        agent: 已编译的 ReAct agent（CompiledStategraph）。
        inputs: agent 输入，形如 ``{"messages": [...]}``。
        source: 事件源标签（"code" / "rag" / "web" / 自定义 agent key）。
    """
    async for event in agent.astream_events(inputs, version="v2"):
        kind = event["event"]
        name = event.get("name", "")
        data = event.get("data", {}) or {}
        run_id = event.get("run_id", "")
        if kind == "on_chat_model_stream":
            content = extract_text(data.get("chunk"))
            if content:
                yield {"type": "token", "content": content}
        elif kind == "on_tool_start":
            yield {
                "type": "tool_call",
                "id": run_id,
                "name": name,
                "args": data.get("input"),
                "source": source,
            }
        elif kind == "on_tool_end":
            yield {
                "type": "tool_result",
                "id": run_id,
                "name": name,
                "result": data.get("output"),
                "source": source,
            }


# 向后兼容别名（历史 import 路径：from app.subagents.code_agent import _make_fs_tools）
_make_fs_tools = make_fs_tools
_make_rag_tools = make_rag_tools
_make_web_tools = make_web_tools
