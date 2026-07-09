"""UTF8JSONBodyMiddleware 回归测试。

覆盖：
1. 正常 UTF-8 JSON 请求透传。
2. GBK 编码 JSON 请求被正确转码为 UTF-8。
3. 非 JSON 请求（如 form）不触发解码。
4. 非法编码请求返回 400。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.main import UTF8JSONBodyMiddleware


@pytest.fixture
def client():
    """挂载 UTF8JSONBodyMiddleware 的 FastAPI 测试客户端。"""
    app = FastAPI()
    app.add_middleware(UTF8JSONBodyMiddleware)

    @app.post("/echo")
    async def echo(request: Request):
        body = await request.json()
        return JSONResponse({"received": body})

    @app.post("/form")
    async def form(request: Request):
        body = await request.body()
        return JSONResponse({"raw_len": len(body), "content_type": request.headers.get("content-type")})

    return TestClient(app)


def test_utf8_json_passes_through(client: TestClient) -> None:
    """UTF-8 编码 JSON 正常透传。"""
    payload = {"name": "测试"}
    response = client.post("/echo", json=payload)
    assert response.status_code == 200
    assert response.json()["received"] == payload


def test_gbk_json_decoded_to_utf8(client: TestClient) -> None:
    """GBK 编码 JSON 被正确解码为 UTF-8 后再交给 Pydantic。"""
    payload = {"name": "测试"}
    # 将 JSON 字符串编码为 GBK 字节
    gbk_bytes = json.dumps(payload, ensure_ascii=False).encode("gbk")

    response = client.post(
        "/echo",
        content=gbk_bytes,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["received"] == payload


def test_non_json_request_left_untouched(client: TestClient) -> None:
    """非 application/json 请求不触发编码探测。"""
    body = "name=测试".encode("gbk")
    response = client.post(
        "/form",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["raw_len"] == len(body)
    assert data["content_type"] == "application/x-www-form-urlencoded"


def test_invalid_encoding_returns_400(client: TestClient) -> None:
    """既不是 UTF-8 也不是 GBK 的请求返回 400。"""
    # 构造无法被 UTF-8 或 GBK 解码的字节序列
    invalid_bytes = b"\xff\xfe\x00\x01"

    response = client.post(
        "/echo",
        content=invalid_bytes,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert "not valid UTF-8 or GBK" in response.json()["detail"]


def test_empty_body_passes_through(client: TestClient) -> None:
    """空 JSON body 透传（endpoint 收到空字节）。"""
    response = client.post("/form", content=b"", headers={"Content-Type": "application/json"})
    assert response.status_code == 200
    data = response.json()
    assert data["raw_len"] == 0
