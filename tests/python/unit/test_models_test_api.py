"""/api/models/test 单元测试：覆盖空字段校验、网络错误、HTTP 4xx/5xx、成功响应。

通过 monkeypatch httpx.AsyncClient 的 post 方法模拟不同场景，避免真实调用 LLM API。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient（不触发 lifespan）。"""
    from app.main import app

    return TestClient(app)


def _mock_response(status_code: int, json_body: dict[str, Any] | None = None, text: str = "") -> httpx.Response:
    """构造 httpx.Response 供 AsyncMock 返回。"""
    if json_body is not None:
        req = httpx.Request("POST", "https://example.com/v1/chat/completions")
        return httpx.Response(status_code, json=json_body, request=req)
    req = httpx.Request("POST", "https://example.com/v1/chat/completions")
    return httpx.Response(status_code, text=text, request=req)


# ============================================================
# 入参校验（不发起真实 HTTP 请求）
# ============================================================


def test_missing_base_url_returns_error(client: TestClient) -> None:
    """base_url 为空时直接返回 200 + ok=false，无需发请求。"""
    r = client.post(
        "/api/models/test",
        json={
            "provider_id": "deepseek",
            "model": "deepseek-chat",
            "base_url": "",
            "api_key": "sk-test",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "base_url" in body["message"]


def test_missing_model_returns_error(client: TestClient) -> None:
    """model 为空时直接返回错误。"""
    r = client.post(
        "/api/models/test",
        json={
            "provider_id": "deepseek",
            "model": "  ",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-test",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "模型名称" in body["message"]


def test_missing_api_key_returns_error(client: TestClient) -> None:
    """api_key 为空时直接返回错误。"""
    r = client.post(
        "/api/models/test",
        json={
            "provider_id": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "api_key": "",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "API Key" in body["message"]


# ============================================================
# 网络错误 / 超时
# ============================================================


def test_timeout_returns_error_with_message(client: TestClient) -> None:
    """超时返回 ok=false + message 含"超时"。"""
    mock_post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    with patch("app.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = mock_post
        mock_client_cls.return_value = mock_client

        r = client.post(
            "/api/models/test",
            json={
                "provider_id": "deepseek",
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com",
                "api_key": "sk-test",
            },
        )

    body = r.json()
    assert body["ok"] is False
    assert "超时" in body["message"]
    assert body["latency_ms"] >= 0


def test_request_error_returns_error(client: TestClient) -> None:
    """网络层异常（DNS / 连接拒绝）返回 ok=false + 含错误信息。"""
    mock_post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    with patch("app.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = mock_post
        mock_client_cls.return_value = mock_client

        r = client.post(
            "/api/models/test",
            json={
                "provider_id": "openai",
                "model": "gpt-4o-mini",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-test",
            },
        )

    body = r.json()
    assert body["ok"] is False
    assert "网络错误" in body["message"]


# ============================================================
# HTTP 4xx / 5xx 响应
# ============================================================


def test_401_unauthorized_returns_error(client: TestClient) -> None:
    """401 返回 ok=false + 错误信息含 OpenAI error.message。"""
    mock_post = AsyncMock(
        return_value=_mock_response(
            401,
            json_body={"error": {"message": "Incorrect API key provided", "type": "invalid_request_error"}},
        )
    )

    with patch("app.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = mock_post
        mock_client_cls.return_value = mock_client

        r = client.post(
            "/api/models/test",
            json={
                "provider_id": "deepseek",
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com",
                "api_key": "sk-wrong",
            },
        )

    body = r.json()
    assert body["ok"] is False
    assert body["status_code"] == 401
    assert "401" in body["message"]
    assert "Incorrect API key" in body["message"]


def test_500_server_error_returns_error(client: TestClient) -> None:
    """500 返回 ok=false，message 用 HTTP 兜底（response 无 JSON body 时）。"""
    mock_post = AsyncMock(return_value=_mock_response(500, text="Internal Server Error"))

    with patch("app.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = mock_post
        mock_client_cls.return_value = mock_client

        r = client.post(
            "/api/models/test",
            json={
                "provider_id": "deepseek",
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com",
                "api_key": "sk-test",
            },
        )

    body = r.json()
    assert body["ok"] is False
    assert body["status_code"] == 500
    assert "500" in body["message"]


# ============================================================
# 成功响应
# ============================================================


def test_success_response_extracts_content(client: TestClient) -> None:
    """200 响应且含 choices[0].message.content 时提取为 response_text。"""
    mock_post = AsyncMock(
        return_value=_mock_response(
            200,
            json_body={
                "id": "chatcmpl-test",
                "choices": [{"message": {"role": "assistant", "content": "Hi"}}],
                "usage": {"total_tokens": 5},
            },
        )
    )

    with patch("app.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = mock_post
        mock_client_cls.return_value = mock_client

        r = client.post(
            "/api/models/test",
            json={
                "provider_id": "deepseek",
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com",
                "api_key": "sk-test",
            },
        )

    body = r.json()
    assert body["ok"] is True
    assert body["status_code"] == 200
    assert body["message"] == "连接成功"
    assert body["response_text"] == "Hi"
    assert body["latency_ms"] >= 0


def test_success_response_without_choices_still_ok(client: TestClient) -> None:
    """200 但无 choices（如 max_tokens=1 返回空）也判定 ok=true，response_text=None。"""
    mock_post = AsyncMock(
        return_value=_mock_response(
            200,
            json_body={"id": "chatcmpl-empty", "choices": [], "usage": {}},
        )
    )

    with patch("app.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = mock_post
        mock_client_cls.return_value = mock_client

        r = client.post(
            "/api/models/test",
            json={
                "provider_id": "openai",
                "model": "gpt-4o-mini",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-test",
            },
        )

    body = r.json()
    assert body["ok"] is True
    assert body["response_text"] is None


# ============================================================
# Endpoint 拼接
# ============================================================


def test_endpoint_url_construction_for_glm_coding_plan(client: TestClient) -> None:
    """GLM Coding Plan base_url 含 /v4 后缀，正确拼接 chat/completions。"""
    captured: dict[str, str] = {}

    async def capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = url
        return _mock_response(200, json_body={"choices": []})

    with patch("app.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = capture_post
        mock_client_cls.return_value = mock_client

        r = client.post(
            "/api/models/test",
            json={
                "provider_id": "glm",
                "model": "glm-5",
                "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
                "api_key": "glm-test",
            },
        )

    assert r.json()["ok"] is True
    assert captured["url"] == "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"


def test_endpoint_url_strips_trailing_slash(client: TestClient) -> None:
    """base_url 末尾 / 不会造成双斜杠。"""
    captured: dict[str, str] = {}

    async def capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = url
        return _mock_response(200, json_body={"choices": []})

    with patch("app.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = capture_post
        mock_client_cls.return_value = mock_client

        client.post(
            "/api/models/test",
            json={
                "provider_id": "kimi",
                "model": "kimi-k2-7-code",
                "base_url": "https://api.kimi.com/coding/v1/",
                "api_key": "kimi-test",
            },
        )

    assert captured["url"] == "https://api.kimi.com/coding/v1/chat/completions"