"""通用 SSE 事件构造工具。

统一前后端 SSE 事件格式，自动处理 JSON 序列化。
"""

from __future__ import annotations

import json
from typing import Any


def make_sse_event(event: str, data: Any) -> dict[str, str]:
    """构造标准 SSE 事件 dict。

    - token: data 为纯字符串（前端直接拼接，不做 JSON.parse）
    - todo_update / approval_request / reasoning / tool_call / tool_result /
      delegation / team_plan / team_progress / team_result / team_done / error:
      data 为 JSON 字符串（dict 会被 json 序列化）
    - done: data 为 "{}"
    """
    if event in (
        "todo_update",
        "approval_request",
        "reasoning",
        "tool_call",
        "tool_result",
        "delegation",
        "team_plan",
        "team_progress",
        "team_result",
        "team_done",
        "error",
    ):
        if isinstance(data, str):
            return {"event": event, "data": data}
        return {"event": event, "data": json.dumps(data, ensure_ascii=False, default=str)}
    if event == "done":
        return {"event": "done", "data": "{}"}
    return {"event": event, "data": str(data)}


def make_todo_event(text: str, done: bool = False) -> dict[str, str]:
    """构造 todo_update SSE 事件。"""
    return make_sse_event(
        "todo_update",
        {"todos": [{"text": text, "done": done}]},
    )


def make_tool_call_event(
    tc_id: str, name: str, args: Any, source: str = "deep"
) -> dict[str, str]:
    """构造 tool_call SSE 事件。"""
    return make_sse_event(
        "tool_call",
        {
            "id": tc_id,
            "name": name,
            "args": args if args is not None else {},
            "source": source,
        },
    )


def make_tool_result_event(
    tc_id: str, name: str, result: Any, source: str = "deep"
) -> dict[str, str]:
    """构造 tool_result SSE 事件。"""
    return make_sse_event(
        "tool_result",
        {
            "id": tc_id,
            "name": name,
            "result": result,
            "source": source,
        },
    )


def make_team_event(event: str, data: Any) -> dict[str, str]:
    """构造 team 相关 SSE 事件（兼容原 _make_team_event）。

    与 make_sse_event 逻辑一致，但额外支持内部哨兵事件 _subtask_done。
    """
    if event in (
        "team_plan",
        "team_progress",
        "team_result",
        "team_done",
        "reasoning",
        "error",
        "_subtask_done",
    ):
        if isinstance(data, str):
            return {"event": event, "data": data}
        return {"event": event, "data": json.dumps(data, ensure_ascii=False, default=str)}
    if event == "done":
        return {"event": "done", "data": "{}"}
    return {"event": event, "data": str(data)}


def make_approval_event(data: dict) -> dict[str, str]:
    """构造 approval_request SSE 事件（仅做 JSON 封装）。

    redaction 和 preview 生成逻辑由调用方（deep/agent.py）处理，
    本函数只负责将 data dict 序列化为 SSE 事件格式。
    """
    return {
        "event": "approval_request",
        "data": json.dumps(data, ensure_ascii=False, default=str),
    }


__all__ = [
    "make_sse_event",
    "make_todo_event",
    "make_tool_call_event",
    "make_tool_result_event",
    "make_team_event",
    "make_approval_event",
]
