"""Workspace 文件列表 API 单元测试：GET /api/workspace/list + list_workspace 函数。

覆盖：
1. ``GET /api/workspace/list`` 合法路径 → 200 + ``{entries: [...]}``
2. ``GET /api/workspace/list`` 非法路径（不在白名单）→ 400
3. ``list_workspace`` 函数：真实临时目录 → 返回 type/size/mtime
4. ``list_workspace`` 函数：非白名单路径 → ValueError
"""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, UPLOADS_DIR, WORKSPACE_DIR
from app.tools.filesystem import list_workspace


# ============================================================
# fixtures
# ============================================================


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient（不触发 lifespan）。"""
    from app.main import app

    return TestClient(app)


# ============================================================
# API 端点测试（mock list_workspace）
# ============================================================


def test_workspace_list_returns_entries(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/workspace/list 合法路径 → 200 + entries 结构。"""
    fake_entries = [
        {"name": "out.txt", "type": "file", "size": 1024, "mtime": 1700000000.0},
        {"name": "subdir", "type": "dir", "size": 0, "mtime": 1700000001.0},
    ]

    async def _fake_list_workspace(path: str) -> list[dict]:
        return fake_entries

    monkeypatch.setattr("app.main.list_workspace", _fake_list_workspace)

    resp = client.get("/api/workspace/list", params={"path": "data/workspace"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "entries" in body
    assert len(body["entries"]) == 2
    assert body["entries"][0]["name"] == "out.txt"
    assert body["entries"][0]["type"] == "file"
    assert body["entries"][1]["type"] == "dir"


def test_workspace_list_default_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """path 缺省时默认 data/workspace。"""
    captured_path: list[str] = []

    async def _fake_list_workspace(path: str) -> list[dict]:
        captured_path.append(path)
        return []

    monkeypatch.setattr("app.main.list_workspace", _fake_list_workspace)

    resp = client.get("/api/workspace/list")
    assert resp.status_code == 200, resp.text
    assert captured_path == ["data/workspace"]


def test_workspace_list_rejects_non_whitelisted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非白名单路径 → 400。"""

    async def _fake_list_workspace(path: str) -> list[dict]:
        raise ValueError(f"路径不在白名单内: {path}")

    monkeypatch.setattr("app.main.list_workspace", _fake_list_workspace)

    resp = client.get("/api/workspace/list", params={"path": "d:/secrets"})
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "detail" in body
    assert "白名单" in body["detail"]


# ============================================================
# list_workspace 函数测试（真实临时目录）
# ============================================================


@pytest.fixture
def workspace_temp_dir():
    """在 WORKSPACE_DIR 下创建临时目录与文件，测试后清理。"""
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    test_dir = WORKSPACE_DIR / "_test_workspace_api_tmp"
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir()
    (test_dir / "file1.txt").write_text("hello", encoding="utf-8")
    (test_dir / "subdir").mkdir()
    yield test_dir
    if test_dir.exists():
        shutil.rmtree(test_dir)


def test_list_workspace_returns_entries(workspace_temp_dir) -> None:
    """list_workspace 返回 name/type/size/mtime 字段。"""
    import asyncio

    rel = f"data/workspace/{workspace_temp_dir.name}"
    entries = asyncio.run(list_workspace(rel))

    names = [e["name"] for e in entries]
    assert "file1.txt" in names
    assert "subdir" in names

    file_entry = next(e for e in entries if e["name"] == "file1.txt")
    assert file_entry["type"] == "file"
    assert file_entry["size"] == 5  # "hello"
    assert isinstance(file_entry["mtime"], float)

    dir_entry = next(e for e in entries if e["name"] == "subdir")
    assert dir_entry["type"] == "dir"


def test_list_workspace_empty_or_dot_returns_workspace_root(
    workspace_temp_dir,
) -> None:
    """空或 '.' → WORKSPACE_DIR 根。"""
    import asyncio

    # 在 WORKSPACE_DIR 根放一个标记文件确保非空
    marker = WORKSPACE_DIR / "_test_marker.txt"
    marker.write_text("marker", encoding="utf-8")
    try:
        entries_dot = asyncio.run(list_workspace("."))
        names_dot = [e["name"] for e in entries_dot]
        assert "_test_marker.txt" in names_dot
    finally:
        marker.unlink(missing_ok=True)


def test_list_workspace_rejects_non_whitelisted() -> None:
    """非白名单路径 → ValueError。"""
    import asyncio

    # 用 PROJECT_ROOT 本身（不在白名单）
    with pytest.raises(ValueError):
        asyncio.run(list_workspace(str(PROJECT_ROOT)))


def test_list_workspace_rejects_arbitrary_relative() -> None:
    """不在白名单的相对路径 → ValueError。"""
    import asyncio

    # 相对路径解析为 PROJECT_ROOT/some_secret_dir，不在白名单内
    with pytest.raises(ValueError):
        asyncio.run(list_workspace("some_secret_dir"))
