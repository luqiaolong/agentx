"""DeepAgent SSE 流式事件驱动（公共 driver，Decision 5 拆分后）。

从 ``app.deepagent.agent`` 拆出（Phase 2.3），保持公共 API 不变。

职责（Decision 5 后）:
- ``stream_agent_events``：薄驱动，持有 ``agent.astream(stream_mode=["custom",
  "values", "messages"])`` 循环、abort 检测。逐 chunk 委托给
  ``StreamEventMapper`` 转换为 SSE 事件。
- 跨 resume 共享状态：调用方通过 ``stream_run_state`` 传入 ``StreamRunState``；
  兼容老调用方通过 ``seen_signatures`` 传入 dedup set（由
  ``adapt_seen_signatures`` 适配为 StreamRunState）。

SSE 事件映射（不变，由 ``StreamEventMapper`` 实现）:
- ``state.todos`` 变化 → ``todo_update``
- ``AIMessageChunk`` 中的 ``<think>`` 块 → ``reasoning_delta``（实时增量）
- ``AIMessageChunk`` 中的可见文本 → ``token``（实时增量）
- ``AIMessage`` with ``tool_calls`` → ``token_rollback`` + ``reasoning`` + ``tool_call``
- ``AIMessage`` without ``tool_calls`` → flush 残留可见文本为 ``token``
- ``ToolMessage`` → ``tool_result``
- ``custom`` stream 事件 → 直接透传 yield + 记录 observation

导入方向：``agent.py`` → ``streaming.py`` → ``stream_events.py``（单向，无循环）。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from loguru import logger

from app.observability.observation import get_observation_sink
from app.observability.trace import current_trace_id
from app.security.approval import get_abort_event
from app.deepagent.stream_events import (
    StreamEventMapper,
    StreamRunState,
    adapt_seen_signatures,
)

__all__ = ["stream_agent_events"]


async def stream_agent_events(
    agent: Any,
    inputs: Any,
    config: dict,
    source: str = "work",
    *,
    seen_signatures: set[str] | None = None,
    stream_run_state: StreamRunState | None = None,
) -> AsyncIterator[dict[str, str]]:
    """驱动 ``agent.astream(stream_mode=["custom", "values", "messages"])``。

    薄驱动（Decision 5）：持有 astream 循环、abort 检测、首 state 诊断日志；
    逐 chunk 委托给 ``StreamEventMapper`` 转换。跨 resume 共享状态由
    ``stream_run_state`` 承载；``seen_signatures`` 兼容参数经
    ``adapt_seen_signatures`` 适配。

    ``astream_events`` 不尊重 ``interrupt_on``（会直接执行工具），
    MUST 用 ``astream`` 才能在 tools 节点前暂停。
    额外开启 ``messages`` 模式以实时消费 ``AIMessageChunk``，把 ``<think>`` 块以
    ``reasoning_delta`` 事件增量推给前端。

    SSE 事件映射:
    - ``state.todos`` 变化 → ``todo_update``（原生 ``{content, status}`` schema，
      由 deepagents ``TodoListMiddleware`` 维护）
    - ``AIMessageChunk`` 中的 ``<think>`` 块 → ``reasoning_delta``（实时增量）
    - AIMessage with ``tool_calls`` → ``reasoning``（非 think 的计划文本）+ ``tool_call`` SSE
    - AIMessage without ``tool_calls`` → ``token``（最终回复）
    - ToolMessage → ``tool_result`` SSE

    在 ``interrupt_on`` 处暂停时，最后一个 state 的 messages[-1]
    是 AIMessage（含 tool_calls），此处 yield tool_call 后流结束，
    调用方 ``_is_interrupted`` 返回 True 进入审批流程。

    Args:
        agent: 已编译的 LangGraph agent。
        inputs: agent 输入，None 表示从 interrupt 处恢复。
        config: LangGraph 运行配置。
        source: SSE 事件 source 标识，默认 "work"（DeepAgent）。
            Supervisor 传 "work"，Expert 传 "coding" 等。
        seen_signatures: 兼容参数。若提供且 ``stream_run_state`` 为 None，
            经 ``adapt_seen_signatures`` 缓存绑定到 ``StreamRunState``，
            使 ``observation_sequence`` 和 ``last_todos`` 跨调用共享。
        stream_run_state: 跨 resume 共享状态。生产路径由
            ``run_agent_with_approval`` 创建一个实例并传入所有 stream 调用。
    """
    thread_id = config.get("configurable", {}).get("thread_id", "")
    abort_event = await get_abort_event(thread_id)
    run_id = current_trace_id() or ""

    state = adapt_seen_signatures(seen_signatures, stream_run_state)
    mapper = StreamEventMapper(
        source=source,
        thread_id=thread_id,
        state=state,
        run_id=run_id,
        sink_lookup=get_observation_sink,
    )

    logger.info(
        "stream_agent_events: start streaming",
        thread_id=thread_id,
        inputs_type=type(inputs).__name__,
        source=source,
        is_resume=inputs is None,
        seen_signatures_count=len(state.seen_message_keys),
    )

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
                async for event in mapper.process_custom(payload):
                    yield event
                continue
            if mode == "messages":
                async for event in mapper.process_messages_chunk(payload):
                    yield event
                continue
            # mode == "values"
            async for event in mapper.process_values_state(payload):
                yield event
        else:
            # 测试桩直接 yield state dict（非 tuple）
            async for event in mapper.process_values_state(chunk):
                yield event

    logger.info(
        "stream_agent_events: streaming completed",
        thread_id=thread_id,
        source=source,
        is_resume=inputs is None,
        first_state_seen=mapper._first_state_seen,
        processed_count=state.processed_message_count,
        seen_signatures_count=len(state.seen_message_keys),
    )
