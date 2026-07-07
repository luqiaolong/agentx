"""DeepAgent SSE 流式事件驱动。

从 ``app.deep.agent`` 拆出（Phase 2.3），保持公共 API 不变。

职责:
- ``_stream_agent_events``：驱动 ``agent.astream(stream_mode="values")``，
  尊重 ``interrupt_before``，把 LangGraph state 转换为前端 SSE 事件。

SSE 事件映射:
- ``AIMessage`` with ``tool_calls`` → ``tool_call`` + ``reasoning`` + ``todo_update``
- ``AIMessage`` without ``tool_calls`` → ``token``（最终回复）
- ``ToolMessage`` → ``tool_result`` + ``todo_update``（标记完成）

导入方向：``agent.py`` → ``streaming.py``（单向，无循环）。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from uuid import uuid4

from app.approval import get_abort_event
from app.utils.sse_events import (
    make_sse_event,
    make_todo_event,
    make_tool_call_event,
    make_tool_result_event,
)

__all__ = ["_stream_agent_events"]


def _extract_plan_or_update(text: str) -> tuple[str, Any] | None:
    """从 LLM 输出中提取结构化计划或计划更新。

    支持纯 JSON 或 markdown 代码块包裹的 JSON。

    Returns:
        ("plan", data_dict) 或 ("plan_update", update_dict) 或 None。
    """
    import json
    import re

    text = text.strip()
    candidates = [text]
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        candidates.append(match.group(1).strip())
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            if isinstance(data.get("plan"), list):
                return "plan", data
            if isinstance(data.get("plan_update"), dict):
                return "plan_update", data["plan_update"]
    return None


async def _stream_agent_events(
    agent: Any, inputs: Any, config: dict
) -> AsyncIterator[dict[str, str]]:
    """驱动 ``agent.astream(stream_mode="values")``，尊重 ``interrupt_before``。

    ``astream_events`` 不尊重 ``interrupt_before``（会直接执行工具），
    MUST 用 ``astream`` + ``stream_mode="values"`` 才能在 tools 节点前暂停。

    SSE 事件映射（spec D1 + T5 扩展）:
    - AIMessage with tool_calls → ``tool_call`` SSE（含 id/name/args/source="deep"）
      + ``todo_update``（任务级进度，与 tool_call 事件并存，语义不同）
    - AIMessage without tool_calls → ``token``（最终回复，strip_think 后一次性 yield）
      或 ``plan`` / ``plan_update``（结构化任务计划/更新）
    - ToolMessage → ``tool_result`` SSE（含 id/name/result/source="deep"）
      + ``todo_update``（标记完成）

    在 ``interrupt_before=["tools"]`` 处暂停时，最后一个 state 的 messages[-1]
    是 AIMessage（含 tool_calls），此处 yield tool_call + todo_update 后流结束，
    调用方 ``_is_interrupted`` 返回 True 进入审批流程。
    """
    from langchain_core.messages import AIMessage, ToolMessage
    from app.utils.text import strip_think

    thread_id = config.get("configurable", {}).get("thread_id", "")
    abort_event = get_abort_event(thread_id)

    async for state in agent.astream(inputs, config=config, stream_mode="values"):
        if abort_event.is_set():
            raise asyncio.CancelledError("aborted")
        messages = state.get("messages", []) if hasattr(state, "get") else []
        if not messages:
            continue
        last_msg = messages[-1]

        if isinstance(last_msg, ToolMessage):
            # 工具执行完成 → tool_result SSE + todo_update（任务级进度）
            tool_name = getattr(last_msg, "name", "") or ""
            tool_call_id = getattr(last_msg, "tool_call_id", "") or str(uuid4())
            content = getattr(last_msg, "content", "")
            if isinstance(content, list):
                content = "".join(
                    block if isinstance(block, str)
                    else block.get("text", "") if isinstance(block, dict)
                    else ""
                    for block in content
                )
            yield make_tool_result_event(tool_call_id, tool_name, content, source="deep")
            yield make_todo_event(f"工具 {tool_name} 完成", done=True, task_id=thread_id)

        elif isinstance(last_msg, AIMessage):
            if getattr(last_msg, "tool_calls", None):
                # AIMessage with tool_calls → 先展示思考计划，再 yield tool_call
                # LLM 的 content 通常包含 💧... 计划 ...</think> 或纯文本计划
                content = last_msg.content
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
                    yield make_sse_event(
                        "reasoning",
                        {"content": display_plan, "source": "deep"},
                    )
                # 再 yield 每个 tool_call
                for tc in last_msg.tool_calls:
                    if isinstance(tc, dict):
                        tc_name = tc.get("name", tc.get("tool", "unknown"))
                        tc_args = tc.get("args", {}) or {}
                        tc_id = tc.get("id") or str(uuid4())
                    else:
                        tc_name = getattr(tc, "name", "unknown")
                        tc_args = getattr(tc, "args", {}) or {}
                        tc_id = getattr(tc, "id", None) or str(uuid4())
                    yield make_tool_call_event(tc_id, tc_name, tc_args, source="deep")
                    yield make_todo_event(f"调用工具: {tc_name}", done=False, task_id=thread_id)
            elif getattr(last_msg, "content", ""):
                # AIMessage without tool_calls → 最终回复
                content = last_msg.content
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
                    # 检测结构化任务计划/更新
                    plan_info = _extract_plan_or_update(text)
                    if plan_info is not None:
                        kind, plan_data = plan_info
                        yield make_sse_event(kind, plan_data)
                    else:
                        yield make_sse_event("token", text)
