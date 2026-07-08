"""work 场景 Supervisor（全能 agent）实现。

基于 ``create_react_agent`` 构建，与 DeepAgent 共享 streaming/approval 基础设施，
但有以下区别：
1. 使用 Supervisor 专用 system prompt（``_DEFAULT_SUPERVISOR_SYSTEM_PROMPT``）
2. 额外注入 ``delegate_to_expert`` / ``delegate_to_subagent`` 委派工具
3. 支持 @mention 语法强制委派
4. SSE 事件 source 标识为 ``"work"``

流程:
1. 解析 @mention：若命中 Expert 则直接运行 Expert；若命中子代理则运行后回注 Supervisor
2. 构建 Supervisor agent（含委派工具 + 完整工具集 + interrupt_before 审批）
3. ``astream_events`` 驱动图执行，流式产出 token / tool_call / tool_result 事件
4. 危险工具中断 → yield approval_request → 等待审批 → 恢复执行
5. 循环直至图完成
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from langgraph.prebuilt import create_react_agent

from app.agents.supervisor.delegation import make_delegation_tools
from app.agents.supervisor.mention import parse_mention
from app.approval import is_paused
from app.approval.state import get_pause_event
from app.config import get_settings
from app.deep.approval import (
    _APPROVAL_POLL_INTERVAL,
    _await_approval,
    _extract_paths_from_tool_call,
    _handle_directory_extension,
    _make_approval_event,
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

__all__ = [
    "build_work_supervisor",
    "run_work_supervisor",
]


def _workspace_prompt_suffix(workspace_path: str | None) -> str:
    """根据工作区路径生成 system prompt 后缀。"""
    if not workspace_path:
        return ""
    return (
        f"\n\n当前工作目录: {workspace_path}\n"
        "执行 cli_execute 工具时，若用户未指定其他目录，"
        "必须将 cwd 参数设为当前工作目录；执行文件读写工具时，"
        "优先使用当前工作目录下的相对路径。"
    )


async def build_work_supervisor(
    thread_id: str,
    tools: list | None = None,
    profile_prompt: str = "",
    checkpointer: Any = None,
    workspace_path: str | None = None,
) -> Any:
    """构造 work 场景 Supervisor agent。

    用 ``create_react_agent`` 构建 ReAct 图，``interrupt_before=["tools"]`` 使图在
    执行任何工具前暂停，由外层 ``run_work_supervisor`` 检查是否为危险工具并
    触发审批流。

    Args:
        thread_id: 会话 ID（用于工具沙箱授权绑定）。
        tools: 可选，已构建的工具列表（含委派工具）。若未传则内部构建。
        profile_prompt: 可选，用户画像前缀，拼到 system prompt 前。
        checkpointer: 可选，共享的 LangGraph checkpointer。
        workspace_path: 可选当前工作区绝对路径。

    Returns:
        编译后的 CompiledStateGraph 实例。
    """
    from app.config.prompts.agent import _DEFAULT_SUPERVISOR_SYSTEM_PROMPT

    settings = get_settings()
    supervisor_cfg = settings.agents.supervisor

    model = get_chat_model(temperature=supervisor_cfg.temperature, streaming=True)

    if tools is None:
        # 标准工具集（fs + cli + git + rag + web）
        standard_tools = _make_deep_tools(thread_id, workspace_path=workspace_path)
        # 委派工具
        delegation_tools = make_delegation_tools(thread_id, workspace_path)
        tools = [*standard_tools, *delegation_tools]

    if checkpointer is None:
        checkpointer = await get_async_checkpointer()

    # 构造 system prompt：画像前缀 + Supervisor prompt + 工作区后缀
    base_prompt = resolve_system_prompt(
        default=supervisor_cfg.system_prompt or _DEFAULT_SUPERVISOR_SYSTEM_PROMPT,
        scene_prompt=None,
        skill_extra=profile_prompt or None,
    )
    system_prompt = base_prompt + _workspace_prompt_suffix(workspace_path)

    return create_react_agent(
        model,
        tools,
        name="work_supervisor",
        prompt=system_prompt,
        interrupt_before=["tools"],
        checkpointer=checkpointer,
    )


async def _get_pending_tool_calls(agent: Any, config: dict) -> list[dict]:
    """从 agent 状态中提取待执行的工具调用列表。"""
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
    """检查 agent 是否在 interrupt 处暂停。"""
    state = await agent.aget_state(config)
    if not state or not state.next:
        return False
    return "tools" in state.next


async def _inject_tool_error_for_call(
    agent: Any, config: dict, tool_call: dict, error_text: str
) -> None:
    """为单个 tool_call 注入 ToolMessage 错误。"""
    from langchain_core.messages import ToolMessage
    from uuid import uuid4

    tc_id = tool_call.get("id") or str(uuid4())
    tool_msg = ToolMessage(content=error_text, tool_call_id=tc_id)
    try:
        await agent.aupdate_state(config, {"messages": [tool_msg]})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "supervisor.inject_tool_error_for_call failed",
            thread_id=config.get("configurable", {}).get("thread_id", ""),
            tool=tool_call.get("name"),
            error=str(exc),
        )


async def run_work_supervisor(
    message: str,
    thread_id: str,
    profile_prompt: str = "",
    history: list | None = None,
    permission_mode: str = "standard",
    workspace_path: str | None = None,
) -> AsyncIterator[dict]:
    """运行 work 场景 Supervisor，yield SSE 事件。

    流程:
    1. 解析 @mention：若命中 Expert 直接运行 Expert；命中子代理运行后回注 Supervisor
    2. 构建 Supervisor agent（含委派工具 + interrupt_before 审批）
    3. 流式执行，危险工具中断 → 审批 → 恢复，循环直至完成

    Args:
        message: 用户消息（可能含 @mention）。
        thread_id: 会话 ID。
        profile_prompt: 用户画像前缀。
        history: 历史 messages 列表（已截断）。
        permission_mode: 权限模式，"standard" 或 "full_trust"。
        workspace_path: 可选当前工作区绝对路径。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    config: dict = {"configurable": {"thread_id": thread_id or "work-default"}}
    sandbox = get_sandbox()
    is_full_trust = permission_mode == "full_trust"

    # ---- 1. @mention 解析 ----
    cleaned_message, mention_target = parse_mention(message)

    # @mention 命中 Expert：直接运行 Expert，bypass Supervisor
    if mention_target and mention_target[0] == "expert":
        expert_name = mention_target[1]
        logger.info(
            "supervisor.mention_force_expert",
            thread_id=thread_id,
            expert=expert_name,
        )
        yield make_sse_event("delegation", {
            "target": expert_name,
            "source": "work",
            "message": f"@mention 强制委派给 {expert_name} Expert",
        })
        from app.agents.expert.coding import run_coding_expert

        async for sse in _convert_expert_events(
            run_coding_expert(
                cleaned_message,
                thread_id,
                profile_prompt=profile_prompt,
                history=history,
                workspace_path=workspace_path,
                permission_mode=permission_mode,
            ),
            source=expert_name,
        ):
            yield sse
        return

    # @mention 命中子代理：运行子代理，结果回注 Supervisor 合成
    if mention_target and mention_target[0] == "subagent":
        agent_name = mention_target[1]
        logger.info(
            "supervisor.mention_force_subagent",
            thread_id=thread_id,
            agent=agent_name,
        )
        yield make_sse_event("delegation", {
            "target": agent_name,
            "source": "work",
            "message": f"@mention 强制委派给 {agent_name} 子代理",
        })

        subagent_result = await _run_subagent_for_mention(
            agent_name, cleaned_message, thread_id, workspace_path
        )

        # 将子代理结果作为上下文注入 Supervisor
        cleaned_message = (
            f"用户通过 @{agent_name} 委派了子代理，子代理返回结果如下：\n\n"
            f"{subagent_result}\n\n"
            f"请基于以上结果为用户综合回复。原始用户消息：{cleaned_message}"
        )

    # ---- 2. 构建 Supervisor agent ----
    if is_full_trust:
        sandbox.set_full_trust(thread_id, True)
        logger.info("supervisor full_trust mode enabled", thread_id=thread_id)

    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": cleaned_message}]}

    try:
        agent_tools = _make_deep_tools(thread_id, workspace_path=workspace_path)
        mcp_tools, mcp_untrusted_names = await _load_mcp_tools()
        if mcp_tools:
            agent_tools.extend(mcp_tools)
            logger.info(
                "MCP tools merged into Supervisor",
                thread_id=thread_id,
                count=len(mcp_tools),
                untrusted=len(mcp_untrusted_names),
            )
        delegation_tools = make_delegation_tools(thread_id, workspace_path)
        all_tools = [*agent_tools, *delegation_tools]

        agent = await build_work_supervisor(
            thread_id,
            tools=all_tools,
            profile_prompt=profile_prompt,
            workspace_path=workspace_path,
        )
    except ValueError as exc:
        yield make_sse_event("error", f"LLM 不可用: {exc}")
        if is_full_trust:
            sandbox.set_full_trust(thread_id, False)
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("build_work_supervisor failed", thread_id=thread_id)
        yield make_sse_event("error", f"Supervisor 初始化失败: {exc}")
        if is_full_trust:
            sandbox.set_full_trust(thread_id, False)
        return

    # 运行时危险工具集合 = DANGEROUS_TOOLS 与已启用工具的交集 + MCP untrusted
    enabled_tool_names = {
        _TOOL_NAME_MAP.get(t.name, t.name) for t in agent_tools
    }
    runtime_dangerous = (DANGEROUS_TOOLS & enabled_tool_names) | mcp_untrusted_names

    # 防御性清理：修复 checkpoint 中残留的未配对 tool_calls
    await _inject_tool_error_messages(
        agent, config, "上次操作未正常完成，已自动清理状态"
    )
    inputs["messages"] = _sanitize_message_history(
        inputs["messages"], "上次操作未正常完成，已自动清理状态"
    )

    # ---- 3. 流式执行 + 中断/恢复循环 ----
    try:
        async for sse in _stream_agent_events(agent, inputs, config, source="work"):
            yield sse
    except Exception as exc:  # noqa: BLE001
        logger.exception("supervisor stream failed", thread_id=thread_id)
        await _inject_tool_error_messages(agent, config, f"Supervisor 执行失败: {exc}")
        yield make_sse_event("error", f"Supervisor 执行失败: {exc}")
        if is_full_trust:
            sandbox.set_full_trust(thread_id, False)
        return

    max_iterations = 50
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        # 暂停/恢复检查
        if await is_paused(thread_id):
            yield make_sse_event("paused", {})
            pause_event = await get_pause_event(thread_id)
            if await is_paused(thread_id):
                await pause_event.wait()
            yield make_sse_event("resumed", {})

        if not await _is_interrupted(agent, config):
            break

        pending_calls = await _get_pending_tool_calls(agent, config)
        if not pending_calls:
            logger.warning("interrupted but no pending tool calls", thread_id=thread_id)
            break

        # full_trust 模式：所有工具直接放行
        if is_full_trust:
            sandbox.clear_temp(thread_id)
            try:
                async for sse in _stream_agent_events(agent, None, config, source="work"):
                    yield sse
            except Exception as exc:  # noqa: BLE001
                logger.exception("supervisor resume failed", thread_id=thread_id)
                await _inject_tool_error_messages(agent, config, f"Supervisor 恢复失败: {exc}")
                yield make_sse_event("error", f"Supervisor 恢复失败: {exc}")
                sandbox.set_full_trust(thread_id, False)
                return
            continue

        # standard 模式：检查危险工具
        dangerous_calls = []
        for tc in pending_calls:
            name = tc.get("name", "")
            if name not in runtime_dangerous:
                continue
            paths = _extract_paths_from_tool_call(tc, workspace_path)
            if not paths:
                # 无路径参数的工具（如 shell_exec）：若已选工作区则自动放行
                if workspace_path and sandbox.is_path_authorized(
                    thread_id, workspace_path, writable=True
                ):
                    continue
                dangerous_calls.append(tc)
                continue
            all_authorized = all(
                sandbox.is_path_authorized(thread_id, p, writable=True)
                for p in paths
            )
            if not all_authorized:
                dangerous_calls.append(tc)

        if dangerous_calls:
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
                for tc in dangerous_calls:
                    await _inject_tool_error_for_call(
                        agent, config, tc, "用户拒绝执行危险操作"
                    )
                yield make_sse_event("error", "用户拒绝执行危险操作")
                sandbox.set_full_trust(thread_id, False)
                return

            logger.info(
                "supervisor approval granted",
                thread_id=thread_id,
                tool_count=len(dangerous_calls),
                tools=[tc.get("name") for tc in dangerous_calls],
            )
        else:
            # 非危险工具：检查只读 fs 工具是否越界
            extension_handled = await _handle_directory_extension(
                pending_calls, thread_id, sandbox
            )
            for evt in extension_handled.events:
                yield evt
            if extension_handled.denied:
                yield make_sse_event("error", "用户拒绝访问该目录")
                await _inject_tool_error_messages(agent, config, "用户拒绝访问该目录")
                sandbox.set_full_trust(thread_id, False)
                return
            if extension_handled.timed_out:
                yield make_sse_event("error", "目录授权等待被中断，操作未执行")
                await _inject_tool_error_messages(agent, config, "目录授权等待被中断，操作未执行")
                sandbox.set_full_trust(thread_id, False)
                return

        # 恢复执行
        try:
            async for sse in _stream_agent_events(agent, None, config, source="work"):
                yield sse
        except Exception as exc:  # noqa: BLE001
            logger.exception("supervisor resume failed", thread_id=thread_id)
            await _inject_tool_error_messages(agent, config, f"Supervisor 恢复失败: {exc}")
            yield make_sse_event("error", f"Supervisor 恢复失败: {exc}")
            sandbox.set_full_trust(thread_id, False)
            return

        sandbox.clear_temp(thread_id)

    if iteration >= max_iterations:
        logger.warning("supervisor hit max iterations", thread_id=thread_id)
        yield make_sse_event("error", "Supervisor 达到最大迭代上限")
        await _inject_tool_error_messages(agent, config, "Supervisor 达到最大迭代上限")
        sandbox.set_full_trust(thread_id, False)
        return

    if is_full_trust:
        sandbox.set_full_trust(thread_id, False)


