"""DeepAgent 路径（路径 C）：deepagents 0.6+ harness，带危险工具中断。

- 用 ``app.deepagent.factory.create_agent`` 封装 ``deepagents.create_deep_agent`` 构建 DeepAgent
- 工具集: filesystem 全部 + rag_retrieve + web_search
- ``interrupt_on``：仅危险工具触发中断，只读工具自动放行
- 审批恢复统一交给 ``app.deepagent.approval_runner.run_agent_with_approval``
- 使用共享的 ``AsyncSqliteSaver`` 作为 agent checkpointer
- deepagents 0.6+ ``PatchToolCallsMiddleware`` 在中间件层自动修复悬空 tool_calls
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator

from app.config import get_settings
from app.deepagent.approval_runner import run_agent_with_approval
from app.deepagent.context import current_thread_id
from app.deepagent.factory import create_agent
from app.deepagent.tool_assembly import (
    DANGEROUS_TOOLS,
    _load_mcp_tools,
    _make_deep_tools,
    compute_runtime_dangerous,
)
from app.llm import get_chat_model
from app.memory.checkpointer import get_async_checkpointer
from app.observability.logger import logger
from app.sandbox import get_sandbox
from app.utils.prompts import build_workspace_prompt_suffix, resolve_system_prompt
from app.sse.events import make_error_event

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.router.state import RouterState

# DeepAgent 系统提示
# write_todos 指令由 deepagents TodoListMiddleware 自带 WRITE_TODOS_SYSTEM_PROMPT 自动注入
_DEEP_SYSTEM_PROMPT = (
    "你是一个强大的个人助理。你可以读写文件、搜索知识库、搜索网页。"
    "执行危险操作（写文件、执行命令）前需要用户审批。"
    "请根据用户任务规划步骤，调用合适的工具完成。"
)

__all__ = [
    "DANGEROUS_TOOLS",
    "build_deep_agent",
    "run_deep_path",
    "trigger_profile_auto_extract",
]


async def build_deep_agent(
    thread_id: str,
    tools: list | None = None,
    profile_prompt: str = "",
    checkpointer: Any = None,
    scene_prompt: str | None = None,
    workspace_path: str | None = None,
    chat_model: BaseChatModel | None = None,
    rubric: str | None = None,
    grader_model: Any | None = None,
    subagents: list | None = None,
) -> Any:
    """构造真实 DeepAgent 图。"""
    if chat_model is not None:
        model = chat_model
    else:
        model = get_chat_model(
            temperature=get_settings().llm_temperature_orchestrator, streaming=True
        )
    if tools is None:
        tools = _make_deep_tools(thread_id)
    if checkpointer is None:
        checkpointer = await get_async_checkpointer()
    base_prompt = resolve_system_prompt(
        default=_DEEP_SYSTEM_PROMPT,
        scene_prompt=scene_prompt,
        skill_extra=profile_prompt or None,
    )
    system_prompt = base_prompt + build_workspace_prompt_suffix(workspace_path)
    return create_agent(
        model,
        tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        thread_id=thread_id,
        workspace_path=workspace_path,
        rubric=rubric,
        grader_model=grader_model,
        subagents=subagents,
    )


async def trigger_profile_auto_extract(
    agent: Any,
    config: dict,
    message: str,
    workspace_path: str | None = None,
) -> None:
    """异步触发用户画像自动提取（持久化队列）。

    从 agent 的 checkpointer 读取最后一轮 assistant 回复，将抽取请求落到
    SQLite 队列，由后台工作器异步调用 LLM 提取并写入画像存储。
    即使进程被 kill -9 / OOM，只要 checkpoint 中的 assistant 回复已写入，
    队列中的任务会在下次启动时被重新消费。

    - ``workspace_path`` 非空 → 写入 ``.agentx/memory/<key>.md``（工作区记忆）
    - ``workspace_path`` 为空 → 写入 ``data/config/profile.json``（全局画像）

    三条路径（deep / coding / work）在 SSE 流结束后调用此函数。
    """
    if not get_settings().profile_auto_extract:
        return

    from app.memory.extract_queue import enqueue
    from app.memory.profile_extractor import extract_last_assistant_reply

    try:
        assistant_reply = await extract_last_assistant_reply(agent, config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("profile auto extract read assistant reply failed", error=str(exc))
        return
    if not assistant_reply:
        return

    await enqueue(
        message=message,
        assistant_reply=assistant_reply,
        workspace_path=workspace_path,
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
    # 设置 contextvar，供 AuthorizedLocalShellBackend 读取 thread_id 做沙箱授权
    current_thread_id.set(thread_id)
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
        yield make_error_event(f"LLM 不可用: {exc}")
        if is_full_trust:
            await sandbox.set_full_trust(thread_id, False)
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("build_deep_agent failed", thread_id=thread_id)
        yield make_error_event(f"DeepAgent 初始化失败: {exc}")
        if is_full_trust:
            await sandbox.set_full_trust(thread_id, False)
        return

    runtime_dangerous = compute_runtime_dangerous(
        agent_tools, mcp_untrusted_names, workspace_path
    )

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
        ):
            yield sse
    finally:
        if is_full_trust:
            await sandbox.set_full_trust(thread_id, False)

    await trigger_profile_auto_extract(agent, config, message, workspace_path=workspace_path)
