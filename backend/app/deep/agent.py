"""DeepAgent 路径（路径 C）：deepagents 0.6+ harness，带危险工具中断。

- 用 ``app.deep.harness.create_agent`` 封装 ``deepagents.create_deep_agent`` 构建 DeepAgent
- 工具集: filesystem 全部 + rag_retrieve + web_search
- ``interrupt_on``：仅危险工具触发中断，只读工具自动放行
- 审批恢复统一交给 ``app.deep.execution.run_agent_with_approval``
- 使用共享的 ``AsyncSqliteSaver`` 作为 agent checkpointer
- deepagents 0.6+ ``PatchToolCallsMiddleware`` 在中间件层自动修复悬空 tool_calls
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, AsyncIterator

from app.config import get_settings
from app.deep.execution import (
    run_agent_with_approval,
    _get_pending_tool_calls as _get_pending_tool_calls,
    _is_interrupted as _is_interrupted,
)
from app.deep.harness import create_agent
from app.deep.streaming import _stream_agent_events as _stream_agent_events
from app.deep.tools import (
    DANGEROUS_TOOLS,
    _TOOL_NAME_MAP,
    _load_mcp_tools,
    _make_deep_tools,
)
from app.llm import get_chat_model
from app.memory.checkpointer import get_async_checkpointer
from app.observability.logger import logger
from app.sandbox import get_sandbox
from app.security.approval_flow import (
    _await_approval as _await_approval,
    _handle_directory_extension as _handle_directory_extension,
    _make_approval_event as _make_approval_event,
)
from app.utils.prompts import resolve_system_prompt

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.router.state import RouterState

# DeepAgent 系统提示
_DEEP_SYSTEM_PROMPT = (
    "你是一个强大的个人助理。你可以读写文件、搜索知识库、搜索网页。"
    "执行危险操作（写文件、执行命令）前需要用户审批。"
    "请根据用户任务规划步骤，调用合适的工具完成。"
    "\n\n对于需要多步执行的复杂任务，请使用 write_todos 工具维护任务清单，"
    "每完成一步更新对应 todo 的状态为 completed。"
)

# T10：异步画像抽取任务引用集合，防止被 GC 回收
_extract_tasks: set[asyncio.Task] = set()


__all__ = [
    "DANGEROUS_TOOLS",
    "build_deep_agent",
    "run_deep_path",
]


def _workspace_prompt_suffix(workspace_path: str | None) -> str:
    """根据工作区路径生成 system prompt 后缀。"""
    if not workspace_path:
        return ""
    return (
        f"\n\n当前工作目录: {workspace_path}\n"
        "该目录已授权，你可以直接使用 list_dir、read_file、glob、grep 等工具访问。"
        "执行 cli_execute 工具时，若用户未指定其他目录，"
        "必须将 cwd 参数设为当前工作目录；执行文件读写工具时，"
        "优先使用当前工作目录下的相对路径。"
    )


async def build_deep_agent(
    thread_id: str,
    tools: list | None = None,
    profile_prompt: str = "",
    checkpointer: Any = None,
    scene_prompt: str | None = None,
    workspace_path: str | None = None,
    chat_model: BaseChatModel | None = None,
    rubric: str | None = None,
    subagents: list | None = None,
) -> Any:
    """构造真实 DeepAgent 图。"""
    if chat_model is not None:
        model = chat_model
    else:
        model = get_chat_model(temperature=0.3, streaming=True)
    if tools is None:
        tools = _make_deep_tools(thread_id)
    if checkpointer is None:
        checkpointer = await get_async_checkpointer()
    base_prompt = resolve_system_prompt(
        default=_DEEP_SYSTEM_PROMPT,
        scene_prompt=scene_prompt,
        skill_extra=profile_prompt or None,
    )
    system_prompt = base_prompt + _workspace_prompt_suffix(workspace_path)
    return create_agent(
        model,
        tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        thread_id=thread_id,
        workspace_path=workspace_path,
        rubric=rubric,
        subagents=subagents,
    )


async def run_deep_path(
    state: RouterState,
    message: str,
    profile_prompt: str = "",
    history: list | None = None,
    permission_mode: str = "standard",
    scene_prompt: str | None = None,
    workspace_path: str | None = None,
    parent_thread_id: str | None = None,
    chat_model: BaseChatModel | None = None,
) -> AsyncIterator[dict]:
    """DeepAgent 路径 SSE 生成器。"""
    thread_id = state.get("thread_id", "")
    config: dict = {"configurable": {"thread_id": thread_id or "deep-default"}}
    sandbox = get_sandbox()
    is_full_trust = permission_mode == "full_trust"

    if is_full_trust:
        await sandbox.set_full_trust(thread_id, True)
        logger.info("deep agent full_trust mode enabled", thread_id=thread_id)

    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": message}]}

    try:
        agent_tools = _make_deep_tools(thread_id, workspace_path=workspace_path)
        mcp_tools, mcp_untrusted_names = await _load_mcp_tools()
        if mcp_tools:
            agent_tools.extend(mcp_tools)
            logger.info(
                "MCP tools merged into DeepAgent",
                thread_id=thread_id,
                count=len(mcp_tools),
                untrusted=len(mcp_untrusted_names),
            )
        agent = await build_deep_agent(
            thread_id,
            tools=agent_tools,
            profile_prompt=profile_prompt,
            scene_prompt=scene_prompt,
            workspace_path=workspace_path,
            chat_model=chat_model,
        )
    except ValueError as exc:
        yield {"event": "error", "data": f"LLM 不可用: {exc}"}
        if is_full_trust:
            await sandbox.set_full_trust(thread_id, False)
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("build_deep_agent failed", thread_id=thread_id)
        yield {"event": "error", "data": f"DeepAgent 初始化失败: {exc}"}
        if is_full_trust:
            await sandbox.set_full_trust(thread_id, False)
        return

    enabled_tool_names = {_TOOL_NAME_MAP.get(t.name, t.name) for t in agent_tools}
    runtime_dangerous = (DANGEROUS_TOOLS & enabled_tool_names) | mcp_untrusted_names

    try:
        async for sse in run_agent_with_approval(
            agent,
            config,
            thread_id=thread_id,
            workspace_path=workspace_path,
            permission_mode=permission_mode,
            runtime_dangerous=runtime_dangerous,
            source="deep",
            inputs=inputs,
            sandbox=sandbox,
            parent_thread_id=parent_thread_id,
            readonly_streak_threshold=10,
        ):
            yield sse
    finally:
        if is_full_trust:
            await sandbox.set_full_trust(thread_id, False)

    if get_settings().profile_auto_extract:
        from app.memory.profile_extractor import extract_last_assistant_reply, extract_profile_via_llm
        from app.memory.profile_store import upsert_from_llm

        async def _do_extract() -> None:
            try:
                assistant_reply = await extract_last_assistant_reply(agent, config)
                if assistant_reply:
                    entries = await extract_profile_via_llm(message, assistant_reply)
                    upsert_from_llm(entries)
                    logger.info("profile auto extracted", count=len(entries))
            except Exception as exc:  # noqa: BLE001
                logger.warning("profile auto extract failed", error=str(exc))

        task = asyncio.create_task(_do_extract())
        _extract_tasks.add(task)
        task.add_done_callback(_extract_tasks.discard)
