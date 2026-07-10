"""DeepAgent / Supervisor / Expert 公共审批执行层。

把原本分散在 ``deepagent/agent.py``、``agents/supervisor/work_supervisor.py``、
``agents/expert/coding.py`` 中的审批循环提取为统一函数
``run_agent_with_approval``，供所有 ReAct 路径复用。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Awaitable, Callable

from loguru import logger

from app.security.approval import (
    is_aborted,
    is_paused,
    wait_for_resume,
)
from app.security.approval.flow import (
    _APPROVAL_POLL_INTERVAL,
    _READONLY_TOOLS,
    _ExtensionResult,
    _await_approval,
    _extract_paths_from_tool_call,
    _handle_directory_extension,
    _inject_tool_error_for_call,
    _make_approval_event,
    _resolve_max_wait,
)
from app.sse.events import make_error_event, make_sse_event

__all__ = ["run_agent_with_approval"]


async def _stream_default(
    agent: Any, inputs: Any, config: dict, source: str = "deep"
) -> AsyncIterator[dict[str, str]]:
    """默认 stream_fn：委托到 ``app.deepagent.streaming._stream_agent_events``。"""
    from app.deepagent.streaming import _stream_agent_events

    async for sse in _stream_agent_events(agent, inputs, config, source=source):
        yield sse


async def _is_interrupted(agent: Any, config: dict) -> bool:
    """检查 agent 是否在 interrupt 处暂停。

    LangGraph 1.x ``StateSnapshot`` 含 ``interrupts`` 字段（``tuple[Interrupt, ...]``），
    deepagents 0.6.x 通过 ``HumanInTheLoopMiddleware.after_model`` 触发 interrupt，
    被中断的 task 节点名为 ``"HumanInTheLoopMiddleware.after_model"``（不是 ``"tools"``）。
    旧判定 ``"tools" in state.next`` 因此永远 False → approval_runner 提前 break → SSE
    无审批事件就关闭（trace 47ba5000ac804eec 复现）。修复：优先检查 ``state.interrupts``，
    保留 ``"tools" in state.next`` 作为 ``interrupt_before=["tools"]`` 旧机制兼容。
    """
    state = await agent.aget_state(config)
    if not state:
        return False
    # 主判定：LangGraph 1.x HITL interrupt 由 HumanInTheLoopMiddleware 触发，
    # StateSnapshot.interrupts 直接给出待处理的 Interrupt 对象列表
    if getattr(state, "interrupts", None):
        return True
    # 向后兼容 interrupt_before=["tools"] 旧机制（已不在项目代码使用）
    if state.next and "tools" in state.next:
        return True
    return False


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


async def _inject_tool_error_messages(
    agent: Any, config: dict, error_text: str
) -> None:
    """为所有待执行 tool_call 注入 ToolMessage 错误。"""
    pending = await _get_pending_tool_calls(agent, config)
    for tc in pending:
        await _inject_tool_error_for_call(agent, config, tc, error_text)


async def run_agent_with_approval(
    agent: Any,
    config: dict,
    *,
    thread_id: str,
    workspace_path: str | None,
    permission_mode: str,
    runtime_dangerous: set[str],
    source: str,
    inputs: dict,
    sandbox: Any | None = None,
    parent_thread_id: str | None = None,
    stream_fn: Callable[..., AsyncIterator[dict[str, str]]] | None = None,
    is_interrupted_fn: Callable[[Any, dict], Awaitable[bool]] | None = None,
    get_pending_calls_fn: Callable[[Any, dict], Awaitable[list[dict]]] | None = None,
    inject_tool_error_for_call_fn: Callable[[Any, dict, dict, str], Awaitable[None]] | None = None,
    inject_tool_error_messages_fn: Callable[[Any, dict, str], Awaitable[None]] | None = None,
    yield_event: Callable[[dict], Awaitable[None]] | None = None,
    readonly_streak_threshold: int = 0,
    max_iterations: int = 100,
) -> AsyncIterator[dict[str, str]]:
    """统一的 agent 审批执行循环。

    流程:
    1. 初始流式执行，产出 token/tool_call/tool_result 事件
    2. while 循环检测中断（interrupt_on）
       - 暂停/恢复检查
       - full_trust 模式跳过审批直接恢复
       - 只读工具循环保护
       - 危险工具审批（directory_extension 或 dangerous_tool）
       - 恢复执行
    3. 达到最大迭代次数或图完成后退出

    Args:
        agent: 已编译的 LangGraph / deep agent。
        config: 含 ``configurable.thread_id`` 的运行配置。
        thread_id: 会话 ID。
        workspace_path: 当前工作区绝对路径。
        permission_mode: ``"standard"`` 或 ``"full_trust"``。
        runtime_dangerous: 运行时危险工具名集合。
        source: SSE 事件 source 标识。
        inputs: 初始输入，形如 ``{"messages": [...]}``。
        sandbox: ``SessionSandbox`` 实例。None 时自动 ``get_sandbox()``。
        parent_thread_id: 父 thread_id（Team 模式授权继承）。
        stream_fn: 流式事件生成函数，默认 ``_stream_agent_events``。
        is_interrupted_fn: 中断检测函数。
        get_pending_calls_fn: 提取 pending tool_calls 函数。
        inject_tool_error_for_call_fn: 单条 tool_call 错误注入函数。
        inject_tool_error_messages_fn: 批量错误注入函数。
        yield_event: 可选的异步回调，每 yield 一个事件时同步调用（用于日志/观察）。
        readonly_streak_threshold: 只读工具连续调用阈值（0 禁用）。
        max_iterations: 最大迭代次数。

    Yields:
        SSE 事件 dict: ``{event: str, data: str}``
    """
    from app.sandbox import get_sandbox

    _stream = stream_fn or _stream_default
    _is_int = is_interrupted_fn or _is_interrupted
    _get_calls = get_pending_calls_fn or _get_pending_tool_calls
    _inject_call = inject_tool_error_for_call_fn or _inject_tool_error_for_call
    _inject_msgs = inject_tool_error_messages_fn or _inject_tool_error_messages
    _sandbox = sandbox or get_sandbox()

    async def _forward(event: dict[str, str]) -> dict[str, str]:
        """yield 前同步调用 yield_event 回调（用于日志/观察/统计）。"""
        if yield_event is not None:
            await yield_event(event)
        return event

    is_full_trust = permission_mode == "full_trust"
    iteration = 0
    readonly_streak = 0

    # 循环保护：记录最近几次 pending_calls 以检测重复模式
    _recent_calls_history: list[list[dict]] = []
    _REPEAT_DETECTION_WINDOW = 3  # 最近 3 次迭代
    _REPEAT_THRESHOLD = 2  # 有 2 次重复即判定为循环

    # 1. 初始流式执行
    try:
        async for sse in _stream(agent, inputs, config, source):
            yield await _forward(sse)
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent initial stream failed", thread_id=thread_id, source=source)
        await _inject_msgs(agent, config, f"执行失败: {exc}")
        yield await _forward(make_error_event( f"执行失败: {exc}"))
        return

    # 2. 中断/恢复循环
    while iteration < max_iterations:
        iteration += 1

        # 最小延迟：防止 CPU 占满和日志风暴
        await asyncio.sleep(0.05)

        # 暂停/恢复检查
        if await is_paused(thread_id):
            yield await _forward(make_sse_event("paused", {}))
            await wait_for_resume(thread_id, timeout=_resolve_max_wait())
            # 恢复后继续执行；不发送已废弃的 resumed 事件

        # abort 检查
        if await is_aborted(thread_id):
            yield await _forward(make_error_event( "操作已中止"))
            return

        if not await _is_int(agent, config):
            break

        pending_calls = await _get_calls(agent, config)
        if not pending_calls:
            logger.warning("interrupted but no pending tool calls", thread_id=thread_id)
            break

        # 重复工具调用检测
        _recent_calls_history.append(pending_calls)
        if len(_recent_calls_history) > _REPEAT_DETECTION_WINDOW:
            _recent_calls_history.pop(0)
        if len(_recent_calls_history) >= _REPEAT_DETECTION_WINDOW:
            # 提取每次的工具名称+参数签名
            def _call_signature(calls: list[dict]) -> str:
                return "|".join(
                    f"{c.get('name','')}:{json.dumps(c.get('args',{}),sort_keys=True,separators=(',',':'))}"
                    for c in calls
                )
            signatures = [_call_signature(c) for c in _recent_calls_history]
            # 检查最近 N 次是否有重复
            repeat_count = sum(1 for i in range(1, len(signatures)) if signatures[i] == signatures[i - 1])
            if repeat_count >= _REPEAT_THRESHOLD:
                logger.warning(
                    "agent stuck in repeating tool-call loop, forcing stop",
                    thread_id=thread_id,
                    signatures=signatures,
                    repeat_count=repeat_count,
                )
                await _inject_msgs(agent, config, "工具调用陷入重复循环，已强制停止。请简化您的请求或明确指定目标路径。")
                yield await _forward(
                    make_sse_event(
                        "error",
                        "工具调用陷入重复循环，已强制停止。请简化您的请求或明确指定目标路径。",
                    )
                )
                return

        pending_names = {tc.get("name", "") for tc in pending_calls}
        has_dangerous = bool(pending_names & runtime_dangerous)
        all_readonly = pending_names.issubset(_READONLY_TOOLS)

        # 只读工具循环保护
        if readonly_streak_threshold > 0:
            if all_readonly and not has_dangerous:
                readonly_streak += 1
            else:
                readonly_streak = 0

            if readonly_streak >= readonly_streak_threshold:
                logger.warning(
                    "agent readonly streak exceeded, forcing stop",
                    thread_id=thread_id,
                    readonly_streak=readonly_streak,
                    pending_tools=list(pending_names),
                )
                for tc in pending_calls:
                    await _inject_call(
                        agent,
                        config,
                        tc,
                        "已获取足够信息，请基于已有结果直接回答用户，不要继续调用工具。",
                    )
                yield await _forward(
                    make_sse_event(
                        "error",
                        "工具调用次数过多，已强制停止。请简化您的请求或明确指定目标路径。",
                    )
                )
                return

        # full_trust 模式：直接恢复
        if is_full_trust:
            await _sandbox.clear_temp(thread_id)
            try:
                async for sse in _stream(agent, None, config, source):
                    yield await _forward(sse)
            except Exception as exc:  # noqa: BLE001
                logger.exception("agent resume failed", thread_id=thread_id)
                await _inject_msgs(agent, config, f"恢复失败: {exc}")
                yield await _forward(make_error_event( f"恢复失败: {exc}"))
                return
            continue

        # standard 模式：危险工具判定
        dangerous_calls: list[dict] = []
        for tc in pending_calls:
            name = tc.get("name", "")
            if name not in runtime_dangerous:
                continue

            paths = _extract_paths_from_tool_call(tc, workspace_path)
            if not paths:
                # 无路径参数的危险工具：若已选工作区且授权则自动放行
                if workspace_path and await _sandbox.is_path_authorized(
                    thread_id,
                    workspace_path,
                    writable=True,
                    base=workspace_path,
                    parent_thread_id=parent_thread_id,
                ):
                    continue
                dangerous_calls.append(tc)
                continue

            path_checks = await asyncio.gather(
                *[
                    _sandbox.is_path_authorized(
                        thread_id,
                        p,
                        writable=True,
                        base=workspace_path,
                        parent_thread_id=parent_thread_id,
                    )
                    for p in paths
                ]
            )
            if not all(path_checks):
                dangerous_calls.append(tc)

        if dangerous_calls:
            for tc in dangerous_calls:
                yield await _forward(_make_approval_event(tc, thread_id, kind="dangerous_tool"))

            decision = await _await_approval(
                thread_id,
                poll_interval=_APPROVAL_POLL_INTERVAL,
                max_wait=_resolve_max_wait(),
            )

            if decision is None or not decision.approved:
                for tc in dangerous_calls:
                    await _inject_call(agent, config, tc, "用户拒绝执行危险操作")
                yield await _forward(make_error_event( "用户拒绝执行危险操作"))
                return

            logger.info(
                "agent approval granted",
                thread_id=thread_id,
                tool_count=len(dangerous_calls),
                tools=[tc.get("name") for tc in dangerous_calls],
                source=source,
            )

        # 所有工具：检查是否越界（含只读工具、execute、cli_execute 等）
        extension_handled: _ExtensionResult = await _handle_directory_extension(
            pending_calls,
            thread_id,
            _sandbox,
            workspace_path=workspace_path,
            parent_thread_id=parent_thread_id,
        )
        for evt in extension_handled.events:
            yield await _forward(evt)
        if extension_handled.denied:
            yield await _forward(make_error_event( "用户拒绝访问该目录"))
            await _inject_msgs(agent, config, "用户拒绝访问该目录")
            return
        if extension_handled.timed_out:
            yield await _forward(make_error_event( "目录授权等待被中断，操作未执行"))
            await _inject_msgs(agent, config, "目录授权等待被中断，操作未执行")
            return

        # 恢复执行
        try:
            async for sse in _stream(agent, None, config, source):
                yield await _forward(sse)
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent resume failed", thread_id=thread_id)
            await _inject_msgs(agent, config, f"恢复失败: {exc}")
            yield await _forward(make_error_event( f"恢复失败: {exc}"))
            return

        await _sandbox.clear_temp(thread_id)

    if iteration >= max_iterations:
        logger.warning("agent hit max iterations", thread_id=thread_id)
        yield await _forward(make_error_event( "达到最大迭代上限"))
        await _inject_msgs(agent, config, "达到最大迭代上限")
        return
