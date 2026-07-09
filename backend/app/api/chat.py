"""聊天 SSE 路由 + 审批 / 中止 / 压缩端点。"""

from __future__ import annotations

from typing import Any, AsyncIterator

from fastapi import FastAPI
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
    is_aborted,
    set_abort,
    set_pause,
    submit_approval,
)
from app.config import get_settings
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
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("clear checkpoint failed", thread_id=thread_id, error=str(exc))


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
    - ``todo_update``   — DeepAgent 任务列表更新。
    - ``approval_request`` — 危险工具/目录扩展审批请求（含 tool_name / args / preview）。
    - ``team_plan``     — AgentTeam Orchestrator 生成的子任务计划（data 为 JSON
                          ``{"plan": [{"agent","input","purpose"}], "reasoning": str}``）。
    - ``team_progress`` — AgentTeam 子任务状态变化（data 为 JSON
                          ``{"agent": str, "status": "running"|"done"|"error", "message?": str}``）。
    - ``team_result``   — AgentTeam 子任务结果摘要（data 为 JSON ``{"agent": str, "summary": str}``）。
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
    from app.main import get_async_checkpointer, get_sandbox, run_router

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

        try:
            # /reset：清空 checkpointer + 沙箱（当不持久化时）
            if req.message.startswith("/reset"):
                await _clear_thread_state(req.thread_id)
                if not settings.persist_authorized_dirs:
                    await get_sandbox().clear(req.thread_id)
                    yield {"event": "token", "data": "已清空会话状态与授权目录"}
                else:
                    yield {"event": "token", "data": "已清空会话状态（授权目录已持久化，未清空）"}
                yield {"event": "done", "data": "{}"}
                return

            # 其他消息：走 Router 场景分发（传入 checkpointer 加载历史）
            checkpointer = await get_async_checkpointer()
            # coding_team 模式受 agents.teams.coding.enabled 开关控制
            effective_agent_mode = req.agent_mode
            if req.agent_mode == "coding_team" and not settings.agents.coding_team_enabled:
                effective_agent_mode = "coding"

            # FR-4.3/4.4/4.5: dual_trace 包裹 run_router，自动写 observation_run.start/end
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
                    async for event in run_router(
                        req.message,
                        req.thread_id,
                        checkpointer=checkpointer,
                        permission_mode=req.permission_mode,
                        agent_mode=effective_agent_mode,
                        workspace_path=req.workspace_path,
                        revoked_paths=req.revoked_paths,
                    ):
                        # 检查中止标志
                        if await is_aborted(req.thread_id):
                            # 中止事件也带 trace_id，方便用户报"任务卡死"问题时定位
                            obs_ctx.add_metadata("error_type", "aborted")
                            obs_ctx.add_metadata("error_message", "user aborted")
                            yield {
                                "event": "error",
                                "data": f"用户已中止 | trace={trace_id}",
                            }
                            await clear_abort(req.thread_id)
                            return
                        if event.get("event") == "token":
                            assistant_content_parts.append(str(event.get("data", "")))
                        yield event
                    # FR-4.4: 正常出口写 result_text + token_count
                    obs_ctx.add_metadata(
                        "result_text", "".join(assistant_content_parts).strip()
                    )
                    obs_ctx.add_metadata(
                        "result_token_count", len(assistant_content_parts)
                    )
                except Exception as inner_exc:
                    # FR-4.5: 异常分支写 error_type + error_message（在 dual_trace 退出前设置）
                    obs_ctx.add_metadata("error_type", type(inner_exc).__name__)
                    obs_ctx.add_metadata("error_message", str(inner_exc))
                    raise

        except Exception as exc:  # noqa: BLE001 — SSE 兜底，避免连接挂起
            logger.exception("SSE chat error", thread_id=req.thread_id)
            yield {
                "event": "error",
                "data": f"内部错误: {exc} | trace={trace_id}",
            }


def register_chat_routes(app: FastAPI) -> None:
    """注册聊天相关路由。

    含 ``POST /api/chat`` + ``POST /api/chat/approve`` + ``POST /api/chat/abort``
    + ``POST /api/chat/pause`` + ``POST /api/chat/resume`` + ``POST /api/chat/compact``。
    """

    @app.post("/api/chat/approve")
    async def chat_approve(req: ApproveRequest) -> dict[str, Any]:
        """提交审批决定，写入 ``app.security.approval`` 供 DeepAgent 消费。

        支持两种审批场景：
        - dangerous_tool：approval=True/False，decision="approve"/"deny"
        - directory_extension：decision="once"/"session"/"deny"，path/writable 描述目标

        若 ``auto_approve_after_seconds > 0`` 且倒计时归零，记录 auto_approve trace；
        否则按用户实际操作记录 user_approve / user_reject。
        """
        settings = get_settings()
        # approval=False → 强制 deny（覆盖 decision 默认值 "approve"）
        effective_decision = "deny" if not req.approval else req.decision
        await submit_approval(
            req.thread_id,
            ApprovalResult(
                decision=ApprovalDecision(effective_decision),
                path=req.path,
                writable=req.writable,
            ),
        )

        # LangSmith trace：区分用户批准 / 拒绝 / 自动批准
        if req.approval and settings.auto_approve_after_seconds > 0:
            # 自动批准由 SSE handler 倒计时触发后调本端点（approval=True）
            span_name = "approval.auto_approve"
            action = "auto_approve"
            auto_approved = True
        elif req.approval:
            span_name = "approval.user_approve"
            action = "user_approve"
            auto_approved = False
        else:
            span_name = "approval.user_reject"
            action = "user_reject"
            auto_approved = False

        with trace_span(
            span_name,
            thread_id=req.thread_id,
            action=action,
            auto_approved=auto_approved,
            decision=req.decision,
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
                        approval_decision=req.decision,
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
        elif req.run_id and auto_approved:
            from app.observability.feedback import record_implicit_ok

            await record_implicit_ok(req.run_id, reason="auto_approved")

        logger.info(
            "approval submitted",
            thread_id=req.thread_id,
            approval=req.approval,
            decision=req.decision,
            auto_approved=auto_approved,
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
        return EventSourceResponse(_event_generator(req))

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
        """
        from langchain_core.messages import SystemMessage

        from app.memory import summarize_messages
        # 延迟 import：测试通过 monkeypatch app.main.get_async_checkpointer
        from app.main import get_async_checkpointer

        checkpointer = await get_async_checkpointer()
        config = {"configurable": {"thread_id": req.thread_id}}

        # 1. 读取历史 messages
        try:
            if hasattr(checkpointer, "aget"):
                checkpoint = await checkpointer.aget(config)
            else:
                checkpoint = checkpointer.get(config)
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
        to_compress = messages[:-2]
        keep_recent = messages[-2:]
        try:
            summary = await summarize_messages(to_compress)
        except Exception as exc:  # noqa: BLE001
            logger.warning("compact: summarize failed", thread_id=req.thread_id, error=str(exc))
            return {"ok": False, "error": f"摘要生成失败: {exc}"}

        # 3. 写回 checkpoint：[SystemMessage(summary), *keep_recent]
        new_messages = [SystemMessage(content=summary), *keep_recent]
        new_channel_values = {**channel_values, "messages": new_messages}
        new_checkpoint = {**checkpoint, "channel_values": new_channel_values}

        try:
            if hasattr(checkpointer, "aput"):
                await checkpointer.aput(config, new_checkpoint, {"messages": "any"}, [])
            elif hasattr(checkpointer, "put"):
                checkpointer.put(config, new_checkpoint, {"messages": "any"}, [])
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
