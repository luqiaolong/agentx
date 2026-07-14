"""DeepAgent SSE 流式事件驱动。

从 ``app.deepagent.agent`` 拆出（Phase 2.3），保持公共 API 不变。

职责:
- ``stream_agent_events``：驱动 ``agent.astream(stream_mode=["custom", "values", "messages"])``，
  尊重 ``interrupt_on``，把 LangGraph state 转换为前端 SSE 事件。
  ``custom`` 模式用于透传工具节点内部通过 ``get_stream_writer()`` 写入的事件
  （如 Supervisor ``delegate_to_expert`` 透传的 Expert approval_request 等）。
  ``messages`` 模式用于实时消费 LLM 的 ``AIMessageChunk``，把 ``<think>...</think>`` 块
  以 ``reasoning_delta`` 事件实时推给前端。

SSE 事件映射:
- ``state.todos`` 变化 → ``todo_update``（原生 deepagents ``{content, status}`` schema）
- ``AIMessageChunk`` 中的 ``<think>`` 块 → ``reasoning_delta``（实时增量）
- ``AIMessageChunk`` 中的可见文本 → ``token``（实时增量）
- ``AIMessage`` with ``tool_calls`` → ``token_rollback``（撤回误推 token）+ ``reasoning`` + ``tool_call``
- ``AIMessage`` without ``tool_calls`` → flush 残留可见文本为 ``token``
- ``ToolMessage`` → ``tool_result``
- ``custom`` stream 事件 → 直接透传 yield（工具节点内部写入的 SSE 事件）

导入方向：``agent.py`` → ``streaming.py``（单向，无循环）。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from uuid import uuid4

from loguru import logger

from app.config import get_settings
from app.observability.observation import get_observation_sink
from app.observability.trace import current_trace_id
from app.security.approval import get_abort_event
from app.sse.events import (
    make_sse_event,
    make_todo_update_event,
    make_tool_call_event,
    make_tool_result_event,
)
from app.utils.text import ThinkFilter, extract_chunk_text

__all__ = ["stream_agent_events"]


async def stream_agent_events(
    agent: Any,
    inputs: Any,
    config: dict,
    source: str = "work",
    *,
    seen_signatures: set[str] | None = None,
) -> AsyncIterator[dict[str, str]]:
    """驱动 ``agent.astream(stream_mode=["custom", "values", "messages"])``，尊重 ``interrupt_on``。

    ``astream_events`` 不尊重 ``interrupt_on``（会直接执行工具），
    MUST 用 ``astream`` 才能在 tools 节点前暂停。
    额外开启 ``messages`` 模式以实时消费 ``AIMessageChunk``，把 ``<think>`` 块以
    ``reasoning_delta`` 事件增量推给前端。

    SSE 事件映射:
    - ``state.todos`` 变化 → ``todo_update``（原生 ``{content, status}`` schema，
      由 deepagents ``TodoListMiddleware`` 维护）
    - ``AIMessageChunk`` 中的 ``<think>`` 块 → ``reasoning_delta``（实时增量）
    - AIMessage with ``tool_calls`` → ``reasoning``（非 think 的计划文本）+ ``tool_call`` SSE
    - AIMessage without ``tool_calls`` → ``token``（最终回复，``strip_think`` 后一次性 yield）
    - ToolMessage → ``tool_result`` SSE

    在 ``interrupt_on`` 处暂停时，最后一个 state 的 messages[-1]
    是 AIMessage（含 tool_calls），此处 yield tool_call 后流结束，
    调用方 ``_is_interrupted`` 返回 True 进入审批流程。

    Args:
        agent: 已编译的 LangGraph agent。
        inputs: agent 输入，None 表示从 interrupt 处恢复。
        config: LangGraph 运行配置。
        source: SSE 事件 source 标识，默认 "deep"（DeepAgent）。
            Supervisor 传 "work"，Expert 传 "coding" 等。
        seen_signatures: 可选外部去重集合。若提供，使用它替代内部新建的 set，
            用于跨 ``stream_agent_events`` 调用（如 approval_runner resume）
            共享"已 yield 的消息签名"，避免 astream resume 时重发历史消息
            被重复 yield（root cause: trace=64851677fced422c）。
    """
    from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
    from app.utils.text import split_think, strip_think, strip_tool_call_xml

    thread_id = config.get("configurable", {}).get("thread_id", "")
    abort_event = await get_abort_event(thread_id)

    # FR-5.1: 观测中心 event append — run_id 从 ContextVar 读取（由 chat.py bind_trace 设置）
    run_id = current_trace_id() or ""
    _seq_counter = 0

    async def _obs(event_type: str, payload: dict[str, Any]) -> None:
        """写 observation_event，失败不阻塞 SSE 流（FR-2.3 隔离）。"""
        nonlocal _seq_counter
        if not run_id:
            return
        _seq_counter += 1
        try:
            await get_observation_sink().append_event(
                run_id, _seq_counter, event_type, payload
            )
        except Exception:  # noqa: BLE001 — 观测失败不阻塞 agent
            pass

    logger.info(
        "stream_agent_events: start streaming",
        thread_id=thread_id,
        inputs_type=type(inputs).__name__,
        source=source,
        is_resume=inputs is None,
        seen_signatures_count=len(seen_signatures) if seen_signatures else 0,
    )

    # 去重集合：基于消息签名避免 LangGraph astream 在 interrupt/resume 后
    # 重发已处理过的消息（astream 每次从图起点遍历，会重复 emit 历史状态）。
    # 签名 = msg_type + content_hash + tool_call_ids，覆盖 AIMessage 和 ToolMessage。
    # 跨调用共享（approval_runner resume 场景）：由调用方传入 seen_signatures；
    # 未传入时新建一次性 set（保持原行为兼容测试桩 stream_fn）。
    _seen_signatures: set[str] = (
        seen_signatures if seen_signatures is not None else set()
    )

    def _msg_signature(msg: Any) -> str:
        """为消息生成唯一签名，用于去重。"""
        msg_type = type(msg).__name__
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            content = "".join(
                block if isinstance(block, str)
                else block.get("text", "") if isinstance(block, dict)
                else ""
                for block in content
            )
        content_hash = str(hash(content)) if content else ""
        tc_ids = ""
        tcs = getattr(msg, "tool_calls", None) or []
        if tcs:
            tc_ids = "|".join(
                str(tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", ""))
                for tc in tcs
            )
        return f"{msg_type}:{content_hash}:{tc_ids}"

    async def _process_message_chunk(payload: Any) -> None:
        """处理 ``stream_mode="messages"`` 的实时 token 块。

        用 ``ThinkFilter`` 实时拆分 ``<think>`` 块和可见文本：
        - think 块内容 → ``reasoning_delta`` 事件（实时增量）
        - 可见文本 → ``token`` 事件（实时增量）

        可见文本有 ``max_hold`` 字符的缓冲延迟（防 ``<think>`` 标签前缀跨 chunk），
        残留部分在完整 ``AIMessage`` 到达后由 ``flush`` 补发。

        若最终 ``AIMessage`` 带 ``tool_calls``，说明误把计划文本当 token 推了，
        由 values 模式发 ``token_rollback`` 撤回，再以 ``reasoning`` 事件重发。
        """
        nonlocal _content_filter, _messages_mode_seen, _token_pushed
        _messages_mode_seen = True

        # LangGraph messages 模式 payload 通常为 (message_chunk, metadata)
        if isinstance(payload, tuple) and len(payload) >= 1:
            msg_chunk = payload[0]
        else:
            msg_chunk = payload
        if not isinstance(msg_chunk, AIMessageChunk):
            return

        raw_text = extract_chunk_text(msg_chunk, strip=False)
        if not raw_text:
            return

        if _content_filter is None:
            _content_filter = ThinkFilter(
                max_hold=get_settings().think_filter_max_hold,
                retain_think=True,
            )

        # feed 返回当前可安全输出的可见文本（已剥离 think 块）
        visible_delta = _content_filter.feed(raw_text)
        reasoning_delta = _content_filter.take_think()
        if reasoning_delta:
            await _obs(
                "reasoning_delta",
                {"delta": reasoning_delta, "source": source},
            )
            yield make_sse_event(
                "reasoning_delta",
                {"delta": reasoning_delta, "source": source},
            )
        if visible_delta:
            _token_pushed = True
            await _obs("token", {"content": visible_delta, "live": True})
            yield make_sse_event("token", visible_delta)

    # 诊断：记录 astream 首次 state 到达的耗时，帮助定位 LLM 调用阻塞
    _astream_start = asyncio.get_event_loop().time()
    _first_state_seen = False

    # 追踪已处理的消息数量，避免 LangGraph astream 一次 emit 多个新消息时
    # 只处理 messages[-1] 而遗漏前面的 tool_result（典型：并行工具调用后
    # state 同时包含多个 ToolMessage，只处理最后一条会导致前面工具卡「运行中」）。
    _processed_count = 0

    # 追踪 state.todos 快照，diff 检测 deepagents TodoListMiddleware 更新
    _last_todos: list[dict] = []

    # 实时 think 块解析器：只处理 AIMessageChunk，跨 chunk 拼接 <think> 块。
    # 在完整的 AIMessage 到达后（values 模式）重置，供下一条消息复用。
    _content_filter: ThinkFilter | None = None
    _messages_mode_seen = False
    # 追踪当前 AIMessage 是否已通过 messages 模式推送过 token 事件。
    # 若最终 AIMessage 带 tool_calls，需发 token_rollback 撤回误推的 token。
    _token_pushed = False

    # stream_mode=["custom", "values", "messages"]：
    # - custom: 工具节点内部通过 get_stream_writer() 写入的 passthrough 事件
    #   （如 delegate_to_expert 透传的 Expert approval_request / tool_call 等）
    # - values: 每次 state 更新的完整快照（用于 todos diff + messages 处理）
    # - messages: LLM 实时 token 流（AIMessageChunk），用于提取 <think> 块并
    #   以 reasoning_delta 事件实时推送
    async for chunk in agent.astream(
        inputs, config=config, stream_mode=["custom", "values", "messages"]
    ):
        if abort_event.is_set():
            raise asyncio.CancelledError("aborted")

        # 兼容：非 tuple chunk（如测试桩直接 yield state）作为 values 处理
        if isinstance(chunk, tuple) and len(chunk) == 2:
            mode, payload = chunk
            if mode == "custom":
                # 工具节点内部透传的 SSE 事件，直接 yield 给前端
                if isinstance(payload, dict) and "event" in payload:
                    yield payload
                continue
            if mode == "messages":
                # 实时 LLM token 流：只处理 AIMessageChunk，提取 think 块
                async for event in _process_message_chunk(payload):
                    yield event
                continue
            # mode == "values"
            state = payload
        else:
            state = chunk

        if not _first_state_seen:
            _first_state_seen = True
            elapsed = asyncio.get_event_loop().time() - _astream_start
            logger.info(
                "stream_agent_events: first state arrived after {elapsed:.2f}s",
                thread_id=thread_id,
                elapsed=elapsed,
                source=source,
            )

        # 读取 deepagents 原生 state.todos（TodoListMiddleware 维护），
        # diff 检测变化后 yield todo_update 事件（原生 {content, status} schema）。
        # 注入 source 标识（work/coding 等），前端据此按角色分组渲染任务流。
        # parent_task_id 在主路径（单 agent）不传，仅 Team 子任务路径由
        # ``_emit_todo_in_progress`` 注入。
        current_todos = state.get("todos", []) if hasattr(state, "get") else []
        if current_todos != _last_todos:
            await _obs("todo_update", {"todos": current_todos, "task_id": thread_id, "source": source})
            yield make_todo_update_event(current_todos, task_id=thread_id, source=source)
            _last_todos = list(current_todos)

        messages = state.get("messages", []) if hasattr(state, "get") else []
        if not messages:
            logger.debug("stream_agent_events: empty messages, skipping")
            continue

        # 处理所有新增消息（从 _processed_count 开始），而不是只处理 messages[-1]。
        # 防御性：若 _processed_count >= len(messages)（如测试用的 _FakeAgent
        # 每次只返回单条消息的 state，非 LangGraph 的累积 messages），则回退到
        # 处理 messages[-1] 以保持兼容。
        if _processed_count >= len(messages):
            new_messages = [messages[-1]]
        else:
            new_messages = messages[_processed_count:]
        _processed_count = len(messages)

        if not new_messages:
            logger.debug(
                "stream_agent_events: no new messages msg_count={msg_count} source={source}",
                msg_count=len(messages),
                source=source,
            )
            continue

        for msg in new_messages:
            sig = _msg_signature(msg)
            if sig in _seen_signatures:
                logger.debug(
                    "stream_agent_events: duplicate message skipped sig={sig} msg_type={msg_type}",
                    sig=sig,
                    msg_type=type(msg).__name__,
                )
                continue
            _seen_signatures.add(sig)
            msg_type = type(msg).__name__
            # 工具调用模式可见化：ToolMessage 记录 tool_name，AIMessage 记录 tc_count，
            # 便于从 INFO 日志判断是否陷入只读工具循环（无需开 DEBUG）
            _tool_name = ""
            _tc_count = 0
            if isinstance(msg, ToolMessage):
                _tool_name = getattr(msg, "name", "") or ""
            elif isinstance(msg, AIMessage):
                _tc_count = len(getattr(msg, "tool_calls", []) or [])
            logger.info(
                "stream_agent_events: msg_type={msg_type} msg_count={msg_count}"
                " tool_name={tool_name} tc_count={tc_count} source={source}",
                msg_type=msg_type,
                msg_count=len(messages),
                tool_name=_tool_name,
                tc_count=_tc_count,
                source=source,
            )

            if isinstance(msg, ToolMessage):
                # 工具执行完成 → tool_result SSE + todo_update（任务级进度）
                tool_name = getattr(msg, "name", "") or ""
                tool_call_id = getattr(msg, "tool_call_id", "") or str(uuid4())
                content = getattr(msg, "content", "")
                logger.debug(
                    "stream_agent_events: ToolMessage name={tool_name} tool_call_id={tool_call_id} content_len={content_len} source={source}",
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    content_len=len(content) if isinstance(content, str) else 0,
                    source=source,
                )
                if isinstance(content, list):
                    content = "".join(
                        block if isinstance(block, str)
                        else block.get("text", "") if isinstance(block, dict)
                        else ""
                        for block in content
                    )
                await _obs("tool_result", {"id": tool_call_id, "name": tool_name, "result": content, "source": source})
                yield make_tool_result_event(tool_call_id, tool_name, content, source=source)
                logger.info(
                    "stream_agent_events: yielded tool_result",
                    thread_id=thread_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    source=source,
                )

            elif isinstance(msg, AIMessage):
                tc_count = len(getattr(msg, "tool_calls", []) or [])
                content_preview = str(msg.content)[:100] if msg.content else ""
                logger.debug(
                    "stream_agent_events: AIMessage tc_count={tc_count} content_preview={content_preview} source={source}",
                    tc_count=tc_count,
                    content_preview=content_preview,
                    source=source,
                )
                if getattr(msg, "tool_calls", None):
                    # AIMessage with tool_calls → 先展示思考计划，再 yield tool_call
                    # LLM 的 content 通常包含 <think>...</think> 或纯文本计划
                    content = msg.content
                    if isinstance(content, list):
                        content = "".join(
                            block if isinstance(block, str)
                            else block.get("text", "") if isinstance(block, dict)
                            else ""
                            for block in content
                        )
                    plan_text = str(content) if content else ""
                    # 防御性剥离：部分 OpenAI 兼容推理模型（典型如 MiniMax-M3）在
                    # tool_calls 字段已正确填充时，仍会在 content 中重复输出 XML 格式
                    # 工具调用文本。剥离后再 split_think，避免 XML 块泄露到 reasoning 事件。
                    plan_text = strip_tool_call_xml(plan_text)
                    reasoning, visible = split_think(plan_text)
                    if _messages_mode_seen:
                        # <think> 块已通过 reasoning_delta 实时推送，可见文本已通过
                        # token 事件实时推送。若 token 被误推（模型在 tool_calls 前
                        # 先输出了可见文本），发 token_rollback 撤回，再以 reasoning
                        # 事件重发可见计划文本。
                        if _token_pushed:
                            await _obs("token_rollback", {})
                            yield make_sse_event("token_rollback", {})
                            _token_pushed = False
                        display_plan = visible.strip()
                    else:
                        # 无 messages 模式（测试桩或旧模型）时保持原行为：优先展示
                        # think 块内容，否则展示可见计划文本。
                        display_plan = reasoning.strip() if reasoning.strip() else visible.strip()
                    if display_plan:
                        # yield reasoning 事件供前端展示思考过程
                        await _obs("reasoning", {"content": display_plan, "source": source})
                        yield make_sse_event(
                            "reasoning",
                            {"content": display_plan, "source": source},
                        )
                    # 再 yield 每个 tool_call
                    for tc in msg.tool_calls:
                        if isinstance(tc, dict):
                            tc_name = tc.get("name", tc.get("tool", "unknown"))
                            tc_args = tc.get("args", {}) or {}
                            tc_id = tc.get("id") or str(uuid4())
                        else:
                            tc_name = getattr(tc, "name", "unknown")
                            tc_args = getattr(tc, "args", {}) or {}
                            tc_id = getattr(tc, "id", None) or str(uuid4())
                        await _obs("tool_call", {"id": tc_id, "name": tc_name, "args": tc_args, "source": source})
                        yield make_tool_call_event(tc_id, tc_name, tc_args, source=source)
                elif getattr(msg, "content", ""):
                    # AIMessage without tool_calls → 最终回复
                    if _messages_mode_seen and _content_filter is not None:
                        # messages 模式已实时推送了大部分可见文本，
                        # 此处只需 flush ThinkFilter 残留（max_hold 缓冲的几个字符）
                        tail = _content_filter.flush()
                        if tail:
                            tail = strip_tool_call_xml(tail)
                            if tail:
                                await _obs("token", {"content": tail, "live": False})
                                yield make_sse_event("token", tail)
                    else:
                        # 无 messages 模式（测试桩或旧模型）：从完整 content 一次性 yield
                        content = msg.content
                        if isinstance(content, list):
                            content = "".join(
                                block if isinstance(block, str)
                                else block.get("text", "") if isinstance(block, dict)
                                else ""
                                for block in content
                            )
                        text = strip_think(content if isinstance(content, str) else str(content))
                        text = strip_tool_call_xml(text)
                        if text:
                            await _obs("token", {"content": text})
                            yield make_sse_event("token", text)

                # 完整的 AIMessage 已处理完毕，重置 think 解析器供下一条消息使用
                _content_filter = None
                _token_pushed = False

    logger.info(
        "stream_agent_events: streaming completed",
        thread_id=thread_id,
        source=source,
        is_resume=inputs is None,
        first_state_seen=_first_state_seen,
        processed_count=_processed_count,
        seen_signatures_count=len(_seen_signatures),
    )
