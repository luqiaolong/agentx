"""DeepAgent / 场景化智能体公共审批执行层。

把原本分散在 ``deepagent/agent.py``、``scenarios/work/agent.py``、
``scenarios/coding/agent.py`` 中的审批循环提取为统一函数
``run_agent_with_approval``，供所有 ReAct 路径复用。

OpenSpec Decision 6 decomposition:
- ``hitl.py``: LangGraph interrupt/resume mechanics (interrupted-state
  detection, pending-call extraction, aligned Command construction, interrupt
  consumption, message-count progress checks).
- ``approval_session.py``: ``ApprovalSession`` state machine with run
  dependencies, stream state, repeat history, stall counters, terminal status.
- ``approval_runner.py`` (this file): public thin facade — dependency
  normalization + bounded context/cleanup + delegation to the approval loop.

Security policy remains in ``app.security.approval.flow``.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Awaitable, Callable

from loguru import logger
from langgraph.errors import GraphInterrupt

from app.security.approval import (
    ApprovalDecision,
    is_aborted,
    is_paused,
    register_approval_request,
)
from app.security.approval.flow import (
    _APPROVAL_POLL_INTERVAL,
    _ExtensionResult,
    _await_approval,
    _extract_paths_from_tool_call,
    _handle_directory_extension,
    _inject_tool_error_for_call,
    _make_approval_event,
    _resolve_max_wait,
)
from app.sse.events import make_error_event, make_sse_event

# HITL mechanics (Decision 6): imported here so tests can monkeypatch
# module-level names (``_is_interrupted``, ``_get_pending_tool_calls``).
from app.deepagent.hitl import (
    get_pending_tool_calls as _get_pending_tool_calls,
    is_interrupted as _is_interrupted,
    make_resume_command as _make_resume_command,
    make_uniform_resume_command as _make_uniform_resume_command,
    state_message_count as _state_message_count_helper,
    _tool_call_name,
)
# Approval session state machine (Decision 6 & 7).
from app.deepagent.approval_session import ApprovalSession, LoopExitReason

__all__ = ["run_agent_with_approval"]


async def _stream_default(
    agent: Any, inputs: Any, config: dict, source: str = "deep", **kwargs: Any
) -> AsyncIterator[dict[str, str]]:
    """默认 stream_fn：委托到 ``app.deepagent.streaming.stream_agent_events``。

    ``**kwargs`` 透传 ``seen_signatures`` 等跨 resume 去重参数，避免
    ``_stream`` 包装器因签名不匹配触发 TypeError 降级，导致去重集合每次重建为空。
    """
    from app.deepagent.streaming import stream_agent_events

    async for sse in stream_agent_events(agent, inputs, config, source=source, **kwargs):
        yield sse


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
    get_pending_calls_fn: Callable[[Any, dict], Awaitable[list[dict[str, Any]]]] | None = None,
    inject_tool_error_for_call_fn: Callable[[Any, dict, dict, str], Awaitable[None]] | None = None,
    inject_tool_error_messages_fn: Callable[[Any, dict, str], Awaitable[None]] | None = None,
    yield_event: Callable[[dict], Awaitable[None]] | None = None,
    max_iterations: int = 100,
) -> AsyncIterator[dict[str, str]]:
    """统一的 agent 审批执行循环（公共 facade，Decision 6）。

    Facade 职责:
    - Dependency normalization (resolve agent, checkpointer, sandbox, etc.)
    - Bounded context/cleanup (``bind_trace``, ``current_parent_thread_id``
      token, ``sandbox.clear_temp`` in ``finally``)
    - Delegation to ``_run_approval_loop`` (which uses ``ApprovalSession``)

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
        stream_fn: 流式事件生成函数，默认 ``stream_agent_events``。
        is_interrupted_fn: 中断检测函数。
        get_pending_calls_fn: 提取 pending tool_calls 函数。
        inject_tool_error_for_call_fn: 单条 tool_call 错误注入函数。
        inject_tool_error_messages_fn: 批量错误注入函数。
        yield_event: 可选的异步回调，每 yield 一个事件时同步调用（用于日志/观察）。
        max_iterations: 最大迭代次数。

    Yields:
        SSE 事件 dict: ``{event: str, data: str}``
    """
    from contextlib import nullcontext
    from app.sandbox import get_sandbox
    from app.observability.trace import bind_trace, current_trace_id
    from app.deepagent.context import current_parent_thread_id

    _trace_id = current_trace_id() or ""
    _token_parent = current_parent_thread_id.set(parent_thread_id)
    _sandbox = sandbox or get_sandbox()

    try:
        with bind_trace(_trace_id) if _trace_id else nullcontext():
            async for sse in _run_approval_loop(
                agent,
                config,
                thread_id=thread_id,
                workspace_path=workspace_path,
                permission_mode=permission_mode,
                runtime_dangerous=runtime_dangerous,
                source=source,
                inputs=inputs,
                sandbox=_sandbox,
                parent_thread_id=parent_thread_id,
                stream_fn=stream_fn,
                is_interrupted_fn=is_interrupted_fn,
                get_pending_calls_fn=get_pending_calls_fn,
                inject_tool_error_for_call_fn=inject_tool_error_for_call_fn,
                inject_tool_error_messages_fn=inject_tool_error_messages_fn,
                yield_event=yield_event,
                max_iterations=max_iterations,
            ):
                yield sse
    finally:
        current_parent_thread_id.reset(_token_parent)
        try:
            await _sandbox.clear_temp(thread_id)
        except Exception as exc:  # noqa: BLE001 — cleanup 失败不应阻塞主流程
            logger.warning(f"clear_temp cleanup failed: {exc!r}")


async def _run_approval_loop(
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
    get_pending_calls_fn: Callable[[Any, dict], Awaitable[list[dict[str, Any]]]] | None = None,
    inject_tool_error_for_call_fn: Callable[[Any, dict, dict, str], Awaitable[None]] | None = None,
    inject_tool_error_messages_fn: Callable[[Any, dict, str], Awaitable[None]] | None = None,
    yield_event: Callable[[dict], Awaitable[None]] | None = None,
    max_iterations: int = 100,
) -> AsyncIterator[dict[str, str]]:
    """统一的 agent 审批执行循环（内部，使用 ApprovalSession 状态机）。

    流程:
    1. 初始流式执行，产出 token/tool_call/tool_result 事件
    2. while 循环检测中断（interrupt_on）
       - 暂停/恢复检查
       - full_trust 模式跳过审批直接恢复
       - 危险工具审批（directory_extension 或 dangerous_tool）
       - 恢复执行
    3. 达到最大迭代次数或图完成后退出

    Uses ``ApprovalSession`` for state management (Decision 6):
    - ``stream_state``: shared ``StreamRunState`` across initial + resume streams
    - ``recent_calls_history``: repeat-loop detection window
    - ``stalled_count``: consecutive stall counter
    - ``yielded_msg_count``: baseline for graph-advance detection
    - ``exit_state``: loop exit classification (Decision 7)
    """
    from app.sandbox import get_sandbox
    from app.observability.trace import current_trace_id

    _trace_id = current_trace_id() or ""

    # ApprovalSession (Decision 6): holds all mutable loop state.
    _session = ApprovalSession()
    _stream_state = _session.stream_state
    _base_stream = stream_fn or _stream_default

    async def _stream(agent: Any, inputs: Any, config: dict, source: str) -> AsyncIterator[dict[str, str]]:
        """包装原 stream_fn 注入共享 stream_run_state + seen_signatures。

        同时传递 ``seen_signatures``（兼容旧 stream_fn 签名）和
        ``stream_run_state``（新生产路径）。TypeError 仅在函数调用阶段捕获：
        async generator function 被传入不支持的 kwarg 时会在调用时（而非迭代时）
        抛 TypeError。若把整个 ``async for`` 包进 try，迭代期间的 TypeError 会被
        误捕获并重新迭代，导致重复事件。

        回退链：both kwargs → seen_signatures only → no kwargs.
        """
        try:
            gen = _base_stream(
                agent,
                inputs,
                config,
                source,
                seen_signatures=_stream_state.seen_message_keys,
                stream_run_state=_stream_state,
            )
        except TypeError:
            try:
                gen = _base_stream(
                    agent,
                    inputs,
                    config,
                    source,
                    seen_signatures=_stream_state.seen_message_keys,
                )
            except TypeError:
                # 自定义 stream_fn（测试桩）不支持任何 kwarg，降级调用
                gen = _base_stream(agent, inputs, config, source)
        async for sse in gen:
            yield sse

    _is_int = is_interrupted_fn or _is_interrupted
    _get_calls = get_pending_calls_fn or _get_pending_tool_calls
    _inject_call = inject_tool_error_for_call_fn or _inject_tool_error_for_call
    _inject_msgs = inject_tool_error_messages_fn or _inject_tool_error_messages
    _sandbox = sandbox or get_sandbox()

    async def _state_msg_count() -> int:
        """读取当前 state 的 messages 数量。委托到 hitl.state_message_count。"""
        return await _state_message_count_helper(agent, config)

    async def _forward(event: dict[str, str]) -> dict[str, str]:
        """yield 前同步调用 yield_event 回调（用于日志/观察/统计）。"""
        if yield_event is not None:
            await yield_event(event)
        return event

    is_full_trust = permission_mode == "full_trust"
    iteration = 0

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
                        _make_uniform_resume_command(
                            pending, decision_type="reject", message="执行失败"
                        ),
                        config=config,
                        stream_mode="values",
                    ):
                        pass
        except GraphInterrupt:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"unexpected resume exception: {exc!r}")
        _session.mark_exit(LoopExitReason.TERMINAL, iteration)
        return
    # 初始化已 yield 基线：取初始 stream 后的 state.messages 数量。
    _session.yielded_msg_count = await _state_msg_count()

    # 2. 中断/恢复循环
    while iteration < max_iterations:
        iteration += 1

        # 最小延迟：防止 CPU 占满和日志风暴
        await asyncio.sleep(0.05)

        # 暂停检查：若已暂停，yield paused 事件后结束当前流
        # 恢复由前端重新发送消息触发，LangGraph 从 checkpoint 自动恢复
        if await is_paused(thread_id):
            yield await _forward(make_sse_event("paused", {}))
            # B3 修复：注入 ``decision_type="reject"`` 但 ``message=None``，
            # 这样 HumanInTheLoopMiddleware 会消费 interrupt 但不向 state 写入 ToolMessage
            # （pause 是"用户主动停下"，不是"AI 拒绝执行"）。
            try:
                if await _is_int(agent, config):
                    pending = await _get_calls(agent, config)
                    if pending:
                        async for _ in agent.astream(
                            _make_uniform_resume_command(
                                pending, decision_type="reject", message=""
                            ),
                            config=config,
                            stream_mode="values",
                        ):
                            pass
            except GraphInterrupt:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"unexpected resume exception: {exc!r}")
            # 清残留审批决策，避免下次会话首个决策污染（B12 修复同步在 chat.py finally）
            try:
                from app.security.approval import pop_approval
                await pop_approval(thread_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"unexpected pop_approval exception: {exc!r}")
            # 结束 SSE 流，不 wait_for_resume；恢复走重新发送消息路径
            _session.mark_exit(LoopExitReason.TERMINAL, iteration)
            return

        # abort 检查
        if await is_aborted(thread_id):
            yield await _forward(make_error_event( "操作已中止"))
            _session.mark_exit(LoopExitReason.TERMINAL, iteration)
            return

        if not await _is_int(agent, config):
            break

        pending_calls = await _get_calls(agent, config)
        if not pending_calls:
            logger.warning("interrupted but no pending tool calls", thread_id=thread_id)
            break

        # 重复工具调用检测。
        # 关键：仅在 LLM 真正生成新 AIMessage 时才记录（current_msg_count > yielded_msg_count）。
        current_msg_count = await _state_msg_count()
        _no_new_messages = (
            _session.yielded_msg_count >= 0
            and current_msg_count >= 0
            and current_msg_count <= _session.yielded_msg_count
        )
        if _no_new_messages:
            logger.debug(
                "repeat detection skipped: no new messages since last stream",
                thread_id=thread_id,
                current_msg_count=current_msg_count,
                yielded_msg_count=_session.yielded_msg_count,
                source=source,
            )
            # 防御：虽然 msg_count 未增长，但如果 pending_calls 与上次完全相同，
            # 且已连续多次出现，则可能是 stuck state 导致的重复（而非正常重放）。
            if _session.recent_calls_history and pending_calls:
                last_calls = _session.recent_calls_history[-1]
                if (
                    len(pending_calls) == len(last_calls)
                    and all(
                        p.get("name") == prev.get("name")
                        and p.get("args", {}) == prev.get("args", {})
                        for p, prev in zip(pending_calls, last_calls)
                    )
                ):
                    _session.record_pending_calls(pending_calls)
        else:
            _session.record_pending_calls(pending_calls)
            if _session.is_repeating_loop():
                logger.warning(
                    "agent stuck in repeating tool-call loop, forcing stop",
                    thread_id=thread_id,
                    source=source,
                )
                await _inject_msgs(agent, config, "工具调用陷入重复循环，已强制停止。请简化您的请求或明确指定目标路径。")
                yield await _forward(
                    make_sse_event(
                        "error",
                        "工具调用陷入重复循环，已强制停止。请简化您的请求或明确指定目标路径。",
                    )
                )
                _session.mark_exit(LoopExitReason.TERMINAL, iteration)
                return

        # full_trust 模式：直接恢复
        if is_full_trust:
            pre_resume_msg_count = await _state_msg_count()
            try:
                async for sse in _stream(
                    agent,
                    _make_uniform_resume_command(pending_calls, decision_type="approve"),
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
                        _make_uniform_resume_command(
                            pending_calls, decision_type="reject", message=f"恢复失败: {exc}"
                        ),
                        config=config,
                        stream_mode="values",
                    ):
                        pass
                except GraphInterrupt:
                    pass
                except Exception as exc2:  # noqa: BLE001
                    logger.warning(f"unexpected resume exception: {exc2!r}")
                yield await _forward(make_error_event( f"恢复失败: {exc}"))
                _session.mark_exit(LoopExitReason.TERMINAL, iteration)
                return
            # 刷新已 yield 基线（root cause: trace=64851677fced422c）
            _session.yielded_msg_count = await _state_msg_count()
            # 防御：检测 resume 后 state 是否推进（root cause: trace=e629ab945dad467c）
            if (
                pre_resume_msg_count >= 0
                and _session.yielded_msg_count >= 0
                and _session.yielded_msg_count <= pre_resume_msg_count
            ):
                logger.warning(
                    "agent resume did not advance state, possible stuck state in full_trust",
                    thread_id=thread_id,
                    source=source,
                    pre_resume_msg_count=pre_resume_msg_count,
                    post_resume_msg_count=_session.yielded_msg_count,
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
                        _make_uniform_resume_command(
                            pending_calls, decision_type="reject", message="状态未推进，强制终止"
                        ),
                        config=config,
                        stream_mode="values",
                    ):
                        pass
                except GraphInterrupt:
                    pass
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"unexpected resume exception: {exc!r}")
                yield await _forward(
                    make_error_event(
                        "工具执行后状态未正常推进，已强制终止。请重试或联系支持。"
                    )
                )
                _session.mark_exit(LoopExitReason.TERMINAL, iteration)
                return
            continue

        # standard 模式：request_permission 工具调用检测（LLM 主动申请路径授权）
        # 必须在 dangerous_calls 之前处理：审批通过后 _stream(resume=approve) 让工具执行
        # 返回"已授权"，然后 continue 回到循环顶部等待 LLM 下一轮（通常是重新调用原
        # 失败的工具）。不 return，让 agent 继续执行。
        permission_calls = [tc for tc in pending_calls if _tool_call_name(tc) == "request_permission"]
        if permission_calls:
            for tc in permission_calls:
                raw_args = tc.get("args", {}) or {}
                args = raw_args if isinstance(raw_args, dict) else {}
                path = args.get("path", "") or ""
                writable = bool(args.get("writable", False))
                reason = args.get("reason", "") or ""
                # kind 判断：有 path → directory_extension；纯命令权限 → sandbox_escalation
                kind = "directory_extension" if path else "sandbox_escalation"
                # D7: 注册活跃审批请求，生成 approval_id + run_id
                approval_req = await register_approval_request(
                    thread_id,
                    _trace_id,
                    kind,
                    tc.get("id"),
                )
                yield await _forward(
                    _make_approval_event(
                        tc,
                        thread_id,
                        kind=kind,
                        requested_path=path or None,
                        writable=writable,
                        reason=reason,
                        approval_id=approval_req.approval_id,
                        run_id=approval_req.run_id,
                    )
                )

            decision = await _await_approval(
                thread_id,
                poll_interval=_APPROVAL_POLL_INTERVAL,
                max_wait=_resolve_max_wait(),
            )

            if decision is None or not decision.approved:
                # 用户拒绝或超时：注入 error ToolMessage + Command(resume=reject)
                for tc in permission_calls:
                    raw_args = tc.get("args", {}) or {}
                    args = raw_args if isinstance(raw_args, dict) else {}
                    denied_path = args.get("path", "") or ""
                    await _inject_call(
                        agent, config, tc, f"用户拒绝授权路径: {denied_path}"
                    )
                yield await _forward(make_error_event("用户拒绝授权路径"))
                # 消费 HITL interrupt，避免 stuck state
                try:
                    async for _ in agent.astream(
                        _make_uniform_resume_command(
                            pending_calls,
                            decision_type="reject",
                            message="用户拒绝授权路径",
                        ),
                        config=config,
                        stream_mode="values",
                    ):
                        pass
                except GraphInterrupt:
                    pass
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"unexpected resume exception: {exc!r}")
                _session.mark_exit(LoopExitReason.TERMINAL, iteration)
                return

            # 审批通过：对有 path 的调用执行 sandbox 授权
            # Major 1: 跟踪授权失败，避免异常被吞后工具仍返回"已授权"导致 LLM 陷入重试
            auth_failures: list[tuple[dict[str, Any], str]] = []
            for tc in permission_calls:
                raw_args = tc.get("args", {}) or {}
                args = raw_args if isinstance(raw_args, dict) else {}
                path = args.get("path", "") or ""
                writable = bool(args.get("writable", False))
                if not path:
                    continue
                try:
                    if decision.decision in (
                        ApprovalDecision.ONCE,
                        ApprovalDecision.APPROVE,
                    ):
                        await _sandbox.authorize_temp(thread_id, path, writable=writable)
                    elif decision.decision == ApprovalDecision.SESSION:
                        await _sandbox.authorize(thread_id, path, writable=writable)
                    elif decision.decision == ApprovalDecision.FULL_TRUST:
                        await _sandbox.authorize(thread_id, path, writable=True)
                        if hasattr(_sandbox, "set_full_trust"):
                            await _sandbox.set_full_trust(thread_id, True)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"request_permission authorize failed: {exc!r}")
                    auth_failures.append((tc, f"路径授权失败: {exc}"))

            # 注入授权失败的 error ToolMessage（在 resume 之前），让 LLM 看到真实失败
            for tc, err_msg in auth_failures:
                await _inject_call(agent, config, tc, err_msg)

            # ============================================================
            # Decision 6 fix (tasks 2.8 + 2.9): per-call aligned decisions
            # ============================================================
            # Bug 2.8: For a mixed request_permission + write_file batch,
            # approve ONLY the request_permission call(s). Reject/defer sibling
            # dangerous calls with an explicit retry message. This prevents an
            # unpresented write from executing.
            #
            # Bug 2.9: When authorize_temp raises, the permission call MUST be
            # resumed as reject (not approve) so the tool body does not execute
            # and return 路径已授权. The error ToolMessage injected above
            # reports the failure.
            #
            # Each pending call gets its own decision aligned by position.
            auth_failure_ids: set[int] = {id(tc) for tc, _ in auth_failures}
            sibling_retry_msg = "危险工具调用需单独审批，请在授权后重试"
            per_call_decisions: list[dict[str, Any]] = []
            for tc in pending_calls:
                name = _tool_call_name(tc)
                if name == "request_permission":
                    if id(tc) in auth_failure_ids:
                        # Auth failed → reject to prevent tool body returning 路径已授权
                        per_call_decisions.append(
                            {"type": "reject", "message": "路径授权失败"}
                        )
                    else:
                        per_call_decisions.append({"type": "approve"})
                elif name in runtime_dangerous:
                    # Sibling dangerous call: reject/defer with retry message.
                    # Inject error ToolMessage so the model sees why it was
                    # rejected and can retry after authorization.
                    per_call_decisions.append(
                        {"type": "reject", "message": sibling_retry_msg}
                    )
                    await _inject_call(agent, config, tc, sibling_retry_msg)
                else:
                    # Non-dangerous sibling: approve (no specific policy)
                    per_call_decisions.append({"type": "approve"})

            resume_command = _make_resume_command(per_call_decisions)

            # resume：_stream 恢复执行，request_permission 工具返回"已授权"
            # LLM 看到结果后重新调用原工具
            try:
                async for sse in _stream(
                    agent,
                    resume_command,
                    config,
                    source,
                ):
                    yield await _forward(sse)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "agent resume failed (request_permission)",
                    thread_id=thread_id,
                )
                await _inject_msgs(agent, config, f"恢复失败: {exc}")
                yield await _forward(make_error_event( f"恢复失败: {exc}"))
                # 消费残留 HITL interrupt，避免下次调用 stuck
                try:
                    if await _is_int(agent, config):
                        async for _ in agent.astream(
                            _make_uniform_resume_command(
                                pending_calls,
                                decision_type="reject",
                                message="执行异常",
                            ),
                            config=config,
                            stream_mode="values",
                        ):
                            pass
                except GraphInterrupt:
                    pass
                except Exception as exc2:  # noqa: BLE001
                    logger.warning(f"unexpected resume exception: {exc2!r}")
                _session.mark_exit(LoopExitReason.TERMINAL, iteration)
                return
            # 刷新已 yield 基线，回到循环顶部等待 LLM 下一轮 tool_call
            _session.yielded_msg_count = await _state_msg_count()
            continue

        # standard 模式：危险工具判定
        decision = None
        dangerous_calls: list[dict[str, Any]] = []
        for tc in pending_calls:
            name = _tool_call_name(tc)
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
                # D7 修复：发 approval_request 事件前必须 register approval_request，
                # 生成 approval_id + run_id 供前端回传到 /api/chat/approve 消费。
                approval_req = await register_approval_request(
                    thread_id,
                    _trace_id,
                    "dangerous_tool",
                    tc.get("id"),
                )
                yield await _forward(
                    _make_approval_event(
                        tc,
                        thread_id,
                        kind="dangerous_tool",
                        approval_id=approval_req.approval_id,
                        run_id=approval_req.run_id,
                    )
                )

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
                        _make_uniform_resume_command(
                            pending_calls, decision_type="reject", message="用户拒绝执行危险操作"
                        ),
                        config=config,
                        stream_mode="values",
                    ):
                        pass
                except GraphInterrupt:
                    pass
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"unexpected resume exception: {exc!r}")
                _session.mark_exit(LoopExitReason.TERMINAL, iteration)
                return

            logger.info(
                "agent approval granted",
                thread_id=thread_id,
                tool_count=len(dangerous_calls),
                tools=[_tool_call_name(tc) for tc in dangerous_calls],
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
                    _make_uniform_resume_command(
                        pending_calls, decision_type="reject", message="用户拒绝访问该目录"
                    ),
                    config=config,
                    stream_mode="values",
                ):
                    pass
            except GraphInterrupt:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"unexpected resume exception: {exc!r}")
            _session.mark_exit(LoopExitReason.TERMINAL, iteration)
            return
        if extension_handled.timed_out:
            yield await _forward(make_error_event( "目录授权等待被中断，操作未执行"))
            await _inject_msgs(agent, config, "目录授权等待被中断，操作未执行")
            # 消费 HITL interrupt，避免 stuck state
            try:
                async for _ in agent.astream(
                    _make_uniform_resume_command(
                        pending_calls, decision_type="reject", message="目录授权等待被中断，操作未执行"
                    ),
                    config=config,
                    stream_mode="values",
                ):
                    pass
            except GraphInterrupt:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"unexpected resume exception: {exc!r}")
            _session.mark_exit(LoopExitReason.TERMINAL, iteration)
            return

        # 恢复执行
        logger.info(
            "agent resume execution",
            thread_id=thread_id,
            source=source,
            pending_tools=[_tool_call_name(tc) for tc in pending_calls],
        )
        try:
            async for sse in _stream(
                agent,
                _make_uniform_resume_command(pending_calls, decision_type="approve"),
                config,
                source,
            ):
                yield await _forward(sse)
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent resume failed", thread_id=thread_id)
            await _inject_msgs(agent, config, f"恢复失败: {exc}")
            yield await _forward(make_error_event( f"恢复失败: {exc}"))
            # 消费残留 HITL interrupt，避免下次调用 stuck
            try:
                if await _is_int(agent, config):
                    async for _ in agent.astream(
                        _make_uniform_resume_command(
                            pending_calls,
                            decision_type="reject",
                            message="执行异常",
                        ),
                        config=config,
                        stream_mode="values",
                    ):
                        pass
            except GraphInterrupt:
                pass
            except Exception as exc2:  # noqa: BLE001
                logger.warning(f"unexpected resume exception: {exc2!r}")
            _session.mark_exit(LoopExitReason.TERMINAL, iteration)
            return

        logger.info(
            "agent resume completed",
            thread_id=thread_id,
            source=source,
        )

        # 刷新已 yield 基线（root cause: trace=64851677fced422c）。
        _session.yielded_msg_count = await _state_msg_count()

        # 防御性检查：若 resume 后 state 未推进（msg_count 未增长）
        # 且仍被中断，说明 LangGraph interrupt 可能 stuck。
        # 连续 2 次停滞才强制停止，避免偶发 async 调度延迟导致误判
        # （root cause: trace=7c742e6f60dc4d96）。
        _state_stalled = (
            current_msg_count >= 0
            and _session.yielded_msg_count >= 0
            and _session.yielded_msg_count <= current_msg_count
        )
        if _state_stalled and await _is_int(agent, config):
            _session.increment_stall()
            if _session.stalled_count >= _session.STALL_THRESHOLD:
                logger.warning(
                    "agent resume did not clear interrupt for %d consecutive iterations, forcing stop",
                    _session.stalled_count,
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
                _session.mark_exit(LoopExitReason.TERMINAL, iteration)
                return
            logger.warning(
                "agent resume did not clear interrupt, waiting for next iteration (stalled_count=%d)",
                _session.stalled_count,
                thread_id=thread_id,
                source=source,
                iteration=iteration,
            )
        else:
            _session.reset_stall_counter()

    # ============================================================
    # Decision 7 fix (task 2.10): model loop exit explicitly
    # ============================================================
    # Bug 2.10: ``if iteration >= max_iterations`` emitted the max-iteration
    # error even when the graph completed on the final iteration (broke out
    # via ``break`` because ``_is_int`` returned False).
    #
    # Fix: model loop exit as one of three states (Decision 7):
    # 1. completed — graph finished normally → NO error emitted
    # 2. terminal — graph terminated (error/abort) → NO iteration error
    # 3. still-interrupted — graph still interrupted at limit → emit error ONCE
    #
    # Only the still-interrupted state emits the iteration-limit error.
    if iteration >= max_iterations:
        # Check if the graph is still interrupted at the limit.
        # If it completed (broke out via ``not _is_int``), don't emit the error.
        try:
            _still_interrupted = await _is_int(agent, config)
        except Exception:  # noqa: BLE001 — state read failure: conservative default
            _still_interrupted = True

        if _still_interrupted:
            logger.warning(
                "agent hit max iterations, still interrupted",
                thread_id=thread_id,
                iteration=iteration,
            )
            yield await _forward(make_error_event("达到最大迭代上限"))
            await _inject_msgs(agent, config, "达到最大迭代上限")
            _session.mark_exit(LoopExitReason.STILL_INTERRUPTED, iteration)
        else:
            # Graph completed on the final iteration — no error emitted.
            _session.mark_exit(LoopExitReason.COMPLETED, iteration)
        return

    # Loop exited via ``break`` (graph completed normally)
    _session.mark_exit(LoopExitReason.COMPLETED, iteration)