async def _run_subagent_for_mention(
    agent_name: str,
    task: str,
    thread_id: str,
    workspace_path: str | None,
) -> str:
    """运行子代理并收集最终文本结果（用于 @mention 子代理委派）。"""
    parts: list[str] = []

    if agent_name == "rag":
        from app.subagents.rag_agent import run_rag_agent

        async for event in run_rag_agent(thread_id, task, history=None):
            if event.get("type") == "token":
                parts.append(event.get("content", ""))
    elif agent_name == "web":
        from app.subagents.web_agent import run_web_agent

        async for event in run_web_agent(thread_id, task, history=None):
            if event.get("type") == "token":
                parts.append(event.get("content", ""))
    else:
        # 自定义子代理
        from app.subagents.custom_agent import run_custom_agent

        async for event in run_custom_agent(
            agent_name, thread_id, task, history=None, workspace_path=workspace_path
        ):
            if event.get("type") == "token":
                parts.append(event.get("content", ""))

    return "".join(parts).strip()


async def _convert_expert_events(
    expert_stream: AsyncIterator[dict],
    source: str,
) -> AsyncIterator[dict]:
    """将 Expert 事件流转换为 SSE 事件（source 标识为 Expert）。

    Expert 内部使用与 DeepAgent 相同的事件格式（token/tool_call/tool_result/
    approval_request 等），此处透传并补充 source 字段。
    """
    async for event in expert_stream:
        # Expert 事件已经是 SSE 格式（make_sse_event），直接透传
        yield event
