"""Code 子代理：使用文件系统工具执行读写/搜索/执行任务。

用 LangGraph create_react_agent 构建 ReAct 子图。
工具集: read_file, list_dir, glob_files, grep_files, edit_file, write_file
（均经 SessionSandbox 授权校验，thread_id 从 state 透传）
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from app.llm import get_chat_model


def _make_fs_tools(thread_id: str) -> list:
    """构建绑定 ``thread_id`` 的文件系统工具列表。

    filesystem 工具的签名含 ``thread_id``（用于沙箱授权校验），该参数不应暴露给
    LLM。这里通过闭包绑定 ``thread_id``，对外只声明业务参数。
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

    @tool
    async def write_file(path: str, content: str) -> str:
        """写入文本文件（覆盖）。"""
        return await fs.write_file(thread_id, path, content)

    @tool
    async def edit_file(path: str, old_text: str, new_text: str) -> str:
        """编辑文件：将 old_text 替换为 new_text（仅首次匹配）。"""
        return await fs.edit_file(thread_id, path, old_text, new_text)

    return [read_file, list_dir, glob_files, grep_files, write_file, edit_file]


def build_code_agent(thread_id: str) -> Any:
    """构建 Code 子代理 ReAct 子图，返回 CompiledStateGraph。"""
    model = get_chat_model(temperature=0.2, streaming=True)
    tools = _make_fs_tools(thread_id)
    return create_react_agent(model, tools, name="code_agent")


async def run_code_agent(thread_id: str, message: str) -> AsyncIterator[dict]:
    """运行 Code 子代理，yield 标准化事件流。

    事件类型:
    - ``{"type": "token", "content": str}``: 模型流式输出 token
    - ``{"type": "tool_call", "name": str, "args": dict}``: 工具调用开始
    - ``{"type": "tool_result", "name": str, "result": Any}``: 工具调用结束
    """
    agent = build_code_agent(thread_id)
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


__all__ = ["build_code_agent", "run_code_agent", "_make_fs_tools"]
