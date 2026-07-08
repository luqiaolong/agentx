"""沙箱授权 API 集成测试：FastAPI TestClient 直连 app（app 级，不依赖 myserver）。

- ``@pytest.mark.integration``
- 使用 ``fastapi.testclient.TestClient``（不进入 context manager，避免触发 lifespan
  关闭共享 embedding 客户端单例；沙箱端点不依赖 lifespan 状态）。
- 测试路径需避开用户主目录（``SessionSandbox`` 将 home 视为系统关键目录拒绝授权），
  因此从 ``PROJECT_ROOT`` 派生同盘符的非关键路径；``authorize`` 不要求路径真实存在。
- 流程：authorize → 200 → list 命中 → revoke → 200 → list 空。
- 系统关键目录 → 400。
"""

from __future__ import annotations

import sys
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT
from app.main import app
from app.sandbox import get_sandbox


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient（不触发 lifespan）。"""
    return TestClient(app)


def _non_critical_path(label: str) -> str:
    """构造一个不在用户主目录下的测试路径（authorize 不要求路径真实存在）。

    从 PROJECT_ROOT 父目录派生，避开 home / Windows / Program Files 等关键目录。
    """
    base = PROJECT_ROOT.parent / f"AGENTX_sandbox_test_{label}_{uuid.uuid4().hex[:8]}"
    return str(base)


@pytest.mark.integration
def test_authorize_list_revoke_roundtrip(client: TestClient) -> None:
    """授权 → 列表命中 → 撤销 → 列表为空。"""
    thread_id = "sandbox-api-roundtrip"
    target = _non_critical_path("roundtrip")
    try:
        # 1. authorize → 200，返回规范化路径与 writable
        resp = client.post(
            "/api/sandbox/authorize",
            json={"thread_id": thread_id, "path": target, "writable": False},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["authorized"] is True
        assert body["writable"] is False
        assert body["path"]  # 规范化后的路径非空

        # 2. list → 命中刚授权的目录
        resp = client.get(f"/api/sandbox/authorized/{thread_id}")
        assert resp.status_code == 200, resp.text
        dirs = resp.json()["dirs"]
        paths = [d["path"] for d in dirs]
        assert body["path"] in paths, f"列表未命中授权目录: {dirs}"

        # 3. revoke → 200
        resp = client.post(
            "/api/sandbox/revoke",
            json={"thread_id": thread_id, "path": target},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["revoked"] is True

        # 4. list → 空
        resp = client.get(f"/api/sandbox/authorized/{thread_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["dirs"] == [], f"撤销后列表非空: {resp.json()}"
    finally:
        _sandbox = get_sandbox()
        _sandbox._authorized_dirs.pop(thread_id, None)
        _sandbox._temp_authorized.pop(thread_id, None)


@pytest.mark.integration
def test_authorize_writable_flag(client: TestClient) -> None:
    """授权 writable=True 时列表反映可写标记。"""
    thread_id = "sandbox-api-writable"
    target = _non_critical_path("writable")
    try:
        resp = client.post(
            "/api/sandbox/authorize",
            json={"thread_id": thread_id, "path": target, "writable": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["writable"] is True

        resp = client.get(f"/api/sandbox/authorized/{thread_id}")
        dirs = resp.json()["dirs"]
        assert any(d["writable"] is True for d in dirs), f"未反映 writable 标记: {dirs}"
    finally:
        _sandbox = get_sandbox()
        _sandbox._authorized_dirs.pop(thread_id, None)
        _sandbox._temp_authorized.pop(thread_id, None)


@pytest.mark.integration
def test_authorize_system_critical_dir_returns_400(client: TestClient) -> None:
    """系统关键目录授权被拒 → 400。"""
    thread_id = "sandbox-api-critical"
    if sys.platform == "win32":
        critical = "C:/Windows/System32"
    else:
        critical = "/etc"
    try:
        resp = client.post(
            "/api/sandbox/authorize",
            json={"thread_id": thread_id, "path": critical, "writable": False},
        )
        assert resp.status_code == 400, f"系统关键目录应返回 400: {resp.status_code} {resp.text}"
        assert "关键目录" in resp.text, resp.text
    finally:
        _sandbox = get_sandbox()
        _sandbox._authorized_dirs.pop(thread_id, None)
        _sandbox._temp_authorized.pop(thread_id, None)


@pytest.mark.integration
def test_revoke_nonexistent_returns_false(client: TestClient) -> None:
    """撤销不存在的授权 → revoked=False。"""
    thread_id = "sandbox-api-revoke-none"
    target = _non_critical_path("revoke-none")
    try:
        resp = client.post(
            "/api/sandbox/revoke",
            json={"thread_id": thread_id, "path": target},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["revoked"] is False
    finally:
        _sandbox = get_sandbox()
        _sandbox._authorized_dirs.pop(thread_id, None)
        _sandbox._temp_authorized.pop(thread_id, None)
