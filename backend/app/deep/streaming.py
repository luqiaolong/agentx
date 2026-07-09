"""DeepAgent SSE 流式事件驱动。

从 ``app.deep.agent`` 拆出（Phase 2.3），保持公共 API 不变。

职责:
- ``_stream_agent_events``：驱动 ``agent.astream_events(version="v2")``，
  尊重 ``interrupt_before``，把 LangGraph 事件转换为前端 SSE 事件。

SSE 事件映射:
- ``on_chat_model_stream`` → 实时 ``reasoning`` / ``token`` 流（逐 token）
- ``on_chat_model_end`` → ``tool_call`` + ``reasoning``（AIMessage 完成时）
- ``on_chain_end`` → 检测 state 中的 ToolMessage 产出 ``tool_result``

导入方向：``agent.py`` → ``streaming.py``（单向，无循环）。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from uuid import uuid4

from loguru import logger

from app.security.approval import get_abort_event
from app.utils.sse_events import (
    make_sse_event,
    make_todo_event,
    make_tool_call_event,
    make_tool_result_event,
)

__all__ = ["_stream_agent_events"]


def _extract_plan_or_update(text: str) -> tuple[str, Any] | None:
    """从 LLM 输出中提取结构化计划或计划更新。

    委托给共享工具 ``app.utils.plan_extraction.extract_plan_or_update``，
    行为详见该函数 docstring。
    """
    from app.utils.plan_extraction import extract_plan_or_update as _shared

    return _shared(text)


def _extract_chunk_text(chunk: Any) -> str:
    """从 AIMessageChunk 中提取文本内容。"""
    if chunk is None:
        return ""
    content = getattr(chunk, "content", chunk)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


async def _stream_agent_events(
    agent: Any, inputs: Any, config: dict, source: str = "deep"
) -> AsyncIterator[dict[str, str]]:
    """驱动 ``agent.astream_events(version="v2")``，尊重 ``interrupt_before``。

    使用 ``astream_events`` 替代 ``astream(stream_mode="values")``，实现：
    - LLM 生成阶段逐 token 流式输出 reasoning/token（不再等完整 AIMessage）
    - 工具调用通过 ``on_chat_model_end`` 产出
    - 工具结果通过 ``on_chain_end`` 检测 state 中的 ToolMessage 产出
    - ``interrupt_before`` 仍有效（LangGraph 1.2+ 已支持）

    SSE 事件映射:
    - on_chat_model_stream → 逐 chunk 提取文本，通过 ThinkFilter 实时分离
      think 块内容 → ``reasoning`` 事件；非 think 内容 → ``token`` 事件
    - on_chat_model_end → AIMessage 完成时 yield ``tool_call`` + ``reasoning``
    - on_chain_end → 检测 state 中的 ToolMessage 产出 ``tool_result`` + ``todo_update``

    在 ``interrupt_before=["tools"]`` 处暂停时，流自然结束，
    调用方 ``_is_interrupted`` 返回 True 进入审批流程。

    Args:
        agent: 已编译的 LangGraph agent。
        inputs: agent 输入，None 表示从 interrupt 处恢复。
        config: LangGraph 运行配置。
        source: SSE 事件 source 标识，默认 "deep"（DeepAgent）。
            Supervisor 传 "work"，Expert 传 "coding" 等。
    """
    from langchain_core.messages import ToolMessage
    from app.utils.text import ThinkFilter, split_think, strip_think, strip_tool_call_xml

    thread_id = config.get("configurable", {}).get("thread_id", "")
    abort_event = await get_abort_event(thread_id)

    logger.info(
        "stream_agent_events: start streaming (astream_events v2)",
        thread_id=thread_id,
        inputs_type=type(inputs).__name__,
        source=source,
    )

    # ThinkFilter 用于跨 chunk 跟踪 think 块，实时分离 reasoning 和 visible
    think_filter = ThinkFilter(retain_think=True)
    # 标记当前 AIMessage 是否包含 tool_calls
    _current_msg_has_tool_calls = False
    # 标记当前是否已 emit 过 tool_call
    _tool_calls_emitted = False
    # 已观测到的 ToolMessage 集合（用于检测新工具结果）
    _seen_tool_ids: set[str] = set()

    astream_kwargs: dict[str, Any] = {"version": "v2"}
    if config is not None:
        astream_kwargs["config"] = config

    async for event in agent.astream_events(inputs, **astream_kwargs):
        if abort_event.is_set():
            raise asyncio.CancelledError("aborted")

        kind = event.get("event", "")
        name = event.get("name", "")
        data = event.get("data", {}) or {}
        run_id = event.get("run_id", "") or str(uuid4())

        if kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            chunk_text = _extract_chunk_text(chunk)
            if not chunk_text:
                continue

            # 检测 chunk 是否携带 tool_calls 信号
            chunk_obj = data.get("chunk")
            if chunk_obj is not None:
                tc = getattr(chunk_obj, "tool_calls", None)
                if tc:
                    _current_msg_has_tool_calls = True

            # 用 ThinkFilter 实时处理 think 块
            cleaned = think_filter.feed(chunk_text)
            think_chunk = think_filter.take_think()

            # 实时 yield think 块内容作为 reasoning
            if think_chunk:
                think_chunk = strip_tool_call_xml(think_chunk)
                if think_chunk.strip():
                    yield make_sse_event(
                        "reasoning",
                        {"content": think_chunk, "source": source},
                    )

            # 非 think 内容：如果当前消息最终会包含 tool_calls，
            # 则不输出 visible 内容（避免计划文本泄露到最终回复）
            if cleaned and not _current_msg_has_tool_calls:
                cleaned = strip_tool_call_xml(cleaned)
                if cleaned.strip():
                    yield make_sse_event("token", cleaned)

        elif kind == "on_chat_model_end":
            output = data.get("output")
            if output is None:
                continue

            from langchain_core.messages import AIMessage

            if not isinstance(output, AIMessage):
                continue

            tc_list = getattr(output, "tool_calls", None) or []
            logger.info(
                "stream_agent_events: on_chat_model_end tc_count={tc_count} content_preview={preview}",
                tc_count=len(tc_list),
                preview=str(output.content)[:100] if output.content else "",
                source=source,
            )
            content = output.content
            if isinstance(content, list):
                content = "".join(
                    block if isinstance(block, str)
                    else block.get("text", "") if isinstance(block, dict)
                    else ""
                    for block in content
                )
            content_str = str(content) if content else ""

            if tc_list and not _tool_calls_emitted:
                # AIMessage with tool_calls → flush 剩余 think + yield tool_call
                _tool_calls_emitted = True

                # flush 剩余 think 缓冲
                tail = think_filter.flush()
                if tail:
                    tail = strip_tool_call_xml(tail)
                    if tail.strip():
                        yield make_sse_event(
                            "reasoning",
                            {"content": tail, "source": source},
                        )

                # 兜底：如果 ThinkFilter 没有产出过 reasoning，从完整内容提取
                plan_text = strip_tool_call_xml(content_str)
                reasoning, visible = split_think(plan_text)
                display_plan = reasoning.strip() if reasoning.strip() else visible.strip()
                if display_plan:
                    yield make_sse_event(
                        "reasoning",
                        {"content": display_plan, "source": source},
                    )

                # yield 每个 tool_call
                for tc in tc_list:
                    if isinstance(tc, dict):
                        tc_name = tc.get("name", tc.get("tool", "unknown"))
                        tc_args = tc.get("args", {}) or {}
                        tc_id = tc.get("id") or str(uuid4())
                    else:
                        tc_name = getattr(tc, "name", "unknown")
                        tc_args = getattr(tc, "args", {}) or {}
                        tc_id = getattr(tc, "id", None) or str(uuid4())
                    yield make_tool_call_event(tc_id, tc_name, tc_args, source=source)
                    yield make_todo_event(f"调用工具: {tc_name}", done=False, task_id=thread_id)

            elif content_str and not tc_list:
                # AIMessage without tool_calls → 最终回复
                tail = think_filter.flush()
                if tail:
                    tail = strip_tool_call_xml(tail)
                    if tail.strip():
                        yield make_sse_event(
                            "reasoning",
                            {"content": tail, "source": source},
                        )

                text = strip_think(content_str)
                text = strip_tool_call_xml(text)
                if text:
                    plan_info = _extract_plan_or_update(text)
                    if plan_info is not None:
                        kind_ev, plan_data = plan_info
                        yield make_sse_event(kind_ev, plan_data)
                    else:
                        yield make_sse_event("token", text)

            # 重置状态，准备下一条消息
            _current_msg_has_tool_calls = False
            _tool_calls_emitted = False
            think_filter = ThinkFilter(retain_think=True)

        elif kind == "on_chain_end":
            # 检测 state 中是否有新的 ToolMessage（工具执行结果）
            # 在 interrupt_before 场景下，工具执行后不会产出 on_tool_end 事件，
            # 但 on_chain_end 的 data.output 或 data.chunk 中可能包含 ToolMessage
            output = data.get("output")
            if output is None:
                output = data.get("chunk")
            logger.info(
                "stream_agent_events: on_chain_end name={name} output_type={output_type} has_output={has_output}",
                name=name,
                output_type=type(output).__name__ if output else "None",
                has_output=output is not None,
                source=source,
            )
            if output is not None:
                messages = []
                if hasattr(output, "get"):
                    messages = output.get("messages", []) or []
                elif hasattr(output, "messages"):
                    messages = output.messages
                
                logger.info(
                    "stream_agent_events: on_chain_end messages_count={count}",
                    count=len(messages),
                    source=source,
                )
                
                for msg in messages:
                    logger.info(
                        "stream_agent_events: on_chain_end msg_type={msg_type} msg_name={msg_name}",
                        msg_type=type(msg).__name__,
                        msg_name=getattr(msg, "name", "N/A"),
                        source=source,
                    )
                    if isinstance(msg, ToolMessage):
                        tc_id = getattr(msg, "tool_call_id", "") or str(uuid4())
                        # 去重：只处理未见过的新 ToolMessage
                        if tc_id not in _seen_tool_ids:
                            _seen_tool_ids.add(tc_id)
                            tool_name = getattr(msg, "name", "") or "unknown"
                            content = getattr(msg, "content", "")
                            if isinstance(content, list):
                                content = "".join(
                                    block if isinstance(block, str)
                                    else block.get("text", "") if isinstance(block, dict)
                                    else ""
                                    for block in content
                                )
                            result = str(content) if content is not None else ""
                            yield make_tool_result_event(tc_id, tool_name, result, source=source)
                            yield make_todo_event(f"工具 {tool_name} 完成", done=True, task_id=thread_id)
                            logger.info(
                                "stream_agent_events: yielded tool_result",
                                thread_id=thread_id,
                                tool_call_id=tc_id,
                                tool_name=tool_name,
                                source=source,
                            )

    # 流结束，flush 任何剩余缓冲
    if not think_filter._in_think:
        tail = think_filter.flush()
        if tail:
            tail = strip_tool_call_xml(tail)
            if tail.strip():
                yield make_sse_event("token", tail)
    else:
        think_filter.flush()

    logger.info(
        "stream_agent_events: streaming finished",
        thread_id=thread_id,
        source=source,
    )
