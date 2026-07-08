"""coding 场景 Expert（代码专家 agent）实现。

基于 ``build_deep_agent`` 框架构建，与 DeepAgent 共享 streaming/approval 基础设施，
但有以下区别：
1. 使用 coding Expert 专用 system prompt（``_DEFAULT_CODING_EXPERT_SYSTEM_PROMPT``）
2. 额外注入 ``delegate_to_subagent`` 委派工具（不可委派其他 Expert）
3. 不包含 ``invoke_agent_team`` 工具（Expert 不可触发 AgentTeam）
4. SSE 事件 source 标识为 ``"coding"``

流程:
1. 构建 coding Expert agent（含 delegate_to_subagent + 完整代码工具集 + interrupt_before 审批）
2. ``astream_events`` 驱动图执行，流式产出 token / tool_call / tool_result 事件
3. 危险工具中断 → yield approval_request → 等待审批 → 恢复执行
4. 循环直至图完成
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator
from uuid import uuid4

from langchain_core.tools import tool

from app.approval import is_paused
from app.approval.state import get_pause_event
from app.config import BUILTIN_SUBAGENT_KEYS, get_settings
from app.deep.agent import build_deep_agent
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
from app.observability.logger import logger
from app.utils.security import get_sandbox
from app.utils.sse_events import make_sse_event

__all__ = [
    "build_coding_expert",
    "run_coding_expert",
]


def _build_subagent_list_description() -> str:
    """构建可用子代理列表描述（用于 delegate_to_subagent 工具描述）。"""
    settings = get_settings()
    lines: list[str] = []
    for name in sorted(BUILTIN_SUBAGENT_KEYS):
        cfg = settings.subagents.get(name)
        if cfg and cfg.enabled:
            lines.append(f"  - {name}: {cfg.trigger_description or name}")
    for key in sorted(settings.custom_subagents.keys()):
        cfg = settings.custom_subagents[key]
        if cfg.enabled:
            lines.append(f"  - {key}: {cfg.trigger_description or key}")
    if not lines:
        return "  (当前无可用子代理)"
    return "\n".join(lines)


def make_expert_delegation_tools(
    thread_id: str,
    workspace_path: str | None = None,
) -> list:
    """构建 Expert 的委派工具列表（仅 delegate_to_subagent）。

    Expert 不可委派其他 Expert（无 delegate_to_expert），也不可触发 AgentTeam
    （无 invoke_agent_team）。Expert 仅可调用 rag/web 子代理辅助任务。

    Args:
        thread_id: 会话 ID（传给子代理用于沙箱授权）。
        workspace_path: 当前工作区路径（传给子代理用于相对路径解析）。

    Returns:
        [delegate_to_subagent] 工具列表（仅一个元素）。
    """
    subagent_desc = (
        "委派任务给子代理（Subagent）。\n\n"
        f"可用子代理列表：\n{_build_subagent_list_description()}\n\n"
        "何时使用：\n"
        "- 知识库检索 → rag 子代理\n"
        "- 网页搜索 → web 子代理\n"
        "- 自定义子代理按需使用\n\n"
        "Args:\n"
        "    agent_name: 子代理名称（如 \"rag\" / \"web\"）\n"
        "    task: 要委派的任务描述\n\n"
        "Returns:\n"
        "    子代理的最终输出文本。"
    )

    @tool(description=subagent_desc)
    async def delegate_to_subagent(agent_name: str, task: str) -> str:
        """委派任务给子代理（Subagent）。"""
        settings = get_settings()
        subagents_cfg = settings.subagents
        custom_cfg = settings.custom_subagents

        # 内置子代理
        if agent_name in BUILTIN_SUBAGENT_KEYS:
            cfg = subagents_cfg.get(agent_name)
            if not cfg or not cfg.enabled:
                return f"错误：子代理 '{agent_name}' 已禁用"

            logger.info(
                "coding_expert.delegate_to_subagent",
                thread_id=thread_id,
                agent=agent_name,
                task_len=len(task),
            )

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
                return f"错误：内置子代理 '{agent_name}' 尚未实现"

            return "".join(parts).strip() or f"子代理 '{agent_name}' 未返回结果"

        # 自定义子代理
        if agent_name in custom_cfg:
            cfg = custom_cfg[agent_name]
            if not cfg.enabled:
                return f"错误：自定义子代理 '{agent_name}' 已禁用"

            logger.info(
                "coding_expert.delegate_to_custom_subagent",
                thread_id=thread_id,
                agent=agent_name,
                task_len=len(task),
            )

            from app.subagents.custom_agent import run_custom_agent

            parts2: list[str] = []
            async for event in run_custom_agent(
                agent_name, thread_id, task, history=None, workspace_path=workspace_path
            ):
                if event.get("type") == "token":
                    parts2.append(event.get("content", ""))
            return "".join(parts2).strip() or f"自定义子代理 '{agent_name}' 未返回结果"

        return f"错误：未知的子代理 '{agent_name}'。可用: rag, web, {', '.join(sorted(custom_cfg.keys()))}"

    return [delegate_to_subagent]


async def build_coding_expert(
    thread_id: str,
    tools: list | None = None,
    profile_prompt: str = "",
    checkpointer: Any = None,
    workspace_path: str | None = None,
) -> Any:
    """构造 coding 场景 Expert agent。

    基于 ``build_deep_agent`` 框架，使用 coding Expert 专用 system prompt。
    与 Supervisor 的区别：
    - 仅含 ``delegate_to_subagent`` 委派工具（不可委派其他 Expert）
    - 无 ``invoke_agent_team`` 工具（不可触发 AgentTeam）
    - source 标识为 ``"coding"``

    Args:
        thread_id: 会话 ID（用于工具沙箱授权绑定）。
        tools: 可选，已构建的工具列表。若未传则内部构建（标准工具集 + 委派工具）。
        profile_prompt: 可选，用户画像前缀，拼到 system prompt 前。
        checkpointer: 可选，共享的 LangGraph checkpointer。
        workspace_path: 可选当前工作区绝对路径。

    Returns:
        编译后的 CompiledStateGraph 实例。
    """
    from app.config.prompts.agent import _DEFAULT_CODING_EXPERT_SYSTEM_PROMPT

    settings = get_settings()
    expert_cfg = settings.agents.experts.get("coding")
    if expert_cfg is None:
        # 配置缺失时使用默认值
        from app.config.agents import ExpertSettings

        expert_cfg = ExpertSettings(
            system_prompt=_DEFAULT_CODING_EXPERT_SYSTEM_PROMPT,
            scenario="coding",
        )

    if tools is None:
        standard_tools = _make_deep_tools(thread_id, workspace_path=workspace_path)
        delegation_tools = make_expert_delegation_tools(thread_id, workspace_path)
        tools = [*standard_tools, *delegation_tools]

    # scene_prompt 透传给 build_deep_agent，覆盖默认 _DEEP_SYSTEM_PROMPT
    scene_prompt = expert_cfg.system_prompt or _DEFAULT_CODING_EXPERT_SYSTEM_PROMPT

    return await build_deep_agent(
        thread_id,
        tools=tools,
        profile_prompt=profile_prompt,
        checkpointer=checkpointer,
        scene_prompt=scene_prompt,
        workspace_path=workspace_path,
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

    tc_id = tool_call.get("id") or str(uuid4())
    tool_msg = ToolMessage(content=error_text, tool_call_id=tc_id)
    try:
        await agent.aupdate_state(config, {"messages": [tool_msg]})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "coding_expert.inject_tool_error_for_call failed",
            thread_id=config.get("configurable", {}).get("thread_id", ""),
            tool=tool_call.get("name"),
            error=str(exc),
        )


async def run_coding_expert(
    message: str,
    thread_id: str,
    profile_prompt: str = "",
    history: list | None = None,
    permission_mode: str = "standard",
    workspace_path: str | None = None,
) -> AsyncIterator[dict]:
    """运行 coding 场景 Expert，yield SSE 事件。

    流程:
    1. 构建 coding Expert agent（含 delegate_to_subagent + interrupt_before 审批）
    2. 流式执行，危险工具中断 → 审批 → 恢复，循环直至完成

    Args:
        message: 用户消息。
        thread_id: 会话 ID。
        profile_prompt: 用户画像前缀。
        history: 历史 messages 列表（已截断）。
        permission_mode: 权限模式，"standard" 或 "full_trust"。
        workspace_path: 可选当前工作区绝对路径。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    config: dict = {"configurable": {"thread_id": thread_id or "coding-default"}}
    sandbox = get_sandbox()
    is_full_trust = permission_mode == "full_trust"

    if is_full_trust:
        sandbox.set_full_trust(thread_id, True)
        logger.info("coding_expert full_trust mode enabled", thread_id=thread_id)

    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": message}]}

    # 构建 agent
    try:
        agent_tools = _make_deep_tools(thread_id, workspace_path=workspace_path)
        mcp_tools, mcp_untrusted_names = await _load_mcp_tools()
        if mcp_tools:
            agent_tools.extend(mcp_tools)
            logger.info(
                "MCP tools merged into coding Expert",
                thread_id=thread_id,
                count=len(mcp_tools),
                untrusted=len(mcp_untrusted_names),
            )
        delegation_tools = make_expert_delegation_tools(thread_id, workspace_path)
        all_tools = [*agent_tools, *delegation_tools]

        agent = await build_coding_expert(
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
        logger.exception("build_coding_expert failed", thread_id=thread_id)
        yield make_sse_event("error", f"Coding Expert 初始化失败: {exc}")
        if is_full_trust:
            sandbox.set_full_trust(thread_id, False)
        return

    # 运行时危险工具集合
    enabled_tool_names = {
        _TOOL_NAME_MAP.get(t.name, t.name) for t in agent_tools
    }
    runtime_dangerous = (DANGEROUS_TOOLS & enabled_tool_names) | mcp_untrusted_names

    # 防御性清理
    await _inject_tool_error_messages(
        agent, config, "上次操作未正常完成，已自动清理状态"
    )
    inputs["messages"] = _sanitize_message_history(
        inputs["messages"], "上次操作未正常完成，已自动清理状态"
    )

    # 只读工具集合（用于循环保护检测）
    _READONLY_TOOLS = {"read_file", "list_dir", "glob", "glob_files", "grep", "grep_files"}

    # 流式执行 + 中断/恢复循环
    try:
        async for sse in _stream_agent_events(agent, inputs, config, source="coding"):
            yield sse
    except Exception as exc:  # noqa: BLE001
        logger.exception("coding_expert stream failed", thread_id=thread_id)
        await _inject_tool_error_messages(agent, config, f"Coding Expert 执行失败: {exc}")
        yield make_sse_event("error", f"Coding Expert 执行失败: {exc}")
        if is_full_trust:
            sandbox.set_full_trust(thread_id, False)
        return

    max_iterations = 50
    iteration = 0
    # 连续只读工具调用计数（用于防过度探索保护）
    readonly_streak = 0
    # 只读工具调用阈值：超过此值认为 LLM 在过度探索，强制其基于已有信息回答
    readonly_streak_threshold = 10
    # 强制回答模式：注入错误消息迫使 LLM 停止工具调用、直接产出最终回复
    force_answer = False
    # 上一轮 tool_call 签名集合（用于检测完全相同的重复调用 = 真循环）
    prev_signatures: set[tuple[str, str]] = set()

    while iteration < max_iterations:
        iteration += 1

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

        # ---- 强制回答模式：注入错误并恢复，让 LLM 直接产出最终回复 ----
        # 触发条件：readonly_streak 超限 或 检测到完全相同的重复 tool_call。
        # 注入 ToolMessage 错误后恢复执行，LLM 看到错误后会基于已收集的信息回答。
        # 若 LLM 仍尝试调用工具，下一轮 force_answer=True 分支会继续注入错误。
        if force_answer:
            for tc in pending_calls:
                await _inject_tool_error_for_call(
                    agent, config, tc,
                    "已进入强制回答模式，请基于已收集的信息直接回答用户，不要再调用任何工具。"
                )
            try:
                async for sse in _stream_agent_events(agent, None, config, source="coding"):
                    yield sse
            except Exception as exc:  # noqa: BLE001
                logger.exception("coding_expert force-answer resume failed", thread_id=thread_id)
                await _inject_tool_error_messages(agent, config, f"Coding Expert 恢复失败: {exc}")
                yield make_sse_event("error", f"Coding Expert 恢复失败: {exc}")
                sandbox.set_full_trust(thread_id, False)
                return
            continue

        # ---- 循环保护：检测连续只读工具过度探索 ----
        pending_names = {tc.get("name", "") for tc in pending_calls}
        has_dangerous = bool(pending_names & runtime_dangerous)
        all_readonly = pending_names.issubset(_READONLY_TOOLS)
        if all_readonly and not has_dangerous:
            readonly_streak += 1
        else:
            readonly_streak = 0

        # ---- 真循环检测：连续两轮完全相同的 tool_call 签名 → 立即强制回答 ----
        current_signatures = {
            (tc.get("name", ""), json.dumps(tc.get("args", {}), sort_keys=True, ensure_ascii=False))
            for tc in pending_calls
        }
        repeated = current_signatures & prev_signatures
        prev_signatures = current_signatures

        trigger_force_answer = False
        if repeated:
            logger.warning(
                "coding_expert duplicate tool calls detected, forcing answer",
                thread_id=thread_id,
                repeated=sorted(repeated),
            )
            trigger_force_answer = True
        elif readonly_streak >= readonly_streak_threshold:
            logger.warning(
                "coding_expert readonly streak exceeded, forcing answer",
                thread_id=thread_id,
                readonly_streak=readonly_streak,
                pending_tools=sorted(pending_names),
            )
            trigger_force_answer = True

        if trigger_force_answer:
            # 注入错误消息告诉 LLM 停止探索，然后恢复执行让其产出最终回复
            for tc in pending_calls:
                await _inject_tool_error_for_call(
                    agent, config, tc,
                    "已获取足够信息，请基于已有结果直接回答用户，不要继续调用工具。"
                )
            yield make_sse_event(
                "reasoning",
                {"content": "已收集足够上下文，正在基于已有信息生成回复...", "source": "coding"},
            )
            force_answer = True
            try:
                async for sse in _stream_agent_events(agent, None, config, source="coding"):
                    yield sse
            except Exception as exc:  # noqa: BLE001
                logger.exception("coding_expert force-answer resume failed", thread_id=thread_id)
                await _inject_tool_error_messages(agent, config, f"Coding Expert 恢复失败: {exc}")
                yield make_sse_event("error", f"Coding Expert 恢复失败: {exc}")
                sandbox.set_full_trust(thread_id, False)
                return
            continue

        if is_full_trust:
            sandbox.clear_temp(thread_id)
            try:
                async for sse in _stream_agent_events(agent, None, config, source="coding"):
                    yield sse
            except Exception as exc:  # noqa: BLE001
                logger.exception("coding_expert resume failed", thread_id=thread_id)
                await _inject_tool_error_messages(agent, config, f"Coding Expert 恢复失败: {exc}")
                yield make_sse_event("error", f"Coding Expert 恢复失败: {exc}")
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
                "coding_expert approval granted",
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
            async for sse in _stream_agent_events(agent, None, config, source="coding"):
                yield sse
        except Exception as exc:  # noqa: BLE001
            logger.exception("coding_expert resume failed", thread_id=thread_id)
            await _inject_tool_error_messages(agent, config, f"Coding Expert 恢复失败: {exc}")
            yield make_sse_event("error", f"Coding Expert 恢复失败: {exc}")
            sandbox.set_full_trust(thread_id, False)
            return

        sandbox.clear_temp(thread_id)

    if iteration >= max_iterations:
        logger.warning("coding_expert hit max iterations", thread_id=thread_id)
        yield make_sse_event("error", "Coding Expert 达到最大迭代上限")
        await _inject_tool_error_messages(agent, config, "Coding Expert 达到最大迭代上限")
        sandbox.set_full_trust(thread_id, False)
        return

    if is_full_trust:
        sandbox.set_full_trust(thread_id, False)
