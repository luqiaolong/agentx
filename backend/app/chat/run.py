"""路径 A：LLM 直答 + ThinkFilter 流式。

从 ``app.router.graph`` 抽取的 ``run_chat_path``（原 ``_run_chat_path``），
行为与原实现完全一致，仅替换 SSE 构造与 prompt 工具为公共模块。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from app.approval import get_abort_event
from app.config import get_settings
from app.llm import get_chat_model
from app.observability.logger import logger
from app.utils.prompts import resolve_system_prompt
from app.utils.sse_events import make_sse_event
from app.utils.text import ThinkFilter, extract_chunk_text

__all__ = ["run_chat_path"]


async def run_chat_path(
    message: str,
    thread_id: str,
    system_prompt_extra: str | None = None,
    history: list | None = None,
    scene_prompt: str | None = None,
) -> AsyncIterator[dict[str, str]]:
    """路径 A：LLM 直答 + 流式 token。

    Args:
        message: 用户消息（已移除 @skill 标记）。
        thread_id: 会话 ID。
        system_prompt_extra: 可选的 skill content，拼到默认 system prompt 前。
        history: 历史 messages 列表（含 SystemMessage / HumanMessage / AIMessage），
            已截断到 ``context_max_messages`` / ``context_max_tokens`` 内。
        scene_prompt: 可选场景 prompt，非空时覆盖 default_system_prompt。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    system_prompt = resolve_system_prompt(
        default=get_settings().default_system_prompt,
        scene_prompt=scene_prompt,
        skill_extra=system_prompt_extra,
    )
    try:
        llm = get_chat_model(temperature=0.7, streaming=True)
    except ValueError as exc:
        yield make_sse_event("error", f"LLM 不可用: {exc}")
        return

    # 构建完整 messages：system + history + current
    # history 已是 BaseMessage 列表（含早期 SystemMessage 会被 trim 移除，
    # 但此处保险起见再过滤一次，避免多个 system prompt）
    history_msgs = [m for m in (history or []) if not isinstance(m, SystemMessage)]
    messages = [
        SystemMessage(content=system_prompt),
        *history_msgs,
        HumanMessage(content=message),
    ]
    think_filter = ThinkFilter(
        max_hold=get_settings().think_filter_max_hold,
        retain_think=True,
    )
    abort_event = get_abort_event(thread_id)
    # 累积已完成 token 流（剥离 think 后），用于最后做 plan JSON 检测。
    # 若 LLM 输出 {"plan": [...]} 形式的任务列表，仍让用户看到流式 token
    # （避免响应卡顿），并在流结束 flush 后再 yield plan 事件供前端结构化展示。
    accumulated_text: list[str] = []
    try:
        async for chunk in llm.astream(messages):
            if abort_event.is_set():
                raise asyncio.CancelledError("aborted")
            raw = extract_chunk_text(chunk, strip=False)
            cleaned = think_filter.feed(raw)
            # retain_think 模式：提取 reasoning chunk 并 yield reasoning 事件
            if getattr(think_filter, "_retain_think", False):
                reasoning = think_filter.take_think()
                if reasoning:
                    yield make_sse_event("reasoning", {"content": reasoning, "source": "assistant"})
            if cleaned:
                accumulated_text.append(cleaned)
                yield make_sse_event("token", cleaned)
        tail = think_filter.flush()
        if tail:
            accumulated_text.append(tail)
            yield make_sse_event("token", tail)
    except asyncio.CancelledError:
        logger.info("chat path aborted", thread_id=thread_id)
        raise
    except Exception as exc:  # noqa: BLE001 — SSE 兜底
        logger.warning("chat path LLM stream failed", error=str(exc))
        yield make_sse_event("error", f"LLM 流式失败: {exc}")
        return

    # 流结束：检测累积文本是否是结构化任务计划，命中则额外 yield plan 事件
    full_text = "".join(accumulated_text).strip()
    if full_text:
        try:
            from app.utils.plan_extraction import extract_plan_or_update

            plan_info = extract_plan_or_update(full_text)
            if plan_info is not None:
                kind, plan_data = plan_info
                yield make_sse_event(kind, plan_data)
        except Exception as exc:  # noqa: BLE001 — 提取失败容错
            logger.debug("plan extraction in chat path failed", error=str(exc))
