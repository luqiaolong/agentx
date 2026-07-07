"""POST /api/chat 的 system_prompt 字段集成测试。

不调真实 LLM / TEI / Milvus，全部 mock。验证：
1. ChatRequest 带 system_prompt 字段被路径 A 接收
2. system_prompt 非空时覆盖 default_system_prompt
3. system_prompt 为 null 时回退 default_system_prompt
4. 老客户端不传 system_prompt 字段，run_router 收到 scene_prompt=None（向后兼容）
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    return TestClient(app)


def _make_fake_run_router(captured: dict):
    async def fake_run_router(
        message: str,
        thread_id: str,
        checkpointer: object | None = None,
        permission_mode: str = "standard",
        scene_prompt: str | None = None,
        agent_mode: str = "agent",
        workspace_path: str | None = None,
    ) -> AsyncIterator[dict[str, str]]:
        captured["scene_prompt"] = scene_prompt
        captured["message"] = message
        captured["thread_id"] = thread_id
        captured["agent_mode"] = agent_mode
        captured["workspace_path"] = workspace_path
        captured["permission_mode"] = permission_mode
        yield {"event": "done", "data": "{}"}

    return fake_run_router


def test_chat_request_accepts_system_prompt_field(client: TestClient, monkeypatch) -> None:
    """ChatRequest 接受 system_prompt 字段，且非空时覆盖 default。

    通过 mock run_router 捕获 scene_prompt 入参，断言其等于请求中的 system_prompt。
    """
    captured: dict = {}
    monkeypatch.setattr("app.main.run_router", _make_fake_run_router(captured))

    resp = client.post(
        "/api/chat",
        json={
            "message": "hello",
            "thread_id": "test-thread-1",
            "system_prompt": "你是编程助手。",
        },
    )

    assert resp.status_code == 200
    assert captured["scene_prompt"] == "你是编程助手。"


def test_chat_request_system_prompt_null_falls_back(client: TestClient, monkeypatch) -> None:
    """system_prompt 为 null 时 run_router 收到 scene_prompt=None。"""
    captured: dict = {}
    monkeypatch.setattr("app.main.run_router", _make_fake_run_router(captured))

    client.post(
        "/api/chat",
        json={"message": "hi", "thread_id": "t2", "system_prompt": None},
    )

    assert captured["scene_prompt"] is None


def test_chat_request_without_system_prompt_field(client: TestClient, monkeypatch) -> None:
    """老客户端不传 system_prompt 字段，run_router 收到 scene_prompt=None（向后兼容）。"""
    captured: dict = {}
    monkeypatch.setattr("app.main.run_router", _make_fake_run_router(captured))

    client.post(
        "/api/chat",
        json={"message": "hi", "thread_id": "t3"},
    )

    assert captured["scene_prompt"] is None


def test_chat_request_passes_workspace_path(client: TestClient, monkeypatch) -> None:
    """workspace_path 作为独立字段透传给 run_router，不再从消息正文解析。"""
    captured: dict = {}
    monkeypatch.setattr("app.main.run_router", _make_fake_run_router(captured))

    resp = client.post(
        "/api/chat",
        json={
            "message": "hi",
            "thread_id": "t4",
            "workspace_path": "d:/projects/foo",
        },
    )

    assert resp.status_code == 200
    assert captured["workspace_path"] == "d:/projects/foo"


def test_chat_request_no_workspace_tag_parsing(client: TestClient, monkeypatch) -> None:
    """消息正文中的 <workspace> 标签不再被解析，原样透传。"""
    captured: dict = {}
    monkeypatch.setattr("app.main.run_router", _make_fake_run_router(captured))

    client.post(
        "/api/chat",
        json={
            "message": "<workspace>d:/projects/bar</workspace> hello",
            "thread_id": "t5",
        },
    )

    assert captured["workspace_path"] is None
    assert captured["message"] == "<workspace>d:/projects/bar</workspace> hello"
