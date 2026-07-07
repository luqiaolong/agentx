"""Workspace API 单元测试：list / read 端点 + filesystem 函数。

覆盖：
1. ``GET /api/workspace/list`` 合法路径 → 200 + ``{entries: [...]}``
2. ``GET /api/workspace/list`` 非法路径（不在白名单）→ 400
3. ``GET /api/workspace/read`` 文本文件 → 200 + ``{content, size, encoding}``
4. ``GET /api/workspace/read`` 未授权路径 → 400；不存在 → 404
5. ``list_workspace`` 函数：真实临时目录 → 返回 type/size/mtime
6. ``list_workspace`` 函数：非白名单路径 → ValueError
7. ``read_workspace_file`` 函数：文本 / 二进制 / 目录 / 未授权
"""

from __future__ import annotations

import shutil
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, UPLOADS_DIR, WORKSPACE_DIR
from app.tools.filesystem import list_workspace, read_workspace_file
from app.utils.security import PathNotAuthorized


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

    async def _fake_list_workspace(path: str, thread_id: str | None = None) -> list[dict]:
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

    async def _fake_list_workspace(path: str, thread_id: str | None = None) -> list[dict]:
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

    async def _fake_list_workspace(path: str, thread_id: str | None = None) -> list[dict]:
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

    # 相对路径解析为 WORKSPACE_DIR/some_secret_dir，在白名单内但不存在 → FileNotFoundError
    with pytest.raises(FileNotFoundError):
        asyncio.run(list_workspace("some_secret_dir"))

    # 使用 ../ 逃逸 WORKSPACE_DIR，不在白名单内 → ValueError
    with pytest.raises(ValueError):
        asyncio.run(list_workspace("../../secrets"))


# ============================================================
# API 端点测试（read /api/workspace/read）
# ============================================================


def test_workspace_read_returns_text_content(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/workspace/read 文本文件 → 200 + content。"""
    captured: list[tuple[str, str | None]] = []

    async def _fake_read_workspace_file(
        path: str, thread_id: str | None = None
    ) -> dict[str, Any]:
        captured.append((path, thread_id))
        return {"content": "hello", "size": 5, "encoding": "utf-8"}

    monkeypatch.setattr("app.main.read_workspace_file", _fake_read_workspace_file)

    resp = client.get(
        "/api/workspace/read",
        params={"path": "data/workspace/foo.txt", "thread_id": "t1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content"] == "hello"
    assert body["size"] == 5
    assert body["encoding"] == "utf-8"
    assert captured == [("data/workspace/foo.txt", "t1")]


def test_workspace_read_returns_binary_flag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """二进制文件 → 200 + binary: True（不报错）。"""

    async def _fake_read_workspace_file(
        path: str, thread_id: str | None = None
    ) -> dict[str, Any]:
        return {"binary": True, "size": 1024}

    monkeypatch.setattr("app.main.read_workspace_file", _fake_read_workspace_file)

    resp = client.get("/api/workspace/read", params={"path": "data/workspace/foo.bin"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["binary"] is True
    assert body["size"] == 1024


def test_workspace_read_rejects_unauthorized(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未授权路径 → 400。"""

    async def _fake_read_workspace_file(
        path: str, thread_id: str | None = None
    ) -> dict[str, Any]:
        raise ValueError("路径不在白名单内且未授权: d:/secrets")

    monkeypatch.setattr("app.main.read_workspace_file", _fake_read_workspace_file)

    resp = client.get("/api/workspace/read", params={"path": "d:/secrets"})
    assert resp.status_code == 400, resp.text
    assert "白名单" in resp.json()["detail"]


def test_workspace_read_not_found(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文件不存在 → 404。"""

    async def _fake_read_workspace_file(
        path: str, thread_id: str | None = None
    ) -> dict[str, Any]:
        raise FileNotFoundError("文件不存在: missing.txt")

    monkeypatch.setattr("app.main.read_workspace_file", _fake_read_workspace_file)

    resp = client.get("/api/workspace/read", params={"path": "missing.txt"})
    assert resp.status_code == 404, resp.text
    assert "detail" in resp.json()


# ============================================================
# read_workspace_file 函数测试（真实临时目录）
# ============================================================


def test_read_workspace_file_text(workspace_temp_dir) -> None:
    """读取 WORKSPACE_DIR 内文本文件返回内容。"""
    import asyncio

    rel = f"data/workspace/{workspace_temp_dir.name}/file1.txt"
    result = asyncio.run(read_workspace_file(rel))
    assert result == {"content": "hello", "size": 5, "encoding": "utf-8"}


def test_read_workspace_file_rejects_directory(
    workspace_temp_dir,
) -> None:
    """路径是目录 → ValueError。"""
    import asyncio

    rel = f"data/workspace/{workspace_temp_dir.name}/subdir"
    with pytest.raises(ValueError, match="目录"):
        asyncio.run(read_workspace_file(rel))


def test_read_workspace_file_rejects_non_whitelisted() -> None:
    """非白名单绝对路径 → PathNotAuthorized。"""
    import asyncio

    with pytest.raises(PathNotAuthorized):
        asyncio.run(read_workspace_file(str(PROJECT_ROOT / "pyproject.toml")))


def test_read_workspace_file_binary(workspace_temp_dir) -> None:
    """二进制文件 → binary: True。"""
    import asyncio

    binary_file = workspace_temp_dir / "binary.bin"
    binary_file.write_bytes(bytes(range(256)))
    rel = f"data/workspace/{workspace_temp_dir.name}/binary.bin"
    result = asyncio.run(read_workspace_file(rel))
    assert result["binary"] is True
    assert result["size"] == 256
