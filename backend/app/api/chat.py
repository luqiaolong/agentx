"""聊天 SSE 路由 + 审批 / 中止 / 压缩端点。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.api.schemas import (
    AbortRequest,
    ApproveRequest,
    ChatRequest,
    CompactRequest,
)
from app.security.approval import (
    ApprovalDecision,
    ApprovalResult,
    clear_abort,
    clear_pause,
    consume_approval,
    has_pending_approval,
    is_aborted,
    set_abort,
    set_pause,
    submit_approval,
)
from app.config import get_settings
from app.lifecycle.run_session import (
    begin_cleanup,
    complete_run_cleanup,
    get_active_run,
    register_run,
)
from app.observability.langsmith import dual_trace, mark_redacted, trace_span
from app.observability.logger import logger
from app.observability.observation import get_observation_sink
from app.observability.trace import bind_trace, new_trace_id


async def _clear_thread_state(thread_id: str) -> None:
    """清空指定会话的 checkpointer 状态（best-effort）。

    尝试用异步 checkpointer 的 ``adelete_thread`` 删除该 thread 的所有 checkpoint。
    失败时仅记日志，不阻塞 /reset 流程。
    """
    # 延迟 import：测试通过 monkeypatch app.main.get_async_checkpointer 注入 fake
    from app.main import get_async_checkpointer

    try:
        checkpointer = await get_async_checkpointer()
        if hasattr(checkpointer, "adelete_thread"):
            await checkpointer.adelete_thread(thread_id)
            logger.info("checkpoint cleared for thread", thread_id=thread_id)
        elif hasattr(checkpointer, "conn"):
            # fallback：直接用 SQL 删除（兼容无 adelete_thread 的同步 SqliteSaver 包装）
            await asyncio.to_thread(
                checkpointer.conn.execute,
                "DELETE FROM checkpoints WHERE thread_id = ?",
                (thread_id,),
            )
            if hasattr(checkpointer.conn, "commit"):
                await asyncio.to_thread(checkpointer.conn.commit)
            logger.info("checkpoint cleared via SQL for thread", thread_id=thread_id)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("clear checkpoint failed", thread_id=thread_id, error=str(exc))


async def _enumerate_child_thread_ids(thread_id: str, checkpointer: Any) -> list[str]:
    """枚举指定 parent thread 的所有子任务 checkpoint thread_id（T5）。

    匹配 ``{thread_id}-team-*`` 前缀（Phase 2 T5 UUID 化后格式为
    ``{parent}-team-{uuid4()}``）。用于 /reset 清理孤儿 child checkpoint，
    避免 ``data/agentx.db`` 无限膨胀。

    优先用 ``checkpointer.alist()`` 枚举后前缀过滤，fallback 为 SQL ``LIKE``
    查询（兼容无 ``alist`` 的同步 SqliteSaver 包装）。

    Args:
        thread_id: 父 thread_id。
        checkpointer: checkpointer 实例（支持 ``alist`` 或 ``conn``）。

    Returns:
        子 thread_id 列表（可能为空）。
    """
    # 转义 SQL LIKE 通配符（\、%、_），避免 thread_id 含特殊字符时匹配错误
    # （review issue 4 修复：thread_id 理论上来自前端，可能含 % 或 _）
    escaped_thread_id = (
        thread_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    prefix = f"{escaped_thread_id}-team-"

    # 路径 1：checkpointer.alist()（如果可用）
    if hasattr(checkpointer, "alist"):
        try:
            child_ids: list[str] = []
            async for config, _meta, _parent in checkpointer.alist():
                cfg = config.get("configurable", {}) if isinstance(config, dict) else {}
                tid = cfg.get("thread_id", "")
                # 用真实前缀（未转义）做 startswith，因为 alist 返回的是原始 thread_id
                if tid.startswith(f"{thread_id}-team-"):
                    child_ids.append(tid)
            return child_ids
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "enumerate via alist failed, falling back to SQL",
                thread_id=thread_id,
                error=str(exc),
            )

    # 路径 2：SQL fallback（无 alist 或 alist 失败）
    if hasattr(checkpointer, "conn"):
        try:
            pattern = f"{prefix}%"
            cursor = await asyncio.to_thread(
                checkpointer.conn.execute,
                "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE ? ESCAPE '\\'",
                (pattern,),
            )
            rows = await asyncio.to_thread(cursor.fetchall)
            return [row[0] for row in rows if row and row[0]]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "enumerate via SQL failed",
                thread_id=thread_id,
                error=str(exc),
            )

    return []


# ---- per-thread_id SSE 流隔离锁（M4）----
# 同一 thread_id 的并发 SSE 请求会竞态写 checkpoint，用 asyncio.Lock 串行化。
_stream_locks: dict[str, asyncio.Lock] = {}
_stream_locks_guard = asyncio.Lock()


async def _get_stream_lock(thread_id: str) -> asyncio.Lock:
    """获取（或创建）指定 thread_id 的 SSE 流锁。"""
    async with _stream_locks_guard:
        if thread_id not in _stream_locks:
            _stream_locks[thread_id] = asyncio.Lock()
        return _stream_locks[thread_id]


@asynccontextmanager
async def _acquired_stream_lock(thread_id: str, trace_id: str):
    """获取并管理 SSE 流锁的生命周期（含超时保护）。

    锁获取超时 300s，超时后 yield error/done 事件并退出。
    cleanup 完成后才释放锁，防止新 run 在旧 cleanup 期间启动。
    """
    lock = await _get_stream_lock(thread_id)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=300.0)
    except asyncio.TimeoutError:
        logger.warning("stream lock acquire timeout", thread_id=thread_id, trace_id=trace_id)
        yield False
        return
    try:
        yield True
    finally:
        # M8: 释放锁后若无人等待，从 dict 移除避免内存泄漏
        lock.release()
        async with _stream_locks_guard:
            if not lock.locked():
                _stream_locks.pop(thread_id, None)


async def _run_with_heartbeat(
    source: AsyncIterator[dict[str, str]],
    timeout: float = 10.0,
) -> AsyncIterator[dict[str, str]]:
    """包装 source，长时间无事件时 yield heartbeat。

    关键：不能用 ``asyncio.wait_for`` 包裹 ``__anext__()`` —— timeout 触发时
    wait_for 会 cancel 底层 coroutine，CancelledError 会沿生成器链传播
    （run_router → run_coding_team → ... → execute_node → acquire_and_run），
    把正在执行的长任务误判为"用户中止"。
    改用 ``asyncio.wait(..., FIRST_COMPLETED)``：timeout 不 cancel 任务，
    下一轮继续等待同一个 task，长任务得以保留。
    """
    _next_task: asyncio.Task | None = None
    try:
        while True:
            if _next_task is None:
                _next_task = asyncio.ensure_future(source.__anext__())
            done, _pending = await asyncio.wait(
                {_next_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                # timeout：底层 task 仍在跑，只发心跳不打断
                yield {"event": "heartbeat", "data": "{}"}
                continue
            # task 完成：取出结果（可能抛 StopAsyncIteration）
            try:
                event = _next_task.result()
            except StopAsyncIteration:
                break
            _next_task = None
            yield event
    finally:
        # 清理未完成的 task，避免孤儿协程泄漏
        if _next_task is not None and not _next_task.done():
            _next_task.cancel()
            with contextlib.suppress(BaseException):
                await _next_task


# ---- 事件处理器（表驱动）----
_EVENT_HANDLERS: dict[
    str,
    callable[[dict[str, str], list[str]], dict[str, str] | None],
] = {}


def _register_event_handler(event_type: str):
    """注册事件处理器的装饰器。"""
    def decorator(fn):
        _EVENT_HANDLERS[event_type] = fn
        return fn
    return decorator


@_register_event_handler("token")
def _handle_token(event: dict[str, str], parts: list[str]) -> dict[str, str] | None:
    """处理 token 事件：过滤工作区恢复通知，收集有效内容。"""
    data = str(event.get("data", ""))
    if data.startswith("[工作区恢复]"):
        return event  # 前端会跳过渲染，不收集
    parts.append(data)
    return event


@_register_event_handler("reasoning")
def _handle_reasoning(event: dict[str, str], parts: list[str]) -> dict[str, str] | None:
    """处理 reasoning 事件：提取 content 并收集。"""
    try:
        payload = json.loads(event.get("data", "{}"))
        content = payload.get("content", "") if isinstance(payload, dict) else ""
    except json.JSONDecodeError:
        content = ""
    if content:
        parts.append(content)
    return event


@_register_event_handler("reasoning_delta")
def _handle_reasoning_delta(event: dict[str, str], parts: list[str]) -> dict[str, str] | None:
    """处理 reasoning_delta 事件：提取 delta 并收集。"""
    try:
        payload = json.loads(event.get("data", "{}"))
        delta = payload.get("delta", "") if isinstance(payload, dict) else ""
    except json.JSONDecodeError:
        delta = ""
    if delta:
        parts.append(delta)
    return event


@_register_event_handler("done")
def _handle_done(event: dict[str, str], parts: list[str]) -> dict[str, str] | None:
    """过滤 run_router 自行发送的 done，统一由 chat.py 收口。"""
    return None  # 跳过，不 yield


async def _handle_reset(
    req: ChatRequest, trace_id: str
) -> AsyncIterator[dict[str, str]]:
    """处理 /reset 命令：清空 checkpointer + 沙箱。"""
    # 延迟 import：测试通过 monkeypatch app.main.* 注入 fake
    from app.main import get_async_checkpointer, get_sandbox

    settings = get_settings()
    reset_checkpointer = await get_async_checkpointer()
    child_ids = await _enumerate_child_thread_ids(req.thread_id, reset_checkpointer)
    for child_id in child_ids:
        await _clear_thread_state(child_id)
    if child_ids:
        logger.info(
            "reset cleared orphan child checkpoints",
            thread_id=req.thread_id,
            count=len(child_ids),
        )
    await _clear_thread_state(req.thread_id)
    if not settings.persist_authorized_dirs:
        await get_sandbox().clear(req.thread_id)
        yield {"event": "token", "data": "已清空会话状态与授权目录"}
    else:
        yield {"event": "token", "data": "已清空会话状态（授权目录已持久化，未清空）"}
    yield {"event": "done", "data": json.dumps({"reason": "completed"})}


async def _handle_resume(
    req: ChatRequest, trace_id: str
) -> AsyncIterator[dict[str, str]]:
    """处理 /resume 命令：清除暂停标志。"""
    await clear_pause(req.thread_id)
    yield {"event": "token", "data": "已恢复执行"}
    yield {"event": "done", "data": json.dumps({"reason": "completed"})}


async def _cleanup_chat_state(thread_id: str, trace_id: str) -> None:
    """统一清理聊天状态：abort/pause/approval 标志，并通知 run_session。

    REQ-CHAT-2 / I1.3: cleanup 必须在 stream_lock 释放之前完成，
    防止新 run 在旧 cleanup 期间启动。
    """
    logger.info("chat generator finally", trace_id=trace_id, thread_id=thread_id)
    try:
        # 1. 标记当前 run 进入 cleaning_up（run_id 验证确保不误伤其他 run）
        await begin_cleanup(thread_id, trace_id)
        # 2. H4: 先 set_abort 通知子任务节点检查 is_aborted() 后停止，
        #    避免 SSE 断连后后台继续消耗 LLM token；再 clear_abort 清理状态
        await set_abort(thread_id)
        await clear_abort(thread_id)
        await clear_pause(thread_id)
        # B12 修复：清残留审批决策
        try:
            from app.security.approval import pop_approval
            await pop_approval(thread_id)
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning("chat finally pop_approval failed", thread_id=thread_id, error=str(exc))
        # 3. 标记 cleanup 完成（唤醒等待新 run 的 register_run waiter）
        await complete_run_cleanup(thread_id, trace_id)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("chat finally cleanup failed", thread_id=thread_id, trace_id=trace_id, error=str(exc))


async def _run_router_and_collect(
    req: ChatRequest,
    trace_id: str,
    effective_agent_mode: str,
    checkpointer: Any,
) -> AsyncIterator[dict[str, str]]:
    """调用 run_router 并收集 assistant_content_parts，处理中止和事件过滤。

    内部用 _run_with_heartbeat 包装，防止长任务期间前端断连。
    """
    # 延迟 import：测试通过 monkeypatch app.main.* 注入 fake
    from app.main import run_router

    assistant_content_parts: list[str] = []
    with dual_trace(
        thread_id=req.thread_id,
        agent_mode=effective_agent_mode,
        user_message=req.message,
        permission_mode=req.permission_mode,
        workspace_path=req.workspace_path,
        run_id=trace_id,
    ) as obs_ctx:
        try:
            router_gen = run_router(
                req.message,
                req.thread_id,
                checkpointer=checkpointer,
                permission_mode=req.permission_mode,
                agent_mode=effective_agent_mode,
                workspace_path=req.workspace_path,
                revoked_paths=req.revoked_paths,
                trace_id=trace_id,
                system_prompt=req.system_prompt,
            ).__aiter__()

            async for event in _run_with_heartbeat(router_gen):
                # 检查中止标志
                if await is_aborted(req.thread_id):
                    obs_ctx.add_metadata("error_type", "aborted")
                    obs_ctx.add_metadata("error_message", "user aborted")
                    # L20: team 模式中止时先发 team_done{status: error}
                    if effective_agent_mode == "coding_team":
                        yield {
                            "event": "team_done",
                            "data": json.dumps({"status": "error"}),
                        }
                    yield {
                        "event": "error",
                        "data": f"用户已中止 | trace={trace_id}",
                    }
                    # REQ-CHAT-3: abort 必须发出唯一 terminal signal
                    yield {"event": "done", "data": json.dumps({"reason": "aborted"})}
                    await clear_abort(req.thread_id)
                    return

                # 表驱动事件处理：收集内容 + 过滤
                event_type = event.get("event", "")
                handler = _EVENT_HANDLERS.get(event_type)
                if handler is not None:
                    event = handler(event, assistant_content_parts)
                if event is None:
                    continue

                # 增量更新 result_text 到 obs_ctx 内存（不写 DB）
                obs_ctx.add_metadata(
                    "result_text", "".join(assistant_content_parts).strip()
                )
                yield event

            # FR-4.4: 正常出口写 result_token_count
            _real_tc = None
            try:
                _real_tc = await get_observation_sink().get_last_llm_token_count(trace_id)
            except (sqlite3.Error, RuntimeError, AttributeError) as exc:
                logger.debug(
                    "get_last_llm_token_count failed, fallback to char estimate",
                    trace_id=trace_id,
                    error=str(exc),
                )
            if _real_tc is None:
                _real_tc = max(1, len("".join(assistant_content_parts)) // 4)
            obs_ctx.add_metadata("result_token_count", _real_tc)
            logger.info(
                "chat done event yielding",
                trace_id=trace_id,
                agent_mode=effective_agent_mode,
                token_count=_real_tc,
                result_text_len=len("".join(assistant_content_parts)),
            )
            yield {"event": "done", "data": json.dumps({"reason": "completed", "token_count": _real_tc})}
        except Exception as inner_exc:
            # FR-4.5: 异常分支写 error_type + error_message（在 dual_trace 退出前设置）
            obs_ctx.add_metadata("error_type", type(inner_exc).__name__)
            obs_ctx.add_metadata("error_message", str(inner_exc))
            raise


async def _event_generator(req: ChatRequest) -> AsyncIterator[dict[str, str]]:
    """SSE 事件生成器：/reset 清空状态，其他消息走 Router 三路径分发。

    事件契约（与前端 preload 一致，chat-rendering-trace-v2 扩展）：
    - ``token``         — 增量 token（visible text，已剥离 think 块）。
    - ``reasoning``     — 思考过程 chunk（data 为 JSON ``{"content": str, "source": str}``，
                          由 ThinkFilter retain_think 模式从 token 流分离）。
    - ``tool_call``     — 工具调用开始（data 为 JSON ``{"id","name","args","source"}``，
                          id 用于前端配对 tool_result）。
    - ``tool_result``   — 工具调用结束（data 为 JSON ``{"id","name","result","source","error?"}``）。
    - ``delegation``    — 子代理委派标记（data 为 JSON ``{"target","source","message"}``）。
    - ``todo_update``   — DeepAgent/Team 任务列表更新（原生 deepagents ``{content, status}`` schema，
                          status: ``"pending"|"in_progress"|"completed"``；可选 ``task_id`` 区分 Team 子任务）。
    - ``approval_request`` — 危险工具/目录扩展审批请求（含 tool_name / args / preview）。
    - ``team_init``     — AgentTeam 计划生成完成（data 为 JSON ``{"plan","agents","reasoning"}``），
                          前端据此在消息顶部创建 TeamNodeCard。在 ``team_done`` 之前发出。
    - ``team_done``     — AgentTeam 整体结束（data 为 JSON ``{"status": "done"|"error"}``）。
    - ``done``          — 流结束。
    - ``error``         — 错误（含消息）。

    trace_id 贯穿：
    - 入口生成 16 字符 hex（``new_trace_id``），通过 ``bind_trace`` ContextVar
      注入到下游所有 ``logger.info`` 与 ``make_sse_event`` 调用。
    - 当前活跃 trace_id 也会被 loguru patcher 自动注入到 stderr/file sink，
      日志行尾 ``| trace=xxxxxxxxxxxxxxxx`` 即可在 backend.log grep 出整条链路。
    - 第一个事件（reasoning / tool_call / error / done）即附带 trace_id，
      前端可立即订阅并展示给用户（用户报问题时复制）。
    """
    # 延迟 import：测试通过 monkeypatch app.main.* 注入 fake
    from app.main import get_async_checkpointer

    settings = get_settings()
    # trace_id 生成：优先沿用前端传入（chat.ts::send() 生成），缺失或异常时
    # 由后端自行生成。bind_trace 设置 ContextVar，下游所有 logger.info /
    # make_sse_event 自动通过 patcher / _resolve_trace_id 拿到。
    frontend_trace_id = (req.trace_id or "").strip()
    if frontend_trace_id and len(frontend_trace_id) <= 32:
        trace_id = frontend_trace_id
    else:
        trace_id = new_trace_id()

    with bind_trace(trace_id):
        logger.info(
            "chat request",
            thread_id=req.thread_id,
            trace_id=trace_id,
            message_len=len(req.message),
            agent_mode=req.agent_mode,
        )

        # M4: 同一 thread_id 的并发 SSE 流用锁串行化，避免竞态写 checkpoint。
        # 使用 _acquired_stream_lock 上下文管理器：超时保护 + 自动释放。
        async with _acquired_stream_lock(req.thread_id, trace_id) as lock_acquired:
            if not lock_acquired:
                yield {
                    "event": "error",
                    "data": f"会话繁忙，获取流锁超时，请稍后重试 | trace={trace_id}",
                }
                yield {"event": "done", "data": json.dumps({"reason": "error"})}
                return

            # REQ-CHAT-1: 注册 live run session（run_id=trace_id）。
            _run_session = await register_run(
                req.thread_id,
                run_id=trace_id,
                wait_for_cleanup=True,
                timeout=300.0,
            )
            if _run_session is None:
                yield {
                    "event": "error",
                    "data": f"上一个请求仍在清理中，请稍后重试 | trace={trace_id}",
                }
                yield {"event": "done", "data": json.dumps({"reason": "error"})}
                return

            # 更新 thread 最后活跃时间（供 checkpointer TTL 清理使用）
            from app.memory.checkpointer_view import touch_thread
            await touch_thread(req.thread_id)

            # 命令路由
            if req.message.startswith("/reset"):
                async for event in _handle_reset(req, trace_id):
                    yield event
                return

            if req.message.startswith("/resume"):
                async for event in _handle_resume(req, trace_id):
                    yield event
                return

            # 其他消息：走 Router 场景分发
            checkpointer = await get_async_checkpointer()
            effective_agent_mode = req.agent_mode
            if req.agent_mode == "coding_team" and not settings.agents.coding_team_enabled:
                effective_agent_mode = "coding"

            try:
                async for event in _run_router_and_collect(
                    req, trace_id, effective_agent_mode, checkpointer
                ):
                    yield event
            except Exception as exc:  # noqa: BLE001 — SSE 兜底，避免连接挂起
                logger.exception("SSE chat error", thread_id=req.thread_id)
                _exc_name = type(exc).__name__
                if _exc_name == "GraphRecursionError":
                    _user_msg = (
                        "任务步骤过多，已达到执行上限。建议："
                        "① 简化请求 ② 明确指定目标文件路径 ③ 拆分为多个小任务"
                    )
                else:
                    _user_msg = f"内部错误: {exc}"
                yield {
                    "event": "error",
                    "data": f"{_user_msg} | trace={trace_id}",
                }
                # REQ-CHAT-3: error 终态标记 reason=error
                yield {"event": "done", "data": json.dumps({"reason": "error"})}
            finally:
                # C1: 无论正常退出、异常、还是客户端断连，都必须清理状态。
                # cleanup 在锁释放前完成（由 _acquired_stream_lock 保证）。
                await _cleanup_chat_state(req.thread_id, trace_id)


def register_chat_routes(app: FastAPI) -> None:
    """注册聊天相关路由。

    含 ``POST /api/chat`` + ``POST /api/chat/approve`` + ``POST /api/chat/abort``
    + ``POST /api/chat/pause`` + ``POST /api/chat/resume`` + ``POST /api/chat/compact``。
    """

    @app.post("/api/chat/approve")
    async def chat_approve(req: ApproveRequest) -> dict[str, Any]:
        """提交审批决定，写入 ``app.security.approval`` 供 DeepAgent 消费。

        REQ-APR-2: 必须先 consume 活跃审批请求（compare-and-consume）。
        前端必须传 ``approval_id`` + ``run_id``，后端验证通过后才写入决策。

        支持两种审批场景：
        - dangerous_tool：approval=True/False，decision="approve"/"deny"
        - directory_extension：decision="once"/"session"/"deny"，path/writable 描述目标
        - full_trust：decision="full_trust"，设置会话为 full_trust 模式（跳过所有审批）
        """
        # approval=False → 强制 deny（覆盖 decision 默认值 "approve"）
        effective_decision = "deny" if not req.approval else req.decision

        # REQ-APR-2: compare-and-consume 活跃请求
        # HIGH-2/HIGH-3 修复：强制要求 approval_id + run_id，移除开发兼容分支，
        # 消除可绕过 consume-once 的安全旁路。所有审批决策必须走 submit_approval。
        run_id = req.run_id or ""
        approval_id = req.approval_id or ""
        if not approval_id or not run_id:
            raise HTTPException(
                status_code=403,
                detail="approval_id 和 run_id 必填（consume-once 审批模式）",
            )

        consumed = await consume_approval(approval_id, run_id)
        if consumed is None:
            logger.warning(
                "approval rejected: consume failed",
                thread_id=req.thread_id,
                approval_id=approval_id,
                run_id=run_id,
            )
            raise HTTPException(
                status_code=409,
                detail="审批请求已失效（不存在、已过期、已消费或 run 不匹配）",
            )
        # consume 成功，thread_id 以注册表为准
        thread_id = consumed.thread_id

        # full_trust 决策：设置会话为 full_trust 模式，同时提交一个 approve 决策
        # REQ-APR-3: full_trust 只在 consume 成功后开启
        if effective_decision == "full_trust":
            from app.main import get_sandbox

            sandbox = get_sandbox()
            await sandbox.set_full_trust(thread_id, True)
            logger.info("full_trust enabled", thread_id=thread_id)
            # 提交 approve 决策让当前审批流继续
            await submit_approval(
                approval_id,
                ApprovalResult(
                    decision=ApprovalDecision.APPROVE,
                    path=req.path,
                    writable=req.writable,
                ),
                run_id,
            )
        else:
            ok = await submit_approval(
                approval_id,
                ApprovalResult(
                    decision=ApprovalDecision(effective_decision),
                    path=req.path,
                    writable=req.writable,
                ),
                run_id,
            )
            if not ok:
                raise HTTPException(
                    status_code=409,
                    detail="审批提交失败：请求已失效",
                )

        # LangSmith trace：区分用户批准 / 拒绝
        if req.approval:
            span_name = "approval.user_approve"
            action = "user_approve"
        else:
            span_name = "approval.user_reject"
            action = "user_reject"

        with trace_span(
            span_name,
            thread_id=thread_id,
            action=action,
            decision=effective_decision,
            path=req.path,
            args=mark_redacted(),
        ):
            pass

        # FR-6.1/6.2: 回填 observation_tool_call.approval_decision
        if req.run_id:
            try:
                sink = get_observation_sink()
                tc_id = req.tool_call_id
                if not tc_id:
                    tc_id = sink.find_pending_approval_tool_call_sync(req.run_id)
                if tc_id:
                    sink.update_tool_call_approval_sync(
                        tool_call_id=tc_id,
                        approval_decision=effective_decision,
                        approved=req.approval,
                    )
            except Exception as exc:  # noqa: BLE001 — 回填失败不阻塞审批
                logger.warning(
                    "observation approval backfill failed",
                    run_id=req.run_id,
                    error=str(exc),
                )

        # FR-9.1: 隐式反馈信号 — 审批 deny → implicit_bad
        if req.run_id and not req.approval:
            from app.observability.feedback import record_implicit_bad

            await record_implicit_bad(req.run_id, reason="rejected_dangerous_tool")

        logger.info(
            "approval submitted",
            thread_id=thread_id,
            approval=req.approval,
            decision=req.decision,
            approval_id=approval_id or None,
        )
        return {"ok": True}

    @app.post("/api/chat/abort")
    async def chat_abort(req: AbortRequest) -> dict[str, Any]:
        """设置中止标志，SSE handler 在下一轮迭代退出。"""
        await set_abort(req.thread_id)
        logger.info("abort flag set", thread_id=req.thread_id)
        # FR-9.1: 隐式反馈信号 — 用户 abort → implicit_bad
        if req.run_id:
            from app.observability.feedback import record_implicit_bad

            await record_implicit_bad(req.run_id, reason="aborted")
        return {"ok": True}

    @app.post("/api/chat/pause")
    async def chat_pause(req: AbortRequest) -> dict[str, Any]:
        """设置暂停标志，DeepAgent 在迭代起点进入等待。"""
        await set_pause(req.thread_id)
        logger.info("pause flag set", thread_id=req.thread_id)
        return {"ok": True}

    @app.post("/api/chat/resume")
    async def chat_resume(req: AbortRequest) -> dict[str, Any]:
        """清除暂停标志并唤醒等待中的 DeepAgent。"""
        await clear_pause(req.thread_id)
        logger.info("pause cleared", thread_id=req.thread_id)
        return {"ok": True}

    @app.post("/api/chat")
    async def chat(req: ChatRequest) -> EventSourceResponse:
        """SSE 流式聊天端点。

        事件契约（chat-rendering-trace-v2 扩展，详见 ``_event_generator`` docstring）：
        - ``token`` / ``reasoning`` / ``tool_call`` / ``tool_result`` / ``delegation``
        - ``todo_update`` / ``approval_request`` / ``done`` / ``error``
        """
        # SSE 心跳：每 15 秒发送一次 ping 事件，防止代理/浏览器在长运行期间断开连接
        # （30 秒在 Tauri webview / 部分代理下仍可能断开，缩短到 15 秒）
        return EventSourceResponse(
            _event_generator(req),
            ping=15,
            ping_message_factory=lambda: {"event": "ping", "data": "{}"},
        )

    @app.post("/api/chat/compact")
    async def chat_compact(req: CompactRequest) -> dict[str, Any]:
        """压缩会话历史：把早期消息摘要化，保留最近 2 条。

        流程:
        1. 从 checkpointer 读取 ``thread_id`` 的历史 messages
        2. 若消息不足 4 条，返回 ``{ok: false, error: "消息不足"}``
        3. 调 LLM 把前 N-2 条压缩成摘要
        4. 用 ``[SystemMessage(summary), 最近 2 条]`` 写回 checkpoint
        5. 返回 ``{ok: true, summary: str, compressed_count: int}``

        LLM 失败时不写回 checkpoint，返回 ``{ok: false, error: str}``。

        REQ-CHAT-4: compact 必须与 live run 互斥，避免并发写同一份 checkpoint。
        """
        # REQ-CHAT-4: 活跃 run 期间拒绝 compact，避免并发写 checkpoint
        active_run = await get_active_run(req.thread_id)
        if active_run is not None:
            return {
                "ok": False,
                "error": "有活跃请求，请等待完成后再压缩",
                "run_id": active_run.run_id,
            }

        from langchain_core.messages import SystemMessage

        from app.memory import summarize_messages
        from app.memory.compact_utils import split_messages_for_compact
        # 延迟 import：测试通过 monkeypatch app.main.get_async_checkpointer
        from app.main import get_async_checkpointer

        checkpointer = await get_async_checkpointer()
        # T1.7: fail-fast — 若 checkpointer 不支持写回，立即返回错误，
        # 避免无谓的 summarize 后才发现无法写回
        if not (hasattr(checkpointer, "aput") or hasattr(checkpointer, "put")):
            return {"ok": False, "error": "checkpointer 不支持写回（缺少 aput/put 方法）"}

        # T1.5: checkpoint_ns 是 LangGraph saver 必需字段（主线程命名空间为空字符串）
        config = {"configurable": {"thread_id": req.thread_id, "checkpoint_ns": ""}}

        # 1. 读取历史 messages
        try:
            if hasattr(checkpointer, "aget"):
                checkpoint = await checkpointer.aget(config)
            elif hasattr(checkpointer, "get"):
                # H2: 同步 SqliteSaver.get() 用 asyncio.to_thread 避免阻塞事件循环
                checkpoint = await asyncio.to_thread(checkpointer.get, config)
            else:
                checkpoint = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("compact: load checkpoint failed", thread_id=req.thread_id, error=str(exc))
            return {"ok": False, "error": f"加载 checkpoint 失败: {exc}"}

        if not checkpoint:
            return {"ok": False, "error": "无 checkpoint 可压缩"}

        channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
        messages = list(channel_values.get("messages", []))
        if len(messages) < 4:
            return {"ok": False, "error": "消息不足，无需压缩"}

        # 2. 调 LLM 压缩前 N-2 条
        # M3: 不能盲取最后 2 条 —— 若 keep_recent 第一条是 ToolMessage，
        # 必须把发起对应 tool_call 的 AIMessage 也移入 keep_recent，
        # 否则压缩后会留下孤立的 ToolMessage，破坏配对（ToolMessage 必须紧跟
        # 在发起 tool_call 的 AIMessage 之后，否则 LangGraph 还原状态会报错）。
        to_compress, keep_recent = split_messages_for_compact(messages)
        try:
            summary = await summarize_messages(to_compress)
        except Exception as exc:  # noqa: BLE001
            logger.warning("compact: summarize failed", thread_id=req.thread_id, error=str(exc))
            return {"ok": False, "error": f"摘要生成失败: {exc}"}

        # 3. 写回 checkpoint：[SystemMessage(summary), *keep_recent]
        new_messages = [SystemMessage(content=summary), *keep_recent]
        new_channel_values = {**channel_values, "messages": new_messages}
        # H1: 生成新 checkpoint ID 并设置 parent_checkpoint_id，避免覆盖旧记录、
        # 破坏历史链（旧实现直接继承 checkpoint["id"]，aput 会覆盖原 checkpoint，
        # 且 parent_checkpoint_id 为 None，丢失时间旅行能力）。
        new_checkpoint_id = str(uuid.uuid4())
        new_checkpoint = {
            **checkpoint,
            "id": new_checkpoint_id,
            "parent_checkpoint_id": checkpoint.get("id"),
            "channel_values": new_channel_values,
        }
        new_config = {
            **config,
            "configurable": {
                **config.get("configurable", {}),
                # T2.2: LangGraph aput 用 config.configurable.checkpoint_id 作为
                # parent_checkpoint_id 存入 DB（而非 checkpoint["parent_checkpoint_id"]）。
                # 因此这里必须传旧 checkpoint 的 id（父节点），不是新 checkpoint 的 id。
                "checkpoint_id": checkpoint.get("id"),
                "checkpoint_ns": "",
            },
        }

        # T1.5: new_versions 必须是 dict（channel -> version），标识本次写回
        # 更新的 channel。为 messages channel 生成新 version，保留其他 channel 原版本。
        new_versions = {
            **(checkpoint.get("channel_versions") or {}),
            "messages": str(uuid.uuid4()),
        }

        try:
            if hasattr(checkpointer, "aput"):
                # M1: 第四个参数 new_versions 应为 dict 而非 list
                await checkpointer.aput(new_config, new_checkpoint, {}, new_versions)
            elif hasattr(checkpointer, "put"):
                # H2: 同步 SqliteSaver.put() 用 asyncio.to_thread 避免阻塞事件循环
                await asyncio.to_thread(
                    checkpointer.put, new_config, new_checkpoint, {}, new_versions
                )
            else:
                return {"ok": False, "error": "checkpointer 不支持写回"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("compact: write checkpoint failed", thread_id=req.thread_id, error=str(exc))
            return {"ok": False, "error": f"写回 checkpoint 失败: {exc}"}

        logger.info(
            "compact done",
            thread_id=req.thread_id,
            compressed_count=len(to_compress),
            summary_len=len(summary),
        )
        return {
            "ok": True,
            "summary": summary,
            "compressed_count": len(to_compress),
        }

    @app.get("/api/chat/pending-approval")
    async def chat_pending_approval(thread_id: str) -> dict[str, Any]:
        """查询指定会话是否有待审批决策。

        用于前端刷新后恢复审批状态：若用户刷新页面时仍有未处理的审批请求，
        前端可调用此端点检查并重新显示审批弹窗。
        """
        pending = await has_pending_approval(thread_id)
        return {"ok": True, "pending": pending}

    @app.get("/api/chat/result/{trace_id}")
    async def chat_result(trace_id: str) -> dict[str, Any]:
        """查询指定 trace_id 的最终结果（SSE 断连后恢复用）。

        - 已完成：``{status: "completed", result_text, token_count, trace_id}``
        - 进行中：``{status: "pending", trace_id}``
        - 未找到：404
        """
        sink = get_observation_sink()
        row = await asyncio.to_thread(sink.get_run_sync, trace_id)
        if row is None:
            raise HTTPException(status_code=404, detail="trace not found")
        if row.get("ended_at") is None:
            return {"status": "pending", "trace_id": trace_id}
        return {
            "status": "completed",
            "result_text": row.get("result_text") or "",
            "token_count": row.get("result_token_count") or 0,
            "trace_id": trace_id,
            "agent_mode": row.get("agent_mode") or "",
        }
