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
from unittest.mock import AsyncMock, MagicMock, patch

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
async def test_run_router_chat_path_yields_token_string_and_done():
    """路径 A: token 事件 data 是纯字符串，done 事件 data 是 "{}"。

    前端 preload 解析: 纯字符串 payload → {type: "token", data: payload} →
    ChatView 读 e.data 正确。
    """
    from app.router import graph as graph_mod

    fake_model = _FakeChatModel(["你好", "！"])

    # graph.py: classify_message 在模块顶层 import，_run_chat_path 函数内 import get_chat_model
    with patch.object(graph_mod, "classify_message", new=AsyncMock(return_value="CHAT")), \
         patch("app.llm.get_chat_model", return_value=fake_model):
        events: list[dict[str, str]] = []
        async for evt in graph_mod.run_router("你好", "test-thread-a"):
            events.append(evt)

    # 最后必须是 done，data 为 "{}"
    assert events[-1] == {"event": "done", "data": "{}"}, f"最后一个事件应为 done: {events[-1]}"

    # token 事件 data 必须是纯字符串（不是 JSON）
    # 注意: ThinkFilter 跨 chunk 缓冲最多 6 字符以剥离 <think> 块，
    # 2 个短 chunk 可能被合并为 1 个 token 事件。契约层面只需验证:
    # (1) 至少 1 个 token 事件 (2) 每个 data 是纯字符串 (3) 拼接内容正确
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
    from app.paths.deep_path import _make_approval_event

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
    from app.paths.deep_path import _make_approval_event

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
    from app.paths.deep_path import _make_todo_event

    event = _make_todo_event("调用工具: read_file", done=False)

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
    from app.paths.deep_path import _make_todo_event

    event = _make_todo_event("工具 read_file 完成", done=True)
    payload = json.loads(event["data"])

    assert payload["todos"][0]["done"] is True
    assert payload["todos"][0]["text"] == "工具 read_file 完成"


# ---- 4. _sse 辅助函数: token 用 str()，todo/approval 用 json.dumps ----


def test_sse_token_uses_plain_string():
    """token 事件 data 必须是纯字符串（str()），不是 JSON。

    前端 preload: 纯字符串不以 { 或 [ 开头 → 不 JSON.parse → payload 保持字符串 →
    走 {data: payload} 分支 → ChatView 读 e.data。
    """
    from app.router.graph import _sse

    event = _sse("token", "hello world")
    assert event == {"event": "token", "data": "hello world"}

    # 确保不是 JSON 包装
    assert not event["data"].startswith('"')


def test_sse_done_data_is_empty_json():
    """done 事件 data 必须是 "{}"。"""
    from app.router.graph import _sse

    event = _sse("done", "{}")
    assert event == {"event": "done", "data": "{}"}


def test_sse_error_uses_plain_string():
    """error 事件 data 是纯字符串错误消息。"""
    from app.router.graph import _sse

    event = _sse("error", "LLM 不可用")
    assert event == {"event": "error", "data": "LLM 不可用"}


def test_sse_todo_update_serializes_dict():
    """todo_update 事件接收 dict 时用 json.dumps 序列化。"""
    from app.router.graph import _sse

    data = {"todos": [{"text": "test", "done": True}]}
    event = _sse("todo_update", data)

    assert event["event"] == "todo_update"
    # data 是 JSON 字符串
    parsed = json.loads(event["data"])
    assert parsed == data


# ---- 5. subagent 事件转换契约 ----
#
# chat-rendering-trace-v2 D1/D2 后契约:
# - token → 经 ThinkFilter 分离为 reasoning SSE + token SSE（无 filter 时直接透传 token）
# - tool_call → 透传为 tool_call SSE（不再 flatten 为 todo_update），含 id/name/args/source
# - tool_result → 透传为 tool_result SSE（不再 flatten 为 todo_update），含 id/name/result/source
# - 未知类型 → 返回空 list（不再返回 None）
# 返回类型从 ``dict | None`` 改为 ``list[dict]``，因为一次 token 事件可能产出多个 SSE。


