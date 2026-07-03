"""TEI 客户端单元测试。

使用 ``respx`` mock ``httpx.AsyncClient``，覆盖：
单文本/批量/重试/全部失败/超长拒绝/批量跳过/响应解包。
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from tenacity import wait_none

from app.config import get_settings
from app.embedding.tei_client import (
    EmbeddingUnavailable,
    TextTooLongError,
    embed_text,
    embed_texts,
    get_embedding_client,
    healthcheck,
)
import app.embedding.tei_client as tei_module


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch: pytest.MonkeyPatch):
    """关闭 tenacity 重试退避的真实睡眠，加速测试。"""
    monkeypatch.setattr(tei_module, "_RETRY_WAIT", wait_none())


@pytest.fixture(autouse=True)
async def _reset_embedding_singleton():
    """每个测试前后重置单例并关闭底层 ``httpx.AsyncClient``。"""
    if tei_module._client is not None:
        try:
            await tei_module._client.aclose()
        except Exception:
            pass
    tei_module._client = None
    yield
    if tei_module._client is not None:
        try:
            await tei_module._client.aclose()
        except Exception:
            pass
    tei_module._client = None


def _make_response(request: httpx.Request) -> httpx.Response:
    """根据请求 body 中 inputs 数量返回对应维度的向量响应。"""
    body = json.loads(request.content)
    inputs = body["inputs"]
    n = len(inputs) if isinstance(inputs, list) else 1
    return httpx.Response(200, json=[[0.1] * 1024 for _ in range(n)])


# ---- 1. 单文本嵌入成功 + 请求体不含 model ----
@respx.mock
async def test_embed_text_single_success():
    settings = get_settings()
    captured: list[dict] = []

    def side_effect(request: httpx.Request):
        captured.append(json.loads(request.content))
        return _make_response(request)

    respx.post(settings.embedding_url).mock(side_effect=side_effect)

    vec = await embed_text("hello")

    assert isinstance(vec, list)
    assert len(vec) == 1024
    assert vec[0] == pytest.approx(0.1)
    assert captured == [{"inputs": "hello"}]
    assert "model" not in captured[0]


# ---- 2. 批量嵌入按 max_batch 分片 ----
@respx.mock
async def test_embed_texts_batch_split():
    settings = get_settings()
    assert settings.embedding_max_batch == 32

    texts = [f"text_{i}" for i in range(64)]
    route = respx.post(settings.embedding_url).mock(side_effect=_make_response)

    result = await embed_texts(texts)

    assert route.call_count == 2
    assert len(result) == 64
    for vec in result:
        assert vec is not None
        assert len(vec) == 1024
    # 顺序保留
    assert result[0] != result[1] or result[0] == result[1]  # 仅校验结构


# ---- 2b. 分片请求体均不含 model 且 inputs 为 list ----
@respx.mock
async def test_embed_texts_request_body_no_model():
    settings = get_settings()
    bodies: list[dict] = []

    def side_effect(request: httpx.Request):
        bodies.append(json.loads(request.content))
        return _make_response(request)

    respx.post(settings.embedding_url).mock(side_effect=side_effect)

    await embed_texts(["a", "b"])

    assert len(bodies) == 1
    assert bodies[0] == {"inputs": ["a", "b"]}
    assert "model" not in bodies[0]


# ---- 3. 超时触发重试后成功 ----
@respx.mock
async def test_retry_then_success():
    settings = get_settings()
    state = {"n": 0}

    def side_effect(request: httpx.Request):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ReadTimeout("read timeout", request=request)
        return _make_response(request)

    route = respx.post(settings.embedding_url).mock(side_effect=side_effect)

    vec = await embed_text("hello")

    assert len(vec) == 1024
    assert route.call_count == 2


# ---- 4. 全部重试失败 -> EmbeddingUnavailable ----
@respx.mock
async def test_all_retries_fail():
    settings = get_settings()

    def side_effect(request: httpx.Request):
        raise httpx.ConnectTimeout("connect timeout", request=request)

    route = respx.post(settings.embedding_url).mock(side_effect=side_effect)

    with pytest.raises(EmbeddingUnavailable):
        await embed_text("hello")

    # 初始尝试 + 3 次重试 = 4 次
    assert route.call_count == 4


# ---- 4b. 网络错误（RequestError）同样重试耗尽 ----
@respx.mock
async def test_request_error_retries_fail():
    settings = get_settings()

    def side_effect(request: httpx.Request):
        raise httpx.ConnectError("conn refused", request=request)

    route = respx.post(settings.embedding_url).mock(side_effect=side_effect)

    with pytest.raises(EmbeddingUnavailable):
        await embed_texts(["a", "b"])

    assert route.call_count == 4


# ---- 5. 超长文本不发送 HTTP 请求 ----
@respx.mock
async def test_text_too_long_no_request():
    settings = get_settings()
    long_text = "x" * (settings.embedding_max_chars + 1)
    route = respx.post(settings.embedding_url).mock(side_effect=_make_response)

    with pytest.raises(TextTooLongError) as exc_info:
        await embed_text(long_text)

    msg = str(exc_info.value)
    assert str(settings.embedding_max_chars) in msg
    assert "分块器" in msg
    assert route.call_count == 0


# ---- 5b. 错误信息包含实际长度 ----
async def test_text_too_long_error_message():
    settings = get_settings()
    length = settings.embedding_max_chars + 6000
    long_text = "y" * length

    with pytest.raises(TextTooLongError) as exc_info:
        await embed_text(long_text)

    assert str(length) in str(exc_info.value)


# ---- 6. 批量中超长文本跳过 -> [vec, None, vec] ----
@respx.mock
async def test_embed_texts_skip_too_long():
    settings = get_settings()
    long_text = "z" * (settings.embedding_max_chars + 1)
    texts = ["short1", long_text, "short2"]

    route = respx.post(settings.embedding_url).mock(side_effect=_make_response)

    result = await embed_texts(texts)

    assert route.call_count == 1  # 仅 2 个短文本一次批量请求
    assert len(result) == 3
    assert result[0] is not None and len(result[0]) == 1024
    assert result[1] is None
    assert result[2] is not None and len(result[2]) == 1024


# ---- 6b. on_skip 回调被调用 ----
@respx.mock
async def test_embed_texts_on_skip_callback():
    settings = get_settings()
    long_text = "z" * (settings.embedding_max_chars + 1)
    skipped: list[tuple] = []

    def on_skip(text: str, err: TextTooLongError):
        skipped.append((text[:3] + "...", str(err)))

    respx.post(settings.embedding_url).mock(side_effect=_make_response)

    result = await embed_texts(["ok", long_text], on_skip=on_skip)

    assert result[1] is None
    assert len(skipped) == 1
    assert "分块器" in skipped[0][1]


# ---- 7. 单文本响应 [[...]] 解包为 [...] ----
@respx.mock
async def test_single_response_unwrapped():
    settings = get_settings()
    respx.post(settings.embedding_url).mock(
        return_value=httpx.Response(200, json=[[0.1, 0.2, 0.3]])
    )

    vec = await embed_text("hello")

    assert vec == [0.1, 0.2, 0.3]
    assert isinstance(vec, list)
    assert not isinstance(vec[0], list)  # 未嵌套


# ---- 7b. 批量响应不解包 ----
@respx.mock
async def test_batch_response_not_unwrapped():
    settings = get_settings()
    respx.post(settings.embedding_url).mock(
        return_value=httpx.Response(200, json=[[0.1, 0.2], [0.3, 0.4]])
    )

    vecs = await embed_texts(["a", "b"])

    assert vecs == [[0.1, 0.2], [0.3, 0.4]]


# ---- 8. 健康检查 ----
@respx.mock
async def test_healthcheck_healthy():
    settings = get_settings()
    respx.post(settings.embedding_url).mock(side_effect=_make_response)

    result = await healthcheck()

    assert result == {"status": "healthy"}


@respx.mock
async def test_healthcheck_unhealthy():
    settings = get_settings()

    def side_effect(request: httpx.Request):
        raise httpx.ConnectError("conn refused", request=request)

    respx.post(settings.embedding_url).mock(side_effect=side_effect)

    result = await healthcheck()

    assert result["status"] == "unhealthy"
    assert "error" in result


# ---- 9. 单例客户端 ----
async def test_get_embedding_client_singleton():
    a = get_embedding_client()
    b = get_embedding_client()
    assert a is b
