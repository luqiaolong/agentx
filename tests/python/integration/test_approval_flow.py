"""审批流集成测试：覆盖 /api/chat 产生 approval_request、/api/chat/approve 恢复、/api/chat/abort 中止。

- 使用 ``httpx.AsyncClient`` + ``ASGITransport`` 直连 app，不依赖真实 HTTP 服务。
- 通过 mock ``app.main.run_router`` 避免调用真实 LLM，产出可控事件序列。
- 包含完整并发流程：SSE 流触发审批 → 外部提交 approve → 流恢复 → /reset 清理。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app import main
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """直连 FastAPI app 的异步 HTTP 客户端。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _collect_sse_events(client: AsyncClient, url: str, json_body: dict) -> list[dict[str, str]]:
    """收集 SSE 事件并解析为 {event, data} 列表。"""
    events: list[dict[str, str]] = []
    async with client.stream("POST", url, json=json_body) as resp:
        assert resp.status_code == 200, resp.text
        buf = ""
        async for chunk in resp.aiter_text():
            buf += chunk.replace("\r\n", "\n")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                lines = [ln for ln in block.split("\n") if ln.strip()]
                if not lines:
                    continue
                event = ""
                data = ""
                for ln in lines:
                    if ln.startswith("event: "):
                        event = ln[7:].strip()
                    elif ln.startswith("data: "):
                        data = ln[6:]
                if event:
                    events.append({"event": event, "data": data})
    return events


@pytest.mark.integration
async def test_approval_request_event_shape(client: AsyncClient) -> None:
    """/api/chat 产生 approval_request 时，事件格式符合前端契约。"""
    thread_id = "approval-shape"
    payload = {
        "thread_id": thread_id,
        "tool_name": "write_file",
        "args": {"path": "/tmp/test.txt", "content": "<redacted>"},
        "preview": "将写入文件: /tmp/test.txt",
    }

    async def _fake(message: str, tid: str, checkpointer: object = None) -> AsyncIterator[dict[str, str]]:
        yield {"event": "approval_request", "data": json.dumps(payload, ensure_ascii=False)}
        yield {"event": "done", "data": "{}"}

    with patch("app.main.run_router", _fake):
        events = await _collect_sse_events(
            client, "/api/chat", {"message": "写文件", "thread_id": thread_id}
        )

    assert events[0]["event"] == "approval_request"
    parsed = json.loads(events[0]["data"])
    assert parsed["thread_id"] == thread_id
    assert parsed["tool_name"] == "write_file"
    assert "preview" in parsed
    assert "args" in parsed
    assert events[-1]["event"] == "done"


@pytest.mark.integration
async def test_approve_and_abort_endpoints(client: AsyncClient) -> None:
    """/api/chat/approve 与 /api/chat/abort 正确写入内存状态并返回 ok。"""
    thread_id = "approval-endpoints"
    main._pending_approvals.pop(thread_id, None)
    main._abort_flags.pop(thread_id, None)

    resp = await client.post(
        "/api/chat/approve", json={"thread_id": thread_id, "approval": True}
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert main._pending_approvals.get(thread_id) is True

    resp = await client.post(
        "/api/chat/approve", json={"thread_id": thread_id, "approval": False}
    )
    assert resp.status_code == 200
    assert main._pending_approvals.get(thread_id) is False

    resp = await client.post("/api/chat/abort", json={"thread_id": thread_id})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert main._abort_flags.get(thread_id) is True


@pytest.mark.integration
async def test_full_approval_flow_auto_resume(client: AsyncClient) -> None:
    """完整审批流：chat 触发 approval_request → 提交 approve → 流恢复 → /reset 清理。"""
    thread_id = "approval-full-flow"
    main._pending_approvals.pop(thread_id, None)
    main._abort_flags.pop(thread_id, None)

    approval_yielded = asyncio.Event()

    async def _fake(message: str, tid: str, checkpointer: object = None) -> AsyncIterator[dict[str, str]]:
        yield {
            "event": "todo_update",
            "data": json.dumps(
                {"todos": [{"text": "调用工具: write_file", "done": False}]},
                ensure_ascii=False,
            ),
        }
        yield {
            "event": "approval_request",
            "data": json.dumps(
                {
                    "thread_id": tid,
                    "tool_name": "write_file",
                    "args": {"path": "/tmp/test.txt", "content": "<redacted>"},
                    "preview": "将写入文件: /tmp/test.txt",
                },
                ensure_ascii=False,
            ),
        }
        approval_yielded.set()

        # 等待审批决定
        for _ in range(100):  # 最多 5 秒
            if main._pending_approvals.get(tid) is True:
                break
            await asyncio.sleep(0.05)
        else:
            yield {"event": "error", "data": "等待审批超时"}
            return

        yield {
            "event": "todo_update",
            "data": json.dumps(
                {"todos": [{"text": "工具 write_file 完成", "done": True}]},
                ensure_ascii=False,
            ),
        }
        yield {"event": "token", "data": "已完成写入"}
        yield {"event": "done", "data": "{}"}

    with patch("app.main.run_router", _fake):
        # 启动 SSE 流任务
        chat_task = asyncio.create_task(
            _collect_sse_events(
                client, "/api/chat", {"message": "写文件", "thread_id": thread_id}
            )
        )
        # 等待 approval_request 产生
        await asyncio.wait_for(approval_yielded.wait(), timeout=2.0)
        # 提交批准
        resp = await client.post(
            "/api/chat/approve", json={"thread_id": thread_id, "approval": True}
        )
        assert resp.status_code == 200
        # 等待流结束
        events = await asyncio.wait_for(chat_task, timeout=5.0)

    event_names = [e["event"] for e in events]
    assert event_names == [
        "todo_update",
        "approval_request",
        "todo_update",
        "token",
        "done",
    ], event_names
    assert json.loads(events[1]["data"])["tool_name"] == "write_file"

    # /reset 清理
    reset_events = await _collect_sse_events(
        client, "/api/chat", {"message": "/reset", "thread_id": thread_id}
    )
    assert any(
        e["event"] == "token" and "已清空" in e["data"] for e in reset_events
    )
    assert reset_events[-1]["event"] == "done"