def test_convert_subagent_event_token():
    """子代理 token 事件（无 think_filter）→ 透传为单个 SSE token 事件，data 为纯字符串。"""
    from app.router.graph import _convert_subagent_event

    subagent_event = {"type": "token", "content": "hello"}
    sse_list = _convert_subagent_event(subagent_event)

    assert isinstance(sse_list, list)
    assert len(sse_list) == 1
    sse = sse_list[0]
    assert sse["event"] == "token"
    assert sse["data"] == "hello"


def test_convert_subagent_event_tool_call():
    """子代理 tool_call 事件 → 透传为 SSE tool_call 事件（含 id/name/args/source）。

    契约变更: D1 后不再 flatten 为 todo_update，前端 ToolCallCard 直接消费 tool_call SSE。
    """
    from app.router.graph import _convert_subagent_event

    subagent_event = {"type": "tool_call", "name": "read_file", "args": {"path": "/foo"}}
    sse_list = _convert_subagent_event(subagent_event, source="code")

    assert isinstance(sse_list, list)
    assert len(sse_list) == 1
    sse = sse_list[0]
    assert sse["event"] == "tool_call"
    payload = json.loads(sse["data"])
    assert payload["name"] == "read_file"
    assert payload["args"] == {"path": "/foo"}
    assert payload["source"] == "code"
    # id 必存在且非空（前端按 id 配对 tool_result）
    assert "id" in payload and payload["id"]


def test_convert_subagent_event_tool_result():
    """子代理 tool_result 事件 → 透传为 SSE tool_result 事件（含 id/name/result/source）。

    契约变更: D1 后不再 flatten 为 todo_update，前端 ToolCallCard 按 id 配对后渲染完成态。
    """
    from app.router.graph import _convert_subagent_event

    subagent_event = {"type": "tool_result", "name": "read_file", "result": "..."}
    sse_list = _convert_subagent_event(subagent_event, source="code")

    assert isinstance(sse_list, list)
    assert len(sse_list) == 1
    sse = sse_list[0]
    assert sse["event"] == "tool_result"
    payload = json.loads(sse["data"])
    assert payload["name"] == "read_file"
    assert payload["result"] == "..."
    assert payload["source"] == "code"
    assert "id" in payload and payload["id"]


def test_convert_subagent_event_unknown_type_returns_empty_list():
    """未知子代理事件类型 → 返回空 list（被过滤，不产生任何 SSE）。"""
    from app.router.graph import _convert_subagent_event

    assert _convert_subagent_event({"type": "unknown"}) == []
    assert _convert_subagent_event({}) == []


def test_convert_subagent_event_token_with_think_filter_splits_reasoning():
    """带 think_filter（retain_think=True）的 token 事件 → reasoning + token SSE 列表。

    覆盖 D2: ThinkFilter 把 ``<think>...</think>`` 内容剥离为 reasoning SSE，
    visible text 走 token SSE。返回 list 才能一次产出两个事件。

    注意: ThinkFilter 默认 max_hold=6（保留末尾 6 字符防跨 chunk 切到 ``<think>``
    标签前缀），所以 visible text 末尾 6 字符会被 hold 不 emit。这里用足够长的
    visible text 让前缀部分被 emit 为 token SSE。
    """
    from app.router.graph import _convert_subagent_event
    from app.utils.text import ThinkFilter

    tf = ThinkFilter(retain_think=True)
    # 完整 <think> 块在一次 feed 内闭合，visible text 长度 > 6 让前缀被 emit
    sse_list = _convert_subagent_event(
        {"type": "token", "content": "<think>内部推理</think>可见回答123456789"},
        think_filter=tf,
        source="code",
    )

    assert isinstance(sse_list, list)
    # 至少包含 reasoning 和 token 两类事件
    events = [s["event"] for s in sse_list]
    assert "reasoning" in events, f"应产出 reasoning SSE: {sse_list}"
    assert "token" in events, f"应产出 token SSE: {sse_list}"

    reasoning_evt = next(s for s in sse_list if s["event"] == "reasoning")
    reasoning_payload = json.loads(reasoning_evt["data"])
    assert "内部推理" in reasoning_payload["content"]
    assert reasoning_payload["source"] == "code"

    token_evt = next(s for s in sse_list if s["event"] == "token")
    # visible text 前缀被 emit（末尾 6 字符被 max_hold 缓冲）
    assert token_evt["data"].startswith("可见回答"), f"token 应是 visible text 前缀: {token_evt['data']}"


