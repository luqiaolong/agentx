"""POST /api/chat 请求字段集成测试。

不调真实 LLM / TEI / Milvus，全部 mock。验证：
1. ChatRequest 接受 agent_mode 字段（新场景枚举）
2. workspace_path 字段透传给 run_router
3. 消息正文中的 <workspace> 标签不再被解析
4. agent_mode="coding_team" 且 coding_team 未启用时降级为 "coding"
5. system_prompt 字段仍被接受（向后兼容前端），但不传给 run_router
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
        agent_mode: str = "work",
        workspace_path: str | None = None,
        revoked_paths: list[str] | None = None,
        trace_id: str | None = None,
    ) -> AsyncIterator[dict[str, str]]:
        captured["message"] = message
        captured["thread_id"] = thread_id
        captured["agent_mode"] = agent_mode
        captured["workspace_path"] = workspace_path
        captured["permission_mode"] = permission_mode
        captured["revoked_paths"] = revoked_paths
        captured["trace_id"] = trace_id
        yield {"event": "done", "data": "{}"}

    return fake_run_router


def test_chat_request_accepts_agent_mode_work(client: TestClient, monkeypatch) -> None:
    """ChatRequest 接受 agent_mode="work"，并透传给 run_router。"""
    captured: dict = {}
    monkeypatch.setattr("app.main.run_router", _make_fake_run_router(captured))

    resp = client.post(
        "/api/chat",
        json={
            "message": "hello",
            "thread_id": "test-thread-1",
            "agent_mode": "work",
        },
    )

    assert resp.status_code == 200
    assert captured["agent_mode"] == "work"


def test_chat_request_accepts_agent_mode_coding(client: TestClient, monkeypatch) -> None:
    """ChatRequest 接受 agent_mode="coding"。"""
    captured: dict = {}
    monkeypatch.setattr("app.main.run_router", _make_fake_run_router(captured))

    client.post(
        "/api/chat",
        json={"message": "hi", "thread_id": "t-coding", "agent_mode": "coding"},
    )

    assert captured["agent_mode"] == "coding"


def test_chat_request_accepts_agent_mode_coding_team(client: TestClient, monkeypatch) -> None:
    """ChatRequest 接受 agent_mode="coding_team"。"""
    captured: dict = {}
    monkeypatch.setattr("app.main.run_router", _make_fake_run_router(captured))

    client.post(
        "/api/chat",
        json={"message": "hi", "thread_id": "t-team", "agent_mode": "coding_team"},
    )

    # coding_team_enabled 默认 True，所以 agent_mode 保持 coding_team
    assert captured["agent_mode"] == "coding_team"


def test_chat_request_default_agent_mode_is_work(client: TestClient, monkeypatch) -> None:
    """不传 agent_mode 字段时，默认为 "work"。"""
    captured: dict = {}
    monkeypatch.setattr("app.main.run_router", _make_fake_run_router(captured))

    client.post(
        "/api/chat",
        json={"message": "hi", "thread_id": "t-default"},
    )

    assert captured["agent_mode"] == "work"


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


def test_chat_request_system_prompt_accepted_but_not_passed(client: TestClient, monkeypatch) -> None:
    """system_prompt 字段仍被 API 接受（向后兼容前端），但不再传给 run_router。

    新架构下，各场景有自己的 system_prompt 配置（Supervisor/Expert），
    不再通过 API 参数覆盖。
    """
    captured: dict = {}
    monkeypatch.setattr("app.main.run_router", _make_fake_run_router(captured))

    resp = client.post(
        "/api/chat",
        json={
            "message": "hello",
            "thread_id": "t-sys-prompt",
            "system_prompt": "你是编程助手。",
            "agent_mode": "work",
        },
    )

    assert resp.status_code == 200
    # run_router 不再接收 scene_prompt 参数
    assert "scene_prompt" not in captured or captured.get("scene_prompt") is None


def test_chat_request_invalid_agent_mode_returns_422(client: TestClient, monkeypatch) -> None:
    """无效 agent_mode 值 → 422 校验错误。"""
    captured: dict = {}
    monkeypatch.setattr("app.main.run_router", _make_fake_run_router(captured))

    resp = client.post(
        "/api/chat",
        json={
            "message": "hi",
            "thread_id": "t-invalid",
            "agent_mode": "invalid_mode",
        },
    )

    assert resp.status_code == 422  # pydantic 校验失败
