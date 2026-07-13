"""自定义子代理工厂：按 key 动态构建 ReAct 子图。

设计：
- 工具池复用 ``make_rag_tools`` / ``make_web_tools``，按配置的 ``tools`` 字段筛选并组装。
- 内置 fs 工具（ls/read_file/glob/grep）由 ``AuthorizedLocalShellBackend`` 自动注入，
  通过 ``create_agent(excluded_tools=FORBIDDEN_SUBAGENT_TOOLS)`` 过滤写工具（write_file/edit_file/delete_file）。
- **安全硬约束**：危险工具已在 config 层被 ``_sanitize_custom_tools`` 过滤，本模块再次
  防御性过滤，确保 subagent 不暴露任何写/编辑/shell 工具（参考 claude.md §10）。
- 自定义子代理复用路径 B 的事件契约（token / tool_call / tool_result）。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from app.config import FORBIDDEN_SUBAGENT_TOOLS, get_settings
from app.llm import get_chat_model
from app.observability.logger import logger
from app.subagents.base import (
    THINK_PROMPT_SUFFIX,
    run_react_agent_stream,
)
from app.utils.prompts import build_workspace_prompt_suffix


def _make_custom_tools(
    thread_id: str, tool_names: list[str], workspace_path: str | None = None
) -> list:
    """按 tool_names 组装工具列表（复用现有工具实现）。

    安全：再次过滤危险工具（防御性），即便 config 层漏过也保底。
    ``workspace_path`` 保留参数签名兼容，fs 工具已由 backend 注入无需此参数。

    注意：fs 工具（read_file/ls/glob/grep）不再通过本函数提供，
    由 ``AuthorizedLocalShellBackend`` 自动注入。本函数仅返回 rag/web 工具。
    """
    from app.subagents.base import make_rag_tools, make_web_tools

    # 防御性过滤：移除危险工具
    safe_names = {t for t in tool_names if t not in FORBIDDEN_SUBAGENT_TOOLS}
    tools: list = []
    # RAG 工具集（make_rag_tools 已含 tools_enabled 过滤）
    if "rag_retrieve" in safe_names:
        tools.extend(make_rag_tools(thread_id))
    # Web 工具集（make_web_tools 已含 tools_enabled 过滤）
    if "web_search" in safe_names:
        tools.extend(make_web_tools(thread_id))
    return tools


def build_custom_agent(
    key: str,
    thread_id: str | None = None,
    system_prompt: str | None = None,
    tools: list[str] | None = None,
    temperature: float | None = None,
    workspace_path: str | None = None,
    checkpointer: Any = None,
    rubric: str = "",
    grader_model: Any = None,
    chat_model: Any = None,
) -> Any:
    """构建自定义子代理 ReAct 子图，返回 CompiledStateGraph。

    支持两种调用模式：
    1. 从配置加载：``build_custom_agent(key, thread_id)`` — 从 ``Settings.custom_subagents`` 读取配置。
       此时 ``rubric`` / ``grader_model`` 从 config 透传（如果有）。
    2. 显式参数：``build_custom_agent(key, system_prompt=..., tools=..., temperature=...)`` —
       用于软件开发专家团角色等动态构建场景。
       ``rubric`` 非空时由 ``create_agent`` 注入 RubricMiddleware。

    Args:
        key: 子代理 key（用于命名和日志）。
        thread_id: 会话 ID，用于沙箱授权校验。模式 1 必须提供，模式 2 可选。
        system_prompt: 显式指定 system prompt（模式 2）。
        tools: 显式指定工具列表（模式 2）。
        temperature: 显式指定温度（模式 2）。
        workspace_path: 当前会话绑定的 workspace 路径，fs 工具解析相对路径用。
        checkpointer: 可选的 LangGraph checkpointer，用于状态持久化。
        rubric: 可选自纠规则文本；非空时由 create_agent 挂载 RubricMiddleware。
        grader_model: 可选判官模型；为 None 时 create_agent 默认用 get_chat_model(temperature=0)。

    Raises:
        KeyError: 模式 1 中 key 不存在于 custom_subagents。
        ValueError: 模式 2 中未提供必要参数。
    """
    settings = get_settings()

    # 模式 2：显式参数（用于 team 角色等动态构建）
    if system_prompt is not None or tools is not None or temperature is not None:
        from app.deepagent.factory import create_agent

        if not tools:
            logger.warning(
                "custom agent has no tools bound, agent will be unreachable",
                key=key,
            )
        _temp = temperature if temperature is not None else 0.2
        model = get_chat_model(temperature=_temp, streaming=True)
        _tools = _make_custom_tools(thread_id or "", tools or [], workspace_path)
        prompt = (
            (system_prompt or "")
            + THINK_PROMPT_SUFFIX
            + build_workspace_prompt_suffix(workspace_path)
        )
        return create_agent(
            model,
            _tools,
            system_prompt=prompt,
            checkpointer=checkpointer,
            thread_id=thread_id or "",
            workspace_path=workspace_path,
            name=f"custom_{key}",
            rubric=rubric or None,
            grader_model=grader_model,
            excluded_tools=FORBIDDEN_SUBAGENT_TOOLS,
        )

    # 模式 1：从配置加载
    from app.deepagent.factory import create_agent

    custom = settings.custom_subagents
    if key not in custom:
        raise KeyError(f"custom subagent not found: {key}")
    cfg = custom[key]
    if not cfg.tools:
        logger.warning(
            "custom subagent has no tools bound, agent will be unreachable",
            key=key,
        )
    model = chat_model if chat_model is not None else get_chat_model(temperature=cfg.temperature, streaming=True)
    _tools = _make_custom_tools(thread_id or "", cfg.tools, workspace_path)
    prompt = (
        (cfg.system_prompt or "")
        + THINK_PROMPT_SUFFIX
        + build_workspace_prompt_suffix(workspace_path)
    )
    return create_agent(
        model,
        _tools,
        system_prompt=prompt,
        checkpointer=checkpointer,
        thread_id=thread_id or "",
        workspace_path=workspace_path,
        name=f"custom_{key}",
        rubric=cfg.rubric or None,
        grader_model=cfg.grader_model,
        excluded_tools=FORBIDDEN_SUBAGENT_TOOLS,
    )


async def run_custom_agent(
    key: str,
    thread_id: str,
    message: str,
    history: list | None = None,
    workspace_path: str | None = None,
    checkpointer: Any = None,
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
        workspace_path: 当前会话绑定的 workspace 绝对路径，fs 工具解析相对路径用。
        checkpointer: 可选的 LangGraph checkpointer，用于状态持久化。
    """
    agent = build_custom_agent(
        key, thread_id=thread_id, workspace_path=workspace_path, checkpointer=checkpointer
    )
    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": message}]}
    source = f"custom-{key}"
    config = {"configurable": {"thread_id": thread_id}}
    async for event in run_react_agent_stream(agent, inputs, source, config=config):
        yield event


__all__ = ["build_custom_agent", "run_custom_agent", "_make_custom_tools"]