def test_convert_subagent_event_tool_call_result_id_pairing():
    """同一轮 tool_call + tool_result 的 id 必须一致（前端按 id 配对）。

    回归保护: subagent 用 astream_events v2 的 run_id 作为 id（chat-rendering-trace-v2 D5）。
    若 _convert_subagent_event 为 tool_call 和 tool_result 各自生成不同 uuid4，
    前端 AssistantUIThread.buildRenderItems 按 id 配对会 100% 失败，导致每个工具
    调用产生两张卡片（永久 running + 孤儿 complete）。
    """
    from app.router.graph import _convert_subagent_event

    # 模拟 subagent 实际产出：同一 tool run 的 start/end 共享 run_id
    shared_run_id = "run-abc-123"
    tool_call_event = {
        "type": "tool_call",
        "id": shared_run_id,
        "name": "read_file",
        "args": {"path": "/foo"},
        "source": "code",
    }
    tool_result_event = {
        "type": "tool_result",
        "id": shared_run_id,
        "name": "read_file",
        "result": "file content",
        "source": "code",
    }

    call_sse_list = _convert_subagent_event(tool_call_event, source="code")
    result_sse_list = _convert_subagent_event(tool_result_event, source="code")

    assert len(call_sse_list) == 1
    assert len(result_sse_list) == 1

    call_payload = json.loads(call_sse_list[0]["data"])
    result_payload = json.loads(result_sse_list[0]["data"])

    # 关键断言: 两个事件的 id 必须相等，前端才能配对
    assert call_payload["id"] == result_payload["id"], (
        f"tool_call id ({call_payload['id']}) 必须等于 tool_result id "
        f"({result_payload['id']}), 否则前端配对失败"
    )
    assert call_payload["id"] == shared_run_id


def test_convert_subagent_event_event_source_overrides_caller_source():
    """event 自带 source 字段时优先于 caller 传入的 source（T3 子代理已补 source）。

    覆盖 _convert_subagent_event 中 ``ev_source = event.get("source") or source`` 优先级。
    """
    from app.router.graph import _convert_subagent_event

    # event 自带 source="rag"，caller 传 source="code"
    sse_list = _convert_subagent_event(
        {
            "type": "tool_call",
            "id": "r1",
            "name": "rag_retrieve",
            "args": {"query": "foo"},
            "source": "rag",
        },
        source="code",  # 应被 event.source 覆盖
    )

    assert len(sse_list) == 1
    payload = json.loads(sse_list[0]["data"])
    assert payload["source"] == "rag", "event 自带 source 应优先于 caller source"


def test_convert_subagent_event_fallback_uuid_when_no_id():
    """subagent 未传 id 时 _convert_subagent_event 兜底生成 uuid（不崩）。"""
    from app.router.graph import _convert_subagent_event

    # 不传 id（旧 subagent 行为），应 fallback 到 uuid4
    sse_list = _convert_subagent_event(
        {"type": "tool_call", "name": "read_file", "args": {"path": "/foo"}},
        source="code",
    )

    assert len(sse_list) == 1
    payload = json.loads(sse_list[0]["data"])
    assert "id" in payload and payload["id"], "无 id 时应 fallback 到 uuid4"


# ---- 6. 路径 B subagent 选择 ----


def test_select_subagent_web_keywords():
    """含 web 关键词 → web 子代理。"""
    from app.router.graph import _select_subagent

    assert _select_subagent("帮我搜索网页信息") == "web"
    assert _select_subagent("联网查一下") == "web"
    assert _select_subagent("search the web") == "web"


def test_select_subagent_rag_keywords():
    """含 RAG 关键词 → rag 子代理。"""
    from app.router.graph import _select_subagent

    assert _select_subagent("从知识库检索文档") == "rag"
    assert _select_subagent("查文档库") == "rag"


def test_select_subagent_defaults_to_code():
    """无关键词命中 → code 子代理（默认）。"""
    from app.router.graph import _select_subagent

    assert _select_subagent("读取这个文件") == "code"
    assert _select_subagent("hello") == "code"
