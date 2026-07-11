"""DeepAgent / Supervisor / Expert 公共审批执行层。

把原本分散在 ``deepagent/agent.py``、``agents/supervisor/work_supervisor.py``、
``agents/expert/coding.py`` 中的审批循环提取为统一函数
``run_agent_with_approval``，供所有 ReAct 路径复用。
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, AsyncIterator, Awaitable, Callable

from loguru import logger
from langgraph.types import Command

from app.security.approval import (
    is_aborted,
    is_paused,
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


def _make_hitl_resume_decisions(pending_calls: list[dict], decision_type: str = "approve", message: str = "") -> Command:
    """生成 HumanInTheLoopMiddleware 期望的 resume Command。

    LangGraph 1.x 的 ``interrupt()`` 必须用 ``Command(resume=...)`` 恢复，
    传 ``None`` 不会消费 interrupt，导致 ``state.interrupts`` 一直存在，
    触发 stuck state 检测（root cause: 执行流程中断状态未正常解除）。

    Args:
        pending_calls: 待处理工具调用列表。
        decision_type: 决策类型，"approve" 或 "reject"。
        message: reject 时的自定义消息（可选）。

    Returns:
        ``Command(resume={"decisions": [...]})`` 供 ``agent.astream`` 使用。
    """
    decisions: list[dict[str, Any]] = []
    for _ in pending_calls:
        d: dict[str, Any] = {"type": decision_type}
        if message and decision_type == "reject":
            d["message"] = message
        decisions.append(d)
    return Command(resume={"decisions": decisions})


async def _stream_default(
    agent: Any, inputs: Any, config: dict, source: str = "deep", **kwargs: Any
) -> AsyncIterator[dict[str, str]]:
    """默认 stream_fn：委托到 ``app.deepagent.streaming._stream_agent_events``。

    ``**kwargs`` 透传 ``seen_signatures`` 等跨 resume 去重参数，避免
    ``_stream`` 包装器因签名不匹配触发 TypeError 降级，导致去重集合每次重建为空。
    """
    from app.deepagent.streaming import _stream_agent_events

    async for sse in _stream_agent_events(agent, inputs, config, source=source, **kwargs):
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
    from app.observability.trace import bind_trace, current_trace_id
    from app.deepagent.context import current_parent_thread_id

    # 显式绑定 trace_id：LangGraph 内部节点/子协程不会自动继承外层 ContextVar
    _trace_id = current_trace_id() or ""
    if _trace_id:
        bind_trace(_trace_id)  # 设置当前协程的 ContextVar

    # 设置 parent_thread_id contextvar，供 AuthorizedLocalShellBackend._check_auth
    # 读取（Team 子代理通过 AuthorizedLocalShellBackend 执行 fs 操作时继承父线程授权）。
    # 与 bind_trace 同样在入口处设置；每次调用都会覆盖上一次的值。
    current_parent_thread_id.set(parent_thread_id)

    # 跨 stream 调用共享的"已 yield 消息签名"集合（避免 astream resume
    # 时重发历史消息被重复 yield，root cause: trace=64851677fced422c）。
    # 包装器自动向支持 seen_signatures 关键字参数的 stream_fn 注入；
    # 不支持的自定义 stream_fn（测试桩）自动降级为不带参数调用。
    _seen_signatures: set[str] = set()
    _base_stream = stream_fn or _stream_default

    async def _stream(agent: Any, inputs: Any, config: dict, source: str) -> AsyncIterator[dict[str, str]]:
        """包装原 stream_fn 注入共享 seen_signatures。

        TypeError 仅在函数调用阶段捕获：async generator function 被传入不支持的
        kwarg 时会在调用时（而非迭代时）抛 TypeError。若把整个 ``async for``
        包进 try，迭代期间的 TypeError 会被误捕获并重新迭代，导致重复事件。
        """
        try:
            gen = _base_stream(
                agent, inputs, config, source, seen_signatures=_seen_signatures
            )
        except TypeError:
            # 自定义 stream_fn（测试桩）不支持 seen_signatures kwarg，降级调用
            gen = _base_stream(agent, inputs, config, source)
        async for sse in gen:
            yield sse

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

    # 循环保护：记录最近几次 pending_calls 以检测重复模式。
    # 重要：仅在 LLM 真正生成新消息后才记录。astream resume 时会重放历史 state，
    # 同一批 pending_calls 会被重复提出，但不应触发"LLM 循环"判定
    # （root cause: trace=64851677fced422c）。
    _recent_calls_history: list[list[dict]] = []
    _REPEAT_DETECTION_WINDOW = 3  # 最近 3 次迭代
    _REPEAT_THRESHOLD = 2  # 有 2 次重复即判定为循环
    # 跟踪已 yield 后的 state.values.messages 数量，作为"是否有新消息"的基线。
    # 初始 stream 后初始化为初始 state 长度；后续每次 stream 完成后更新。
    _yielded_msg_count: int = -1

    async def _state_msg_count() -> int:
        """读取当前 state 的 messages 数量（用于增量判断）。失败时返回 -1 表示未知。

        兼容 MagicMock / AsyncMock 测试场景：state.values.get(...) 在 AsyncMock 下
        会返回 coroutine，需 inspect.isawaitable 检测后 await。
        """
        try:
            state = await agent.aget_state(config)
            if not state or not getattr(state, "values", None):
                return -1
            messages = state.values.get("messages", [])
            # 防御性：AsyncMock 会把 .get 当 async 调用，返回 coroutine
            if inspect.isawaitable(messages):
                messages = await messages
            return len(messages or [])
        except Exception:  # noqa: BLE001 — 读取失败不应阻塞主流程
            return -1

    # 1. 初始流式执行
    try:
        async for sse in _stream(agent, inputs, config, source):
            yield await _forward(sse)
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent initial stream failed", thread_id=thread_id, source=source)
        await _inject_msgs(agent, config, f"执行失败: {exc}")
        yield await _forward(make_error_event( f"执行失败: {exc}"))
        # 消费可能残留的 HITL interrupt，避免下次调用 stuck
        try:
            if await _is_int(agent, config):
                pending = await _get_calls(agent, config)
                if pending:
                    async for _ in agent.astream(
                        _make_hitl_resume_decisions(
                            pending, decision_type="reject", message="执行失败"
                        ),
                        config=config,
                        stream_mode="values",
                    ):
                        pass
        except Exception:  # noqa: BLE001
            pass
        return
    # 初始化已 yield 基线：取初始 stream 后的 state.messages 数量。
    # 后续 stream 完成时刷新；重复检测用此判断"是否有新消息生成"。
    _yielded_msg_count = await _state_msg_count()

    # 2. 中断/恢复循环
    while iteration < max_iterations:
        iteration += 1

        # 最小延迟：防止 CPU 占满和日志风暴
        await asyncio.sleep(0.05)

        # 暂停检查：若已暂停，yield paused 事件后结束当前流
        # 恢复由前端重新发送消息触发，LangGraph 从 checkpoint 自动恢复
        if await is_paused(thread_id):
            yield await _forward(make_sse_event("paused", {}))
            # 消费可能残留的 HITL interrupt，避免下次调用 stuck
            try:
                if await _is_int(agent, config):
                    pending = await _get_calls(agent, config)
                    if pending:
                        async for _ in agent.astream(
                            _make_hitl_resume_decisions(
                                pending, decision_type="reject", message="会话已暂停"
                            ),
                            config=config,
                            stream_mode="values",
                        ):
                            pass
            except Exception:  # noqa: BLE001
                pass
            # 结束 SSE 流，不 wait_for_resume；恢复走重新发送消息路径
            return

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

        # 重复工具调用检测。
        # 关键：仅在 LLM 真正生成新 AIMessage 时才记录（current_msg_count > _yielded_msg_count）。
        # astream resume 时会重放历史 state，同一批 pending_calls 被重复提出，
        # 但因为 messages 数量未增长，属于重放而非 LLM 循环，不应触发判定
        # （root cause: trace=64851677fced422c：msg_count 始终为 7，但 pending_calls 一致）。
        current_msg_count = await _state_msg_count()
        _no_new_messages = (
            _yielded_msg_count >= 0
            and current_msg_count >= 0
            and current_msg_count <= _yielded_msg_count
        )
        if _no_new_messages:
            logger.debug(
                "repeat detection skipped: no new messages since last stream",
                thread_id=thread_id,
                current_msg_count=current_msg_count,
                yielded_msg_count=_yielded_msg_count,
                source=source,
            )
            # 防御：虽然 msg_count 未增长，但如果 pending_calls 与上次完全相同，
            # 且已连续多次出现，则可能是 stuck state 导致的重复（而非正常重放）。
            # 此时仍记录并检测，避免 stuck state 场景下重复检测完全失效
            # （root cause: trace=e629ab945dad467c）。
            if _recent_calls_history and pending_calls:
                last_calls = _recent_calls_history[-1]
                if (
                    len(pending_calls) == len(last_calls)
                    and all(
                        p.get("name") == prev.get("name")
                        and p.get("args", {}) == prev.get("args", {})
                        for p, prev in zip(pending_calls, last_calls)
                    )
                ):
                    _recent_calls_history.append(pending_calls)
                    if len(_recent_calls_history) > _REPEAT_DETECTION_WINDOW:
                        _recent_calls_history.pop(0)
                    # 不在这里触发停止，让 stuck state 检测在 resume 后处理
        else:
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
            pre_resume_msg_count = await _state_msg_count()
            try:
                async for sse in _stream(
                    agent,
                    _make_hitl_resume_decisions(pending_calls, decision_type="approve"),
                    config,
                    source,
                ):
                    yield await _forward(sse)
            except Exception as exc:  # noqa: BLE001
                logger.exception("agent resume failed", thread_id=thread_id)
                await _inject_msgs(agent, config, f"恢复失败: {exc}")
                # 异常后尝试消费可能残留的 interrupt，避免 stuck state
                try:
                    async for _ in agent.astream(
                        _make_hitl_resume_decisions(
                            pending_calls, decision_type="reject", message=f"恢复失败: {exc}"
                        ),
                        config=config,
                        stream_mode="values",
                    ):
                        pass
                except Exception:  # noqa: BLE001
                    pass
                yield await _forward(make_error_event( f"恢复失败: {exc}"))
                return
            # 刷新已 yield 基线（root cause: trace=64851677fced422c）
            _yielded_msg_count = await _state_msg_count()
            # 防御：检测 resume 后 state 是否推进（root cause: trace=e629ab945dad467c）
            # 注意：任一值为 -1（state 读取失败）时跳过检测，避免误判
            if (
                pre_resume_msg_count >= 0
                and _yielded_msg_count >= 0
                and _yielded_msg_count <= pre_resume_msg_count
            ):
                logger.warning(
                    "agent resume did not advance state, possible stuck state in full_trust",
                    thread_id=thread_id,
                    source=source,
                    pre_resume_msg_count=pre_resume_msg_count,
                    post_resume_msg_count=_yielded_msg_count,
                    iteration=iteration,
                )
                # 强制注入错误消息以尝试解除 stuck state
                await _inject_msgs(
                    agent,
                    config,
                    "工具执行后状态未正常推进，已强制终止。请重试或联系支持。",
                )
                # 消费可能残留的 interrupt
                try:
                    async for _ in agent.astream(
                        _make_hitl_resume_decisions(
                            pending_calls, decision_type="reject", message="状态未推进，强制终止"
                        ),
                        config=config,
                        stream_mode="values",
                    ):
                        pass
                except Exception:  # noqa: BLE001
                    pass
                yield await _forward(
                    make_error_event(
                        "工具执行后状态未正常推进，已强制终止。请重试或联系支持。"
                    )
                )
                return
            continue

        # standard 模式：危险工具判定
        decision = None
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
                # 用户拒绝：先注入 ToolMessage 错误，再用 Command(resume=...) 消费
                # HumanInTheLoopMiddleware 的 interrupt，避免 interrupt 残留导致
                # 下次调用 stuck（root cause: 执行流程中断状态未正常解除）。
                for tc in dangerous_calls:
                    await _inject_call(agent, config, tc, "用户拒绝执行危险操作")
                yield await _forward(make_error_event( "用户拒绝执行危险操作"))
                # 消费 HITL interrupt，避免 stuck state
                # 注意：必须用 pending_calls（全部待处理调用）而非仅 dangerous_calls，
                # 否则非危险工具的 HITL interrupt 不会被消费，导致下次调用 stuck
                try:
                    async for _ in agent.astream(
                        _make_hitl_resume_decisions(
                            pending_calls, decision_type="reject", message="用户拒绝执行危险操作"
                        ),
                        config=config,
                        stream_mode="values",
                    ):
                        pass
                except Exception:  # noqa: BLE001
                    pass
                return

            logger.info(
                "agent approval granted",
                thread_id=thread_id,
                tool_count=len(dangerous_calls),
                tools=[tc.get("name") for tc in dangerous_calls],
                source=source,
            )

        # 所有工具：检查是否越界（含只读工具、execute、cli_execute 等）
        # 如果 dangerous_tool 审批已通过，将决策传给 directory_extension 避免重复等待
        extension_handled: _ExtensionResult = await _handle_directory_extension(
            pending_calls,
            thread_id,
            _sandbox,
            workspace_path=workspace_path,
            parent_thread_id=parent_thread_id,
            existing_decision=decision if dangerous_calls else None,
        )
        for evt in extension_handled.events:
            yield await _forward(evt)
        if extension_handled.denied:
            yield await _forward(make_error_event( "用户拒绝访问该目录"))
            await _inject_msgs(agent, config, "用户拒绝访问该目录")
            # 消费 HITL interrupt，避免 stuck state
            try:
                async for _ in agent.astream(
                    _make_hitl_resume_decisions(
                        pending_calls, decision_type="reject", message="用户拒绝访问该目录"
                    ),
                    config=config,
                    stream_mode="values",
                ):
                    pass
            except Exception:  # noqa: BLE001
                pass
            return
        if extension_handled.timed_out:
            yield await _forward(make_error_event( "目录授权等待被中断，操作未执行"))
            await _inject_msgs(agent, config, "目录授权等待被中断，操作未执行")
            # 消费 HITL interrupt，避免 stuck state
            try:
                async for _ in agent.astream(
                    _make_hitl_resume_decisions(
                        pending_calls, decision_type="reject", message="目录授权等待被中断，操作未执行"
                    ),
                    config=config,
                    stream_mode="values",
                ):
                    pass
            except Exception:  # noqa: BLE001
                pass
            return

        # 恢复执行
        logger.info(
            "agent resume execution",
            thread_id=thread_id,
            source=source,
            pending_tools=[tc.get("name") for tc in pending_calls],
        )
        try:
            async for sse in _stream(
                agent,
                _make_hitl_resume_decisions(pending_calls, decision_type="approve"),
                config,
                source,
            ):
                yield await _forward(sse)
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent resume failed", thread_id=thread_id)
            await _inject_msgs(agent, config, f"恢复失败: {exc}")
            yield await _forward(make_error_event( f"恢复失败: {exc}"))
            # 消费残留 HITL interrupt，避免下次调用 stuck
            # （仿照 full_trust 模式异常处理，pending_calls 在此作用域可用）
            try:
                if await _is_int(agent, config):
                    async for _ in agent.astream(
                        _make_hitl_resume_decisions(
                            pending_calls,
                            decision_type="reject",
                            message="执行异常",
                        ),
                        config=config,
                        stream_mode="values",
                    ):
                        pass
            except Exception:  # noqa: BLE001
                pass
            return

        logger.info(
            "agent resume completed",
            thread_id=thread_id,
            source=source,
        )

        # 刷新已 yield 基线（root cause: trace=64851677fced422c）。
        # 每次 stream 完成后记录最新 state.messages 数量，重复检测据此判断
        # 是否有新 LLM 消息生成（仅在 current_count > _yielded_msg_count 时
        # 才进入循环检测，避免 astream resume 重放历史误触发）。
        _yielded_msg_count = await _state_msg_count()

        # 防御性检查：若 resume 后 state 未推进（msg_count 未增长）
        # 且仍被中断，说明 LangGraph interrupt 可能 stuck（如
        # HumanInTheLoopMiddleware 的 interrupt 未被正确消费）。
        # 此时强制 break 避免无限循环（root cause: trace=7c742e6f60dc4d96）。
        # 注意：msg_count < 0 表示无法读取 state（如测试 mock），跳过此检查。
        # current_msg_count = 本轮开始时的值（旧），_yielded_msg_count = resume 后的值（新）。
        # state 推进 → _yielded > current → 条件 False；state 未推进 → _yielded <= current → 条件 True。
        _state_stalled = (
            current_msg_count >= 0
            and _yielded_msg_count >= 0
            and _yielded_msg_count <= current_msg_count
        )
        if _state_stalled and await _is_int(agent, config):
            logger.warning(
                "agent resume did not clear interrupt, possible stuck state",
                thread_id=thread_id,
                source=source,
                iteration=iteration,
            )
            await _inject_msgs(
                agent,
                config,
                "执行流程中断状态未正常解除，已强制终止。请重试或联系支持。",
            )
            yield await _forward(
                make_error_event(
                    "执行流程中断状态未正常解除，已强制终止。请重试或联系支持。"
                )
            )
            return

        await _sandbox.clear_temp(thread_id)

    if iteration >= max_iterations:
        logger.warning("agent hit max iterations", thread_id=thread_id)
        yield await _forward(make_error_event( "达到最大迭代上限"))
        await _inject_msgs(agent, config, "达到最大迭代上限")
        return
