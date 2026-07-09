"""DeepAgent SSE 流式事件驱动。

从 ``app.deepagent.agent`` 拆出（Phase 2.3），保持公共 API 不变。

职责:
- ``_stream_agent_events``：驱动 ``agent.astream(stream_mode="values")``，
  尊重 ``interrupt_on``，把 LangGraph state 转换为前端 SSE 事件。

SSE 事件映射:
- ``state.todos`` 变化 → ``todo_update``（原生 deepagents ``{content, status}`` schema）
- ``AIMessage`` with ``tool_calls`` → ``reasoning`` + ``tool_call``
- ``AIMessage`` without ``tool_calls`` → ``token``（最终回复）
- ``ToolMessage`` → ``tool_result``

导入方向：``agent.py`` → ``streaming.py``（单向，无循环）。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from uuid import uuid4

from loguru import logger

from app.observability.observation import get_observation_sink
from app.observability.trace import current_trace_id
from app.security.approval import get_abort_event
from app.sse.events import (
    make_sse_event,
    make_todo_update_event,
    make_tool_call_event,
    make_tool_result_event,
)

__all__ = ["_stream_agent_events"]


async def _stream_agent_events(
    agent: Any, inputs: Any, config: dict, source: str = "deep"
) -> AsyncIterator[dict[str, str]]:
    """驱动 ``agent.astream(stream_mode="values")``，尊重 ``interrupt_on``。

    ``astream_events`` 不尊重 ``interrupt_on``（会直接执行工具），
    MUST 用 ``astream`` + ``stream_mode="values"`` 才能在 tools 节点前暂停。

    SSE 事件映射:
    - ``state.todos`` 变化 → ``todo_update``（原生 ``{content, status}`` schema，
      由 deepagents ``TodoListMiddleware`` 维护）
    - AIMessage with tool_calls → ``reasoning`` + ``tool_call`` SSE
    - AIMessage without tool_calls → ``token``（最终回复，strip_think 后一次性 yield）
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
    """
    from langchain_core.messages import AIMessage, ToolMessage
    from app.utils.text import strip_think

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
    )

    # 去重集合：基于消息签名避免 LangGraph astream 在 interrupt/resume 后
    # 重发已处理过的消息（astream 每次从图起点遍历，会重复 emit 历史状态）。
    # 签名 = msg_type + content_hash + tool_call_ids，覆盖 AIMessage 和 ToolMessage。
    _seen_signatures: set[str] = set()

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

    # 诊断：记录 astream 首次 state 到达的耗时，帮助定位 LLM 调用阻塞
    _astream_start = asyncio.get_event_loop().time()
    _first_state_seen = False

    # 追踪已处理的消息数量，避免 LangGraph astream 一次 emit 多个新消息时
    # 只处理 messages[-1] 而遗漏前面的 tool_result（典型：并行工具调用后
    # state 同时包含多个 ToolMessage，只处理最后一条会导致前面工具卡「运行中」）。
    _processed_count = 0

    # 追踪 state.todos 快照，diff 检测 deepagents TodoListMiddleware 更新
    _last_todos: list[dict] = []

    async for state in agent.astream(inputs, config=config, stream_mode="values"):
        if not _first_state_seen:
            _first_state_seen = True
            elapsed = asyncio.get_event_loop().time() - _astream_start
            logger.info(
                "stream_agent_events: first state arrived after {elapsed:.2f}s",
                thread_id=thread_id,
                elapsed=elapsed,
                source=source,
            )
        if abort_event.is_set():
            raise asyncio.CancelledError("aborted")

        # 读取 deepagents 原生 state.todos（TodoListMiddleware 维护），
        # diff 检测变化后 yield todo_update 事件（原生 {content, status} schema）。
        current_todos = state.get("todos", []) if hasattr(state, "get") else []
        if current_todos != _last_todos:
            await _obs("todo_update", {"todos": current_todos, "task_id": thread_id})
            yield make_todo_update_event(current_todos, task_id=thread_id)
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
            logger.info(
                "stream_agent_events: msg_type={msg_type} msg_count={msg_count} source={source}",
                msg_type=msg_type,
                msg_count=len(messages),
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
                    # LLM 的 content 通常包含 💧... 计划 ... 或纯文本计划
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
                    from app.utils.text import split_think, strip_tool_call_xml
                    plan_text = strip_tool_call_xml(plan_text)
                    reasoning, visible = split_think(plan_text)
                    # 优先展示 reasoning（think 块内），其次展示 visible（非 think 内容）
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
                    content = msg.content
                    if isinstance(content, list):
                        # 兼容 list 内容块
                        content = "".join(
                            block if isinstance(block, str)
                            else block.get("text", "") if isinstance(block, dict)
                            else ""
                            for block in content
                        )
                    text = strip_think(content if isinstance(content, str) else str(content))
                    # 防御性剥离：避免 XML 格式工具调用文本泄露到最终回复 token 流。
                    from app.utils.text import strip_tool_call_xml
                    text = strip_tool_call_xml(text)
                    if text:
                        await _obs("token", {"content": text})
                        yield make_sse_event("token", text)
