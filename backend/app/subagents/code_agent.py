"""Code 子代理：使用文件系统工具执行只读/搜索任务。

用 LangGraph create_react_agent 构建 ReAct 子图。
工具集: read_file, list_dir, glob_files, grep_files（只读）

设计说明（安全关键）：
- subagent **无 interrupt_before 审批流**，禁止包含任何写/编辑/shell 工具。
- 写文件 / 编辑文件 / shell_exec 等危险操作必须经 DeepAgent 路径走 interrupt_before
  审批，避免被绕过（参考 classifier.py 的 DANGEROUS_TOOL_KEYWORDS 注释）。
- 所有工具均经 SessionSandbox 授权校验，thread_id 从 state 透传。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from app.config import get_settings
from app.llm import get_chat_model


def _make_fs_tools(thread_id: str) -> list:
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


# 子代理思考过程提示：要求模型在思考时包裹 think 标签，供前端展示 reasoning block
_THINK_PROMPT_SUFFIX = (
    "\n\n在调用工具前，请先用 <think>..</think> 标签包裹你的思考过程，"
    "例如：<think>我需要查看目录内容来了解文件结构</think>。"
    "这样用户可以看到你的推理过程。"
)

def build_code_agent(thread_id: str) -> Any:
    """构建 Code 子代理 ReAct 子图，返回 CompiledStateGraph。"""
    settings = get_settings()
    cfg = settings.subagents["code"]
    model = get_chat_model(temperature=cfg.temperature, streaming=True)
    tools = _make_fs_tools(thread_id)
    kwargs: dict[str, Any] = {}
    # 合并用户配置的 system_prompt 与 think 标签指令
    prompt = cfg.system_prompt or ""
    prompt = prompt + _THINK_PROMPT_SUFFIX
    kwargs["prompt"] = prompt
    return create_react_agent(model, tools, name="code_agent", **kwargs)


async def run_code_agent(
    thread_id: str,
    message: str,
    history: list | None = None,
) -> AsyncIterator[dict]:
    """运行 Code 子代理，yield 标准化事件流。

    事件类型:
    - ``{"type": "token", "content": str}``: 模型流式输出 token
    - ``{"type": "tool_call", "id": str, "name": str, "args": dict}``: 工具调用开始
    - ``{"type": "tool_result", "id": str, "name": str, "result": Any}``: 工具调用结束

    ``id`` 来自 astream_events v2 的 ``run_id``，同一 tool run 的 start/end 共享，
    供前端 ``AssistantUIThread.buildRenderItems`` 按 id 配对（chat-rendering-trace-v2 D5）。

    Args:
        thread_id: 会话 ID。
        message: 当前用户消息。
        history: 历史 messages 列表（已截断），拼到 inputs 前。
    """
    agent = build_code_agent(thread_id)
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
                "source": "code",
            }
        elif kind == "on_tool_end":
            yield {
                "type": "tool_result",
                "id": run_id,
                "name": name,
                "result": data.get("output"),
                "source": "code",
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


__all__ = ["build_code_agent", "run_code_agent", "_make_fs_tools"]
