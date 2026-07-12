"""项目级配置 API 端点单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.workspace import register_routes as register_project_config_routes


@pytest.fixture()
def app() -> FastAPI:
    """创建注册了 workspace 路由的 FastAPI 测试实例（合并自原 workspace.py + project_config.py）。"""
    app = FastAPI()
    register_project_config_routes(app)
    return app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def authorized_client(client: TestClient) -> TestClient:
    """mock 沙箱授权通过的 client。

    patch ``app.sandbox.get_sandbox`` 返回的 sandbox 的 ``check_read``
    不抛异常，模拟路径已授权。
    """
    with patch("app.sandbox.get_sandbox") as mock_get_sandbox:
        mock_sandbox = mock_get_sandbox.return_value
        # 默认 check_read / check_write 通过（不抛异常）
        mock_sandbox.check_read = AsyncMock(return_value=None)
        mock_sandbox.check_write = AsyncMock(return_value=None)
        yield client


_TEST_THREAD_ID = "test-thread-id"


class TestProjectConfigInit:
    """``POST /api/project-config/init`` 测试。"""

    def test_init_success(self, authorized_client: TestClient, tmp_path: Path) -> None:
        """已存在且已授权的目录应成功生成 .agentx/。"""
        response = authorized_client.post(
            "/api/project-config/init",
            json={"path": str(tmp_path), "thread_id": _TEST_THREAD_ID},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert ".agentx" in data["path"]
        assert len(data["created"]) > 0
        assert len(data["skipped"]) == 0

    def test_init_idempotent(
        self, authorized_client: TestClient, tmp_path: Path
    ) -> None:
        """第二次调用应全部 skipped。"""
        # 第一次
        authorized_client.post(
            "/api/project-config/init",
            json={"path": str(tmp_path), "thread_id": _TEST_THREAD_ID},
        )
        # 第二次
        response = authorized_client.post(
            "/api/project-config/init",
            json={"path": str(tmp_path), "thread_id": _TEST_THREAD_ID},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["created"]) == 0
        assert len(data["skipped"]) > 0

    def test_init_empty_path(self, authorized_client: TestClient) -> None:
        """空路径应返回 400。"""
        response = authorized_client.post(
            "/api/project-config/init",
            json={"path": "", "thread_id": _TEST_THREAD_ID},
        )

        assert response.status_code == 400

    def test_init_dot_path(self, authorized_client: TestClient) -> None:
        """``.`` 路径应返回 400（防止 resolve() 解析为 CWD）。"""
        response = authorized_client.post(
            "/api/project-config/init",
            json={"path": ".", "thread_id": _TEST_THREAD_ID},
        )

        assert response.status_code == 400

    def test_init_dotdot_path(self, authorized_client: TestClient) -> None:
        """``..`` 路径应返回 400。"""
        response = authorized_client.post(
            "/api/project-config/init",
            json={"path": "..", "thread_id": _TEST_THREAD_ID},
        )

        assert response.status_code == 400

    def test_init_whitespace_path(self, authorized_client: TestClient) -> None:
        """纯空白路径应返回 400。"""
        response = authorized_client.post(
            "/api/project-config/init",
            json={"path": "   ", "thread_id": _TEST_THREAD_ID},
        )

        assert response.status_code == 400

    def test_init_missing_thread_id(
        self, authorized_client: TestClient, tmp_path: Path
    ) -> None:
        """缺少 thread_id 字段应返回 422（Pydantic 校验失败）。"""
        response = authorized_client.post(
            "/api/project-config/init",
            json={"path": str(tmp_path)},
        )

        assert response.status_code == 422

    def test_init_nonexistent_path(
        self, authorized_client: TestClient, tmp_path: Path
    ) -> None:
        """不存在的路径应返回 400。"""
        response = authorized_client.post(
            "/api/project-config/init",
            json={"path": str(tmp_path / "nonexistent"), "thread_id": _TEST_THREAD_ID},
        )

        assert response.status_code == 400
        assert "不存在" in response.json()["detail"]

    def test_init_file_not_directory(
        self, authorized_client: TestClient, tmp_path: Path
    ) -> None:
        """文件而非目录应返回 400。"""
        file_path = tmp_path / "file.txt"
        file_path.write_text("hello")
        response = authorized_client.post(
            "/api/project-config/init",
            json={"path": str(file_path), "thread_id": _TEST_THREAD_ID},
        )

        assert response.status_code == 400

    def test_init_unauthorized_path(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """未授权路径应返回 400（沙箱校验失败）。"""
        with patch("app.sandbox.get_sandbox") as mock_get_sandbox:
            mock_sandbox = mock_get_sandbox.return_value
            mock_sandbox.check_read = AsyncMock(side_effect=Exception("PathNotAuthorized"))
            response = client.post(
                "/api/project-config/init",
                json={"path": str(tmp_path), "thread_id": _TEST_THREAD_ID},
            )

        assert response.status_code == 400
        assert "未授权" in response.json()["detail"]


class TestProjectConfigGet:
    """``GET /api/project-config`` 测试。"""

    def test_get_not_initialized(
        self, authorized_client: TestClient, tmp_path: Path
    ) -> None:
        """未生成 .agentx/ 时返回 exists=False。"""
        response = authorized_client.get(
            "/api/project-config",
            params={"path": str(tmp_path), "thread_id": _TEST_THREAD_ID},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is False
        assert data["files"] == []
        assert data["agents_md_preview"] is None

    def test_get_after_init(
        self, authorized_client: TestClient, tmp_path: Path
    ) -> None:
        """生成后返回 exists=True 和文件列表。"""
        # 先生成
        authorized_client.post(
            "/api/project-config/init",
            json={"path": str(tmp_path), "thread_id": _TEST_THREAD_ID},
        )

        response = authorized_client.get(
            "/api/project-config",
            params={"path": str(tmp_path), "thread_id": _TEST_THREAD_ID},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is True
        assert len(data["files"]) > 0
        # 应包含 mcp.json（TEMPLATES 中的文件）
        mcp_files = [f for f in data["files"] if f["name"] == "mcp.json"]
        assert len(mcp_files) == 1
        assert mcp_files[0]["exists"] is True
        # AGENTS.md 未自动生成，预览为 None
        assert data["agents_md_preview"] is None

    def test_get_nonexistent_path(
        self, authorized_client: TestClient, tmp_path: Path
    ) -> None:
        """不存在的路径应返回 400。"""
        response = authorized_client.get(
            "/api/project-config",
            params={"path": str(tmp_path / "nonexistent"), "thread_id": _TEST_THREAD_ID},
        )

        assert response.status_code == 400

    def test_get_unauthorized_path(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """未授权路径应返回 400（沙箱校验失败）。"""
        with patch("app.sandbox.get_sandbox") as mock_get_sandbox:
            mock_sandbox = mock_get_sandbox.return_value
            mock_sandbox.check_read = AsyncMock(side_effect=Exception("PathNotAuthorized"))
            response = client.get(
                "/api/project-config",
                params={"path": str(tmp_path), "thread_id": _TEST_THREAD_ID},
            )

        assert response.status_code == 400
        assert "未授权" in response.json()["detail"]
