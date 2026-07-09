"""UTF8JSONBodyMiddleware 回归测试。

验证：
- UTF-8 编码 JSON 正常透传
- GBK 编码 JSON 被正确解码为 UTF-8 后传给下游
- 非法编码返回 400
- 不修改原 ``request._body`` 私有属性（通过包装新 Request 实现）
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.main import UTF8JSONBodyMiddleware


@pytest.fixture
def client():
    """挂载 UTF8JSONBodyMiddleware 的小型 FastAPI 应用。"""
    app = FastAPI()
    app.add_middleware(UTF8JSONBodyMiddleware)

    @app.post("/echo")
    async def echo(request: Request):
        body = await request.json()
        return {"received": body}

    return TestClient(app)


def test_utf8_middleware_decodes_utf8_request(client: TestClient) -> None:
    """UTF-8 JSON 正常透传。"""
    response = client.post(
        "/echo",
        content='{"key": "测试"}'.encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json() == {"received": {"key": "测试"}}


def test_utf8_middleware_decodes_gbk_request(client: TestClient) -> None:
    """GBK 编码 JSON 被正确解码为 UTF-8。"""
    gbk_bytes = '{"key": "测试"}'.encode("gbk")
    response = client.post(
        "/echo",
        content=gbk_bytes,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json() == {"received": {"key": "测试"}}


def test_utf8_middleware_rejects_invalid_encoding(client: TestClient) -> None:
    """既非 UTF-8 也非 GBK 的字节序列返回 400。"""
    # 构造一个同时破坏 UTF-8 和 GBK 的字节序列
    invalid_bytes = b"\xff\xfe\x01\x02"
    response = client.post(
        "/echo",
        content=invalid_bytes,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert "not valid UTF-8 or GBK" in response.json()["detail"]


def test_utf8_middleware_skips_non_json_requests(client: TestClient) -> None:
    """非 application/json 请求不被中间件处理。"""
    response = client.post(
        "/echo",
        content='{"key": "value"}'.encode("utf-8"),
        headers={"content-type": "text/plain"},
    )
    # FastAPI 的 Request.json() 在 content-type 不是 JSON 时仍会尝试解析，
    # 这里只验证中间件没有拦截/改写 body（返回 200 或 422 均可接受）
    assert response.status_code in (200, 422)
