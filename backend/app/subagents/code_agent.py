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

from langgraph.prebuilt import create_react_agent

from app.config import get_settings
from app.llm import get_chat_model
from app.subagents.base import (
    THINK_PROMPT_SUFFIX,
    _make_fs_tools,
    run_react_agent_stream,
)


def build_code_agent(thread_id: str, workspace_path: str | None = None) -> Any:
    """构建 Code 子代理 ReAct 子图，返回 CompiledStateGraph。

    ``workspace_path`` 用于沙箱授权时解析相对路径的基准。
    """
    settings = get_settings()
    cfg = settings.subagents["code"]
    model = get_chat_model(temperature=cfg.temperature, streaming=True)
    tools = _make_fs_tools(thread_id, workspace_path=workspace_path)
    kwargs: dict[str, Any] = {}
    # 合并用户配置的角色定义与 think 标签指令
    prompt = cfg.system_prompt or ""
    prompt = prompt + THINK_PROMPT_SUFFIX
    kwargs["prompt"] = prompt
    return create_react_agent(model, tools, name="code_agent", **kwargs)


async def run_code_agent(
    thread_id: str,
    message: str,
    history: list | None = None,
    workspace_path: str | None = None,
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
        workspace_path: 当前会话绑定的 workspace 绝对路径，相对路径解析基准。
    """
    agent = build_code_agent(thread_id, workspace_path=workspace_path)
    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": message}]}
    async for event in run_react_agent_stream(agent, inputs, source="code"):
        yield event


__all__ = ["build_code_agent", "run_code_agent", "_make_fs_tools"]
