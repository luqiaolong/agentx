"""DeepAgent 路径（路径 C）：deepagents + LangGraph，带危险工具中断。

- 用 ``langgraph.prebuilt.create_react_agent`` 构建 ReAct DeepAgent（与 subagents 一致）
- 工具集: filesystem 全部 + rag_retrieve + web_search
- ``interrupt_before=["tools"]``：调用任何工具前 LangGraph 暂停，SSE handler 检查待执行
  工具是否属于 ``DANGEROUS_TOOLS``，是则 yield approval_request 等用户审批，否则自动放行
- 审批恢复: ``_await_approval`` 轮询 ``app.approval``，通过后以
  ``agent.astream_events(None, config)`` 续跑（LangGraph ``interrupt_before`` 的标准恢复方式）
- 使用共享的 ``AsyncSqliteSaver`` 作为 agent checkpointer，支持跨轮次历史 + interrupt/resume
- 权限模式：
  - ``standard``（默认）：危险工具走审批；只读 fs 工具访问未授权目录时弹扩展授权弹窗
  - ``full_trust``：会话内全量放行，不弹任何审批弹窗（系统关键目录仍拒绝）

模块拆分（Phase 2.3）:
- ``app.deep.tools``：``DANGEROUS_TOOLS`` / ``_TOOL_NAME_MAP`` / ``_make_deep_tools`` / ``_load_mcp_tools``
- ``app.deep.streaming``：``_stream_agent_events``
- ``app.deep.approval``：``wait_for_approval`` / ``_await_approval`` / ``_make_approval_event`` /
  ``_handle_directory_extension`` 及辅助函数
- ``app.deep.recovery``：``_inject_tool_error_messages`` / ``_sanitize_message_history`` /
  ``_collect_unpaired_tool_call_ids`` / ``_to_serializable``

本模块仅保留编排层：``_DEEP_SYSTEM_PROMPT`` / ``build_deep_agent`` / ``run_deep_path`` /
``_extract_tasks`` / ``_get_pending_tool_calls`` / ``_is_interrupted``，并通过 ``from ... import``
re-export 子模块符号，保持向后兼容（``from app.deep.agent import _make_approval_event`` 等仍可用）。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import uuid4

from langgraph.prebuilt import create_react_agent

from app.approval import clear_pause, is_paused
from app.approval.state import get_pause_event
from app.config import get_settings
from app.deep.approval import (
    _APPROVAL_POLL_INTERVAL,
    _await_approval,
    _extract_paths_from_tool_call,
    _handle_directory_extension,
    _make_approval_event,
    wait_for_approval,
)
from app.deep.recovery import (
    _inject_tool_error_messages,
    _sanitize_message_history,
)
from app.deep.streaming import _stream_agent_events
from app.deep.tools import (
    DANGEROUS_TOOLS,
    _TOOL_NAME_MAP,
    _load_mcp_tools,
    _make_deep_tools,
)
from app.llm import get_chat_model
from app.memory.checkpointer import get_async_checkpointer
from app.observability.logger import logger
from app.utils.prompts import resolve_system_prompt
from app.utils.security import get_sandbox
from app.utils.sse_events import make_sse_event

if TYPE_CHECKING:
    # RouterState 仅用于类型注解（``from __future__ import annotations`` 使注解
    # 在运行时为字符串），延迟到 TYPE_CHECKING 避免运行时循环导入。
    # 场景化架构下 graph.py 不再 import run_deep_path，但 team/scheduler.py 仍调用。
    from app.router.state import RouterState

# DeepAgent 系统提示
_DEEP_SYSTEM_PROMPT = (
    "你是一个强大的个人助理。你可以读写文件、搜索知识库、搜索网页。"
    "执行危险操作（写文件、执行命令）前需要用户审批。"
    "请根据用户任务规划步骤，调用合适的工具完成。"
    "\n\n对于需要多步执行的复杂任务，请先输出 JSON 计划，格式："
    '{"plan": [{"id": "1", "title": "步骤标题", "status": "pending"}, ...]}'
    "；执行过程中每次完成一步输出："
    '{"plan_update": {"id": "...", "status": "done"}}'
    "。"
)

# T10：异步画像抽取任务引用集合，防止被 GC 回收（asyncio 已知坑）
_extract_tasks: set[asyncio.Task] = set()


__all__ = [
    "DANGEROUS_TOOLS",
    "build_deep_agent",
    "run_deep_path",
    "wait_for_approval",
]


def _workspace_prompt_suffix(workspace_path: str | None) -> str:
    """根据工作区路径生成 system prompt 后缀，提示 LLM 使用当前工作目录。"""
    if not workspace_path:
        return ""
    return (
        f"\n\n当前工作目录: {workspace_path}\n"
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
) -> Any:
    """构造真实 DeepAgent 图。

    用 ``create_react_agent`` 构建 ReAct 子图，``interrupt_before=["tools"]`` 使图在
    执行任何工具前暂停。使用共享的 ``AsyncSqliteSaver`` 作为 checkpointer，支持
    跨轮次历史恢复与 interrupt/resume 循环。

    Args:
        thread_id: 会话 ID（用于工具的沙箱授权绑定）。
        tools: 可选，已构建的工具列表。若未传则内部调用 ``_make_deep_tools(thread_id)``。
            ``run_deep_path`` 可先构建工具集，复用于 dangerous 判断。
        profile_prompt: 可选，用户画像前缀，拼到 ``_DEEP_SYSTEM_PROMPT`` 前。
        checkpointer: 可选，共享的 LangGraph checkpointer。若未传则用
            ``await get_async_checkpointer()`` 获取全局 ``AsyncSqliteSaver`` 单例。
        scene_prompt: 可选场景 prompt，非空时覆盖 ``_DEEP_SYSTEM_PROMPT``。
        workspace_path: 可选当前工作区绝对路径，注入到 system prompt 并作为 cli_execute 默认 cwd。

    Returns:
        编译后的 CompiledStateGraph 实例。
    """
    model = get_chat_model(temperature=0.3, streaming=True)
    if tools is None:
        tools = _make_deep_tools(thread_id)
    if checkpointer is None:
        # MUST await：get_async_checkpointer 是 async def，不 await 会传入 coroutine
        # 导致 create_react_agent 报 "Invalid checkpointer ... Received coroutine"
        checkpointer = await get_async_checkpointer()
    # T9：画像前缀拼到最前；scene_prompt 覆盖 _DEEP_SYSTEM_PROMPT（场景切换器注入）
    base_prompt = resolve_system_prompt(
        default=_DEEP_SYSTEM_PROMPT,
        scene_prompt=scene_prompt,
        skill_extra=profile_prompt or None,
    )
    system_prompt = base_prompt + _workspace_prompt_suffix(workspace_path)
    return create_react_agent(
        model,
        tools,
        name="deep_agent",
        prompt=system_prompt,
        interrupt_before=["tools"],
        checkpointer=checkpointer,
    )


async def _get_pending_tool_calls(agent: Any, config: dict) -> list[dict]:
    """从 agent 状态中提取待执行的工具调用列表。

    当图在 ``interrupt_before=["tools"]`` 处暂停时，最后一条消息是 AIMessage，
    其 ``tool_calls`` 属性包含待执行的工具调用。

    MUST 使用 ``aget_state``（异步接口）：agent 的 checkpointer 是
    ``AsyncSqliteSaver``，在主线程同步调用 ``get_state`` 会抛
    "Synchronous calls to AsyncSqliteSaver are only allowed from a different thread"
    （截图 bug 根因）。同步 ``get_state`` 在主线程会阻塞事件循环；切换到
    ``aget_state`` 走 aiosqlite 异步通道，避免该异常并与其他 SSE 异步逻辑一致。
    """
    state = await agent.aget_state(config)
    if not state or not state.values:
        return []
    messages = state.values.get("messages", [])
    if not messages:
        return []
    last_msg = messages[-1]
    tool_calls = getattr(last_msg, "tool_calls", None) or []
    return list(tool_calls)


async def _is_interrupted(agent: Any, config: dict) -> bool:
    """检查 agent 是否在 interrupt 处暂停（next 含 "tools"）。

    MUST 使用 ``aget_state``（异步接口）——见 ``_get_pending_tool_calls`` 注释。
    """
    state = await agent.aget_state(config)
    if not state or not state.next:
        return False
    return "tools" in state.next


async def _inject_tool_error_for_call(
    agent: Any, config: dict, tool_call: dict, error_text: str
) -> None:
    """为单个 tool_call 注入 ToolMessage 错误。

    用于危险工具审批被拒绝时，避免 checkpoint 中残留未配对的 tool_call
    导致后续 ``INVALID_CHAT_HISTORY`` 校验失败。
    """
    from langchain_core.messages import ToolMessage

    tc_id = tool_call.get("id") or str(uuid4())
    tool_msg = ToolMessage(content=error_text, tool_call_id=tc_id)
    try:
        await agent.aupdate_state(config, {"messages": [tool_msg]})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "inject_tool_error_for_call failed",
            thread_id=config.get("configurable", {}).get("thread_id", ""),
            tool=tool_call.get("name"),
            error=str(exc),
        )


async def run_deep_path(
    state: RouterState,
    message: str,
    profile_prompt: str = "",
    history: list | None = None,
    permission_mode: str = "standard",
    scene_prompt: str | None = None,
    workspace_path: str | None = None,
) -> AsyncIterator[dict]:
    """DeepAgent 路径 SSE 生成器（真实实现）。

    流程:
    1. 构建 DeepAgent（含 ``interrupt_before=["tools"]``）
    2. 若 ``permission_mode == "full_trust"``：``sandbox.set_full_trust(thread_id, True)``
    3. ``astream_events`` 驱动图执行，流式产出 token / todo_update 事件
    4. 流结束后检查是否在 tools 前中断
    5. 若中断：按权限模式处理待执行工具
       - full_trust：直接放行所有工具（包括危险工具）
       - standard：
         - 危险工具 → yield approval_request(kind=dangerous_tool) → 等待审批
         - 只读 fs 工具访问未授权目录 → yield approval_request(kind=directory_extension)
           → 等待决策（once/session/deny）
         - 其他 → 自动放行
    6. 恢复后继续流式，循环直至图完成（``state.next`` 为空）
    7. 清理：``sandbox.set_full_trust(thread_id, False)`` + ``sandbox.clear_temp(thread_id)``
    8. T10：若 ``profile_auto_extract`` 开启，异步调 LLM 抽取画像并写入 profile.json

    Args:
        state: Router 状态（含 thread_id）。
        message: 用户消息。
        profile_prompt: 用户画像前缀，拼到 DeepAgent system prompt 前。
        history: 历史 messages 列表（已截断），拼到 inputs 前。
        permission_mode: 权限模式，"workspace"（默认，仅当前工作区）或 "full_trust"。
        scene_prompt: 可选场景 prompt，透传给 build_deep_agent。
        workspace_path: 可选当前工作区绝对路径，注入 system prompt 并作为 cli_execute 默认 cwd。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    thread_id = state.get("thread_id", "")
    config: dict = {"configurable": {"thread_id": thread_id or "deep-default"}}
    sandbox = get_sandbox()
    is_full_trust = permission_mode == "full_trust"

    # full_trust 模式：设置 sandbox 标志，fs 工具自动放行
    if is_full_trust:
        sandbox.set_full_trust(thread_id, True)
        logger.info("deep agent full_trust mode enabled", thread_id=thread_id)

    # 构建完整 inputs：history + 当前消息
    # history 已是 BaseMessage 列表，create_react_agent 的 messages channel 接受 BaseMessage
    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": message}]}

    # 1. 构建 agent（先构建工具集，便于计算运行时 dangerous 集合）
    try:
        agent_tools = _make_deep_tools(thread_id, workspace_path=workspace_path)
        # 异步加载 MCP 工具并合并到 DeepAgent 工具集
        # MCP 工具仅暴露给 DeepAgent（路径 C），subagent 不暴露（安全红线）
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
        )
    except ValueError as exc:
        yield make_sse_event("error", f"LLM 不可用: {exc}")
        if is_full_trust:
            sandbox.set_full_trust(thread_id, False)
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("build_deep_agent failed", thread_id=thread_id)
        yield make_sse_event("error", f"DeepAgent 初始化失败: {exc}")
        if is_full_trust:
            sandbox.set_full_trust(thread_id, False)
        return

    # 运行时危险工具集合 = DANGEROUS_TOOLS 与已启用工具的交集
    # 安全关键：若 edit_file 被禁用，此处不含 edit_file，审批流不会误触发
    # MCP 工具：来自 trusted=False server 的工具也加入危险集合，触发审批
    enabled_tool_names = {
        _TOOL_NAME_MAP.get(t.name, t.name) for t in agent_tools
    }
    runtime_dangerous = (DANGEROUS_TOOLS & enabled_tool_names) | mcp_untrusted_names

    # 1b. 防御性清理：若之前异常退出导致 checkpoint 中残留未配对的
    # tool_calls，首次 astream 会因 _validate_chat_history 抛 INVALID_CHAT_HISTORY。
    # 此处提前注入 ToolMessage 修复，确保历史消息一致性。
    await _inject_tool_error_messages(
        agent, config, "上次操作未正常完成，已自动清理状态"
    )

    # 1c. 净化 inputs：当 checkpoint 被 DELETE 清空（用户清空历史）或
    # history 含中断残留的未配对 AIMessage 时，_inject_tool_error_messages
    # 因 state 为空直接返回，但 inputs["messages"] 仍含未配对 tool_calls，
    # _validate_chat_history 仍会抛 INVALID_CHAT_HISTORY。此处直接对 inputs
    # 补齐 ToolMessage，确保 astream 不会因校验失败而中断。
    inputs["messages"] = _sanitize_message_history(
        inputs["messages"], "上次操作未正常完成，已自动清理状态"
    )

    # 2. 初始流式运行（可能中断在 tools 前）
    try:
        async for sse in _stream_agent_events(agent, inputs, config):
            yield sse
    except Exception as exc:  # noqa: BLE001 — SSE 兜底
        logger.exception("deep agent stream failed", thread_id=thread_id)
        await _inject_tool_error_messages(agent, config, f"DeepAgent 执行失败: {exc}")
        yield make_sse_event("error", f"DeepAgent 执行失败: {exc}")
        if is_full_trust:
            sandbox.set_full_trust(thread_id, False)
        return

    # 3. 中断/恢复循环
    max_iterations = 50  # 安全上限，防止无限循环
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        # 5b. 暂停/恢复检查：pause 时 yield paused 事件并阻塞，resume 后 yield resumed
        # 注意：不清理 pending_approvals 或 abort_flags，只暂停 LLM 流。
        if is_paused(thread_id):
            yield make_sse_event("paused", {})
            pause_event = get_pause_event(thread_id)
            if is_paused(thread_id):
                await pause_event.wait()
            yield make_sse_event("resumed", {})

        if not await _is_interrupted(agent, config):
            # 图已完成，退出循环
            break

        # 获取待执行的工具调用
        pending_calls = await _get_pending_tool_calls(agent, config)
        if not pending_calls:
            # 无待执行工具调用，不应发生但安全退出
            logger.warning("interrupted but no pending tool calls", thread_id=thread_id)
            break

        # full_trust 模式：所有工具直接放行，不弹审批
        if is_full_trust:
            # 清理 once 临时授权（防御性，full_trust 模式理论上不用 temp）
            sandbox.clear_temp(thread_id)
            try:
                async for sse in _stream_agent_events(agent, None, config):
                    yield sse
            except Exception as exc:  # noqa: BLE001
                logger.exception("deep agent resume failed", thread_id=thread_id)
                await _inject_tool_error_messages(agent, config, f"DeepAgent 恢复失败: {exc}")
                yield make_sse_event("error", f"DeepAgent 恢复失败: {exc}")
                sandbox.set_full_trust(thread_id, False)
                return
            continue

        # standard 模式：按工具类型处理
        # 检查是否有危险工具（运行时集合 = DANGEROUS_TOOLS ∩ 已启用工具）
        # 优化：若危险工具的目标路径已授权写入，则跳过审批（工作区内免审批）
        dangerous_calls = []
        for tc in pending_calls:
            name = tc.get("name", "")
            if name not in runtime_dangerous:
                continue
            # 提取路径并检查是否已授权写入
            # cli_execute 未指定 cwd 时，用 workspace_path 兜底，避免已选工作区仍弹审批
            paths = _extract_paths_from_tool_call(tc, workspace_path)
            # 无路径参数的工具（如 shell_exec）或路径未授权 → 需审批
            if not paths:
                dangerous_calls.append(tc)
                continue
            # 所有路径均已授权写入 → 跳过审批
            all_authorized = all(
                sandbox.is_path_authorized(thread_id, p, writable=True)
                for p in paths
            )
            if not all_authorized:
                dangerous_calls.append(tc)

        if dangerous_calls:
            # 4a. 危险工具 → 一次性 yield 所有危险工具的 approval_request，等待统一审批
            for tc in dangerous_calls:
                yield _make_approval_event(tc, thread_id, kind="dangerous_tool")

            decision = await _await_approval(
                thread_id,
                poll_interval=_APPROVAL_POLL_INTERVAL,
                max_wait=float("inf")
                if get_settings().approval_max_wait == 0
                else get_settings().approval_max_wait,
            )

            if decision is None or not decision.approved:
                # 拒绝/超时/中止：为每个待审批的危险 tool_call 注入 ToolMessage 错误，
                # 避免 checkpoint 中残留未配对的 tool_calls 导致后续 INVALID_CHAT_HISTORY。
                for tc in dangerous_calls:
                    await _inject_tool_error_for_call(
                        agent, config, tc, "用户拒绝执行危险操作"
                    )
                yield make_sse_event("error", "用户拒绝执行危险操作")
                sandbox.set_full_trust(thread_id, False)
                return

            # 审批通过，继续恢复执行
            logger.info(
                "deep agent approval granted",
                thread_id=thread_id,
                tool_count=len(dangerous_calls),
                tools=[tc.get("name") for tc in dangerous_calls],
            )
        else:
            # 4b. 非危险工具：检查只读 fs 工具是否越界（directory_extension）
            extension_handled = await _handle_directory_extension(
                pending_calls, thread_id, sandbox
            )
            for evt in extension_handled.events:
                yield evt
            if extension_handled.denied:
                yield make_sse_event("error", "用户拒绝访问该目录")
                await _inject_tool_error_messages(
                    agent, config, "用户拒绝访问该目录"
                )
                sandbox.set_full_trust(thread_id, False)
                return
            if extension_handled.timed_out:
                yield make_sse_event("error", "目录授权等待被中断，操作未执行")
                await _inject_tool_error_messages(
                    agent, config, "目录授权等待被中断，操作未执行"
                )
                sandbox.set_full_trust(thread_id, False)
                return

        # 5. 恢复执行：用 None 输入续跑（LangGraph interrupt_before 标准恢复方式）
        try:
            async for sse in _stream_agent_events(agent, None, config):
                yield sse
        except Exception as exc:  # noqa: BLE001 — SSE 兜底
            logger.exception("deep agent resume failed", thread_id=thread_id)
            await _inject_tool_error_messages(agent, config, f"DeepAgent 恢复失败: {exc}")
            yield make_sse_event("error", f"DeepAgent 恢复失败: {exc}")
            sandbox.set_full_trust(thread_id, False)
            return

        # 6. 清理 once 临时授权（每次工具调用恢复后清理）
        sandbox.clear_temp(thread_id)

    if iteration >= max_iterations:
        logger.warning("deep agent hit max iterations", thread_id=thread_id)
        yield make_sse_event("error", "DeepAgent 达到最大迭代上限")
        await _inject_tool_error_messages(
            agent, config, "DeepAgent 达到最大迭代上限"
        )
        sandbox.set_full_trust(thread_id, False)
        return

    # 7. 清理 full_trust 标志（防御性）
    if is_full_trust:
        sandbox.set_full_trust(thread_id, False)

    # T10：路径 C 流式结束后，若开关开启则异步触发画像抽取（失败仅 warning，不报错）
    # 不阻塞 done 事件：fire-and-forget（spec memory-management R10）
    if get_settings().profile_auto_extract:
        from app.memory.profile_extractor import extract_last_assistant_reply, extract_profile_via_llm
        async def _do_extract() -> None:
            try:
                assistant_reply = await extract_last_assistant_reply(agent, config)
                if assistant_reply:
                    from app.memory.profile_store import upsert_from_llm
                    entries = await extract_profile_via_llm(message, assistant_reply)
                    upsert_from_llm(entries)
                    logger.info("profile auto extracted", count=len(entries))
            except Exception as exc:  # noqa: BLE001 — 抽取失败不报错
                logger.warning("profile auto extract failed", error=str(exc))
        task = asyncio.create_task(_do_extract())
        _extract_tasks.add(task)
        task.add_done_callback(_extract_tasks.discard)

    # done 事件由 run_router 统一 yield，此处不再重复
