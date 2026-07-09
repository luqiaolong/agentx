"""SSE 事件契约单测：验证 run_router 产出的事件格式与前端 preload 解析契约一致。

覆盖:
1. 路径 A (CHAT): mock LLM astream → 验证 token 事件 data 为纯字符串 + done 事件 data 为 "{}"
2. /reset: 验证 _event_generator 产出 token + done 事件
3. approval_request: 验证 _make_approval_event 包含 thread_id（前端 ApprovalDialog 据此调 approve）
4. todo_update: 验证 _make_todo_event 产出 {"todos": [...]} JSON（前端读 e.todos）

契约对齐（preload/index.ts 的 SSE 解析）:
- token: data 是纯字符串 → preload 放入 {data: payload} → ChatView 读 e.data
- todo_update: data 是 JSON 对象 → preload 展开到顶层 → ChatView 读 e.todos
- approval_request: data 是 JSON 对象含 thread_id/tool_name/args/preview → preload 转 ApprovalRequest
- done: data 是 "{}"
- error: data 是错误消息字符串
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator
from unittest.mock import patch

import pytest


# ---- Fake LLM ChatModel（模拟 astream 流式输出）----


class _FakeChunk:
    """模拟 LangChain chunk，content 为字符串。"""

    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChatModel:
    """模拟 ChatModel，astream 按预设 token 列表逐个 yield。"""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens

    async def astream(self, messages: Any, **kwargs: Any) -> AsyncIterator[_FakeChunk]:
        for tok in self._tokens:
            yield _FakeChunk(tok)


# ---- 1. 路径 A (CHAT): token + done 事件契约 ----


@pytest.mark.asyncio
async def test_run_router_work_mode_yields_token_string_and_done():
    """work 场景: token 事件 data 是纯字符串，done 事件 data 是 "{}"。

    前端 preload 解析: 纯字符串 payload → {type: "token", data: payload} →
    ChatView 读 e.data 正确。
    """
    from app.router import graph as graph_mod

    async def _fake_run_work_supervisor(*args: Any, **kwargs: Any) -> Any:
        yield {"event": "token", "data": "你好"}
        yield {"event": "token", "data": "！"}

    # graph.py: run_work_supervisor 在模块顶层 import（run_router 内调用）
    with patch.object(graph_mod, "run_work_supervisor", new=_fake_run_work_supervisor):
        events: list[dict[str, str]] = []
        async for evt in graph_mod.run_router("你好", "test-thread-a", agent_mode="work"):
            events.append(evt)

    # 最后必须是 done，data 为 "{}"
    assert events[-1] == {"event": "done", "data": "{}"}, f"最后一个事件应为 done: {events[-1]}"

    # token 事件 data 必须是纯字符串（不是 JSON）
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) >= 1, f"应至少 1 个 token 事件，实际: {len(token_events)}"
    combined = "".join(e["data"] for e in token_events)
    assert combined == "你好！", f"token 拼接内容应等于原始输入，实际: {combined}"
    for evt in token_events:
        assert isinstance(evt["data"], str)
        # 纯字符串不以 { 或 [ 开头（否则 preload 会误 JSON.parse）
        assert not evt["data"].startswith(("{", "["))


# ---- 2. approval_request 事件必须包含 thread_id ----


def test_make_approval_event_includes_thread_id():
    """approval_request 事件 data 必须包含 thread_id。

    断点背景: 前端 ApprovalDialog 调 window.api.approve.submit(threadId, approval)，
    后端 _pending_approvals 按 thread_id 索引。若 approval_request 事件缺 thread_id，
    前端 ApprovalRequest.threadId 为空字符串，approve 提交后后端无法匹配，
    DeepAgent _await_approval 永远收不到决定 → 危险操作链路彻底断开。
    """
    from app.security.approval_flow import _make_approval_event

    tool_call = {
        "name": "write_file",
        "args": {"path": "/data/workspace/test.txt", "content": "hello"},
    }

    event = _make_approval_event(tool_call, "thread-xyz-123")

    assert event["event"] == "approval_request"
    payload = json.loads(event["data"])

    # 关键断言: thread_id 必须存在且非空
    assert payload["thread_id"] == "thread-xyz-123", \
        f"approval_request 必须包含 thread_id，实际 payload: {payload}"

    # 其他字段
    assert payload["tool_name"] == "write_file"
    assert payload["preview"] == "将写入文件: /data/workspace/test.txt"
    # content 被 redacted
    assert payload["args"]["content"] == "<redacted>"
    assert payload["args"]["path"] == "/data/workspace/test.txt"


def test_make_approval_event_redacts_edit_file_content():
    """edit_file 的 old_text/new_text 应被 redacted。"""
    from app.security.approval_flow import _make_approval_event

    tool_call = {
        "name": "edit_file",
        "args": {"path": "/data/workspace/foo.py", "old_text": "secret", "new_text": "new"},
    }

    event = _make_approval_event(tool_call, "thread-edit")
    payload = json.loads(event["data"])

    assert payload["args"]["old_text"] == "<redacted>"
    assert payload["args"]["new_text"] == "<redacted>"
    assert payload["args"]["path"] == "/data/workspace/foo.py"
    assert payload["thread_id"] == "thread-edit"


# ---- 3. todo_update 事件契约: data 是 {"todos": [...]} JSON ----


def test_make_todo_event_produces_todos_json():
    """todo_update 事件 data 必须是 {"todos": [...]} JSON 对象。

    前端 preload 解析: JSON 对象 payload → 展开到 ChatEvent 顶层 →
    ChatView 读 e.todos（不是 e.data）。
    """
    from app.utils.sse_events import make_todo_event

    event = make_todo_event("调用工具: read_file", done=False)

    assert event["event"] == "todo_update"
    payload = json.loads(event["data"])

    # 必须是 {"todos": [...]} 结构
    assert "todos" in payload
    assert isinstance(payload["todos"], list)
    assert len(payload["todos"]) == 1

    todo = payload["todos"][0]
    assert todo["text"] == "调用工具: read_file"
    assert todo["done"] is False


def test_make_todo_event_done_true():
    """done=True 的 todo 事件。"""
    from app.utils.sse_events import make_todo_event

    event = make_todo_event("工具 read_file 完成", done=True)
    payload = json.loads(event["data"])

    assert payload["todos"][0]["done"] is True
    assert payload["todos"][0]["text"] == "工具 read_file 完成"


# ---- 4. _sse 辅助函数: token 用 str()，todo/approval 用 json.dumps ----


def test_sse_token_uses_plain_string():
    """token 事件 data 必须是纯字符串（str()），不是 JSON。

    前端 preload: 纯字符串不以 { 或 [ 开头 → 不 JSON.parse → payload 保持字符串 →
    走 {data: payload} 分支 → ChatView 读 e.data。
    """
    from app.utils.sse_events import make_sse_event

    event = make_sse_event("token", "hello world")
    assert event == {"event": "token", "data": "hello world"}

    # 确保不是 JSON 包装
    assert not event["data"].startswith('"')


def test_sse_done_data_is_empty_json():
    """done 事件 data 必须是 "{}"。"""
    from app.utils.sse_events import make_sse_event

    event = make_sse_event("done", "{}")
    assert event == {"event": "done", "data": "{}"}


def test_sse_error_is_structured_json():
    """error 事件 data 是结构化 JSON，至少包含 message。"""
    from app.utils.sse_events import make_error_event

    event = make_error_event("LLM 不可用")
    assert event["event"] == "error"
    payload = json.loads(event["data"])
    assert payload["message"] == "LLM 不可用"


def test_sse_error_with_code():
    """error 事件可携带 code 字段。"""
    from app.utils.sse_events import make_error_event

    event = make_error_event("something went wrong", code="E123")
    assert event["event"] == "error"
    payload = json.loads(event["data"])
    assert payload["message"] == "something went wrong"
    assert payload["code"] == "E123"


def test_sse_todo_update_serializes_dict():
    """todo_update 事件接收 dict 时用 json.dumps 序列化。"""
    from app.utils.sse_events import make_sse_event

    data = {"todos": [{"text": "test", "done": True}]}
    event = make_sse_event("todo_update", data)

    assert event["event"] == "todo_update"
    # data 是 JSON 字符串
    parsed = json.loads(event["data"])
    assert parsed == data


# 注: 第 5 节（subagent 事件转换契约）和第 6 节（路径 B subagent 选择）
# 已删除——场景化架构（Supervisor + Expert）下，convert_subagent_event /
# select_subagent 随 app/subagents/dispatch.py 一并移除。Supervisor/Expert
# 直接使用 LangGraph create_react_agent / build_deep_agent 的事件流，不再
# 经过单独的 subagent 事件转换层。
