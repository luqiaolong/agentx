"""自定义子代理工厂：按 key 动态构建 ReAct 子图。

设计：
- 工具池复用现有 ``_make_fs_tools`` / ``_make_rag_tools`` / ``_make_web_tools``，
  按配置的 ``tools`` 字段筛选并组装。
- **安全硬约束**：危险工具（write_file / edit_file / shell_exec）已在 config 层
  被 ``_sanitize_custom_tools`` 过滤，本模块再次防御性过滤，确保 subagent 不暴露
  任何写/编辑/shell 工具（参考 claude.md §10）。
- 自定义子代理复用路径 B 的事件契约（token / tool_call / tool_result）。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from app.config import FORBIDDEN_SUBAGENT_TOOLS, get_settings
from app.llm import get_chat_model
from app.observability.logger import logger

# 工具内部名 → 所属子代理工具集的映射 key（与 _make_*_tools 函数返回的工具名一致）
# read_file / list_dir / glob_files / grep_files → fs 工具集
# rag_retrieve → rag 工具集
# web_search → web 工具集
_FS_TOOL_NAMES = {"read_file", "list_dir", "glob", "grep"}
_RAG_TOOL_NAMES = {"rag_retrieve"}
_WEB_TOOL_NAMES = {"web_search"}


def _make_custom_tools(thread_id: str, tool_names: list[str]) -> list:
    """按 tool_names 组装工具列表（复用现有工具实现）。

    安全：再次过滤危险工具（防御性），即便 config 层漏过也保底。
    """
    # 防御性过滤：移除危险工具与未知工具
    safe_names = [
        t for t in tool_names
        if t not in FORBIDDEN_SUBAGENT_TOOLS
        and (t in _FS_TOOL_NAMES or t in _RAG_TOOL_NAMES or t in _WEB_TOOL_NAMES)
    ]

    from app.tools import filesystem as fs
    from app.tools.rag_retrieve import rag_retrieve as _rag_retrieve

    tools: list = []

    # FS 工具集（与 code_agent._make_fs_tools 一致）
    if "read_file" in safe_names:
        @tool
        async def read_file(path: str) -> str:
            """读取文本文件内容。"""
            return await fs.read_file(thread_id, path)
        tools.append(read_file)
    if "list_dir" in safe_names:
        @tool
        async def list_dir(path: str) -> list[str]:
            """列出目录下的条目名称（不含路径前缀）。"""
            return await fs.list_dir(thread_id, path)
        tools.append(list_dir)
    if "glob" in safe_names:
        @tool
        async def glob_files(pattern: str) -> list[str]:
            """glob 匹配文件路径，pattern 形如 ``d:/docs/**/*.md``。"""
            return await fs.glob(thread_id, pattern)
        tools.append(glob_files)
    if "grep" in safe_names:
        @tool
        async def grep_files(pattern: str, path: str) -> list[str]:
            """在 path 目录下递归搜索匹配 pattern（正则）的行。"""
            return await fs.grep(thread_id, pattern, path)
        tools.append(grep_files)

    # RAG 工具集
    if "rag_retrieve" in safe_names:
        @tool
        async def rag_retrieve(query: str, top_k: int = 5) -> str:
            """检索知识库，返回带来源与相似度的上下文。"""
            return await _rag_retrieve(query, thread_id=thread_id, top_k=top_k)
        tools.append(rag_retrieve)

    # Web 工具集（与 web_agent._make_web_tools 一致）
    if "web_search" in safe_names:
        import asyncio
        import os

        _TAVILY_KEY_ENV = "AGENTX_TAVILY_API_KEY"
        _NO_KEY_MSG = "web_search 不可用：未配置 AGENTX_TAVILY_API_KEY"

        def _format_tavily(result: dict) -> str:
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

        @tool
        async def web_search(query: str, max_results: int = 5) -> str:
            """联网搜索，返回带标题、链接与摘要的结果。"""
            key = os.environ.get(_TAVILY_KEY_ENV)
            if not key:
                return _NO_KEY_MSG
            try:
                from tavily import TavilyClient

                client = TavilyClient(api_key=key)
                result = await asyncio.to_thread(
                    client.search, query, max_results=max_results
                )
            except Exception as exc:  # noqa: BLE001 — 工具层兜底
                return f"web_search 失败: {exc}"
            return _format_tavily(result)
        tools.append(web_search)

    # 工具启用由 settings.tools_enabled 过滤
    enabled = get_settings().tools_enabled
    # 内部函数名 → tools_enabled 配置 key 映射
    name_map = {
        "read_file": "read_file",
        "list_dir": "list_dir",
        "glob_files": "glob",
        "grep_files": "grep",
        "rag_retrieve": "rag_retrieve",
        "web_search": "web_search",
    }
    return [t for t in tools if enabled.get(name_map.get(t.name, t.name), True)]


# 子代理思考过程提示：要求模型在思考时包裹 think 标签，供前端展示 reasoning block
_THINK_PROMPT_SUFFIX = (
    "\n\n在调用工具前，请先用 " + chr(60) + "think" + chr(62) + ".." + chr(60) + "/think" + chr(62) + " 标签包裹你的思考过程，"
    "例如：" + chr(60) + "think" + chr(62) + "我需要调用工具来获取更多信息" + chr(60) + "/think" + chr(62) + "。"
    "这样用户可以看到你的推理过程。"
)

def build_custom_agent(key: str, thread_id: str) -> Any:
    """构建自定义子代理 ReAct 子图，返回 CompiledStateGraph。

    Args:
        key: 自定义子代理 key（在 ``Settings.custom_subagents`` 中存在）。
        thread_id: 会话 ID，用于沙箱授权校验。

    Raises:
        KeyError: key 不存在于 custom_subagents。
    """
    settings = get_settings()
    custom = settings.custom_subagents
    if key not in custom:
        raise KeyError(f"custom subagent not found: {key}")
    cfg = custom[key]
    if not cfg.tools:
        logger.warning(
            "custom subagent has no tools bound, agent will be unreachable",
            key=key,
        )
    model = get_chat_model(temperature=cfg.temperature, streaming=True)
    tools = _make_custom_tools(thread_id, cfg.tools)
    kwargs: dict[str, Any] = {"name": f"custom_{key}"}
    # 合并用户配置的 system_prompt 与 think 标签指令
    prompt = cfg.system_prompt or ""
    prompt = prompt + _THINK_PROMPT_SUFFIX
    kwargs["prompt"] = prompt
    return create_react_agent(model, tools, **kwargs)


async def run_custom_agent(
    key: str,
    thread_id: str,
    message: str,
    history: list | None = None,
) -> AsyncIterator[dict]:
    """运行自定义子代理，yield 标准化事件流（与内置子代理契约一致）。

    事件类型:
    - ``{"type": "token", "content": str}``: 模型流式输出 token
    - ``{"type": "tool_call", "id": str, "name": str, "args": dict}``: 工具调用开始
    - ``{"type": "tool_result", "id": str, "name": str, "result": Any}``: 工具调用结束

    ``id`` 来自 astream_events v2 的 ``run_id``，同一 tool run 的 start/end 共享，
    供前端按 id 配对（chat-rendering-trace-v2 D5）。

    Args:
        key: 自定义子代理 key。
        thread_id: 会话 ID。
        message: 当前用户消息。
        history: 历史 messages 列表（已截断），拼到 inputs 前。
    """
    agent = build_custom_agent(key, thread_id)
    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": message}]}
    source = f"custom-{key}"
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


__all__ = ["build_custom_agent", "run_custom_agent", "_make_custom_tools"]
