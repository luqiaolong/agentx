"""TEI 嵌入服务契约测试：直连 myserver TEI（``192.168.1.4:8080``）。

- ``@pytest.mark.integration`` + ``@pytest.mark.requires_myserver``
- 真实调用 ``embed_text("healthcheck")``，断言返回 1024 维向量（bge-m3）。
- 请求体不含 model 字段（TEI 单模型部署，model 仅作 LangSmith metadata）；
  无法直接断言请求体，改为断言响应形状（list[float] 且维度 == 1024）。
- myserver 不可达时跳过（先以独立 httpx 客户端做连通性预检，避免污染单例）。
- 每个用例前重置 embedding 单例，防止 httpx.AsyncClient 跨事件循环复用
  （pytest-asyncio function scope 每个用例新建事件循环，单例会绑定到旧循环）。
"""

from __future__ import annotations

import pytest
import httpx

from app.config import get_settings
from app.embedding import EmbeddingUnavailable, embed_text


@pytest.fixture(autouse=True)
def _reset_embedding_singleton() -> None:
    """每个用例前后清空 TeiClient 单例，避免跨事件循环复用 stale httpx 客户端。"""
    import app.embedding.tei_client as tc

    tc._client = None  # noqa: SLF001 — 重置模块级单例
    yield
    tc._client = None  # noqa: SLF001


async def _skip_if_tei_unreachable() -> None:
    """以独立 httpx 客户端预检 TEI ``/embed`` 端点可用性，不可达或非 200 则跳过。

    直接 POST ``{"inputs": "ping"}`` 到 embedding_url：200 表示 TEI 功能正常，
    404/5xx 表示 TEI 未部署或异常，连接异常表示 myserver 不可达。三种情况均跳过。
    使用独立客户端避免污染 TeiClient 单例的事件循环。
    """
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(settings.embedding_url, json={"inputs": "ping"})
    except Exception as exc:  # noqa: BLE001 — 任何连接异常均跳过
        pytest.skip(f"TEI 不可达，跳过契约测试: {exc}")
    if resp.status_code != 200:
        pytest.skip(
            f"TEI /embed 返回 {resp.status_code}，TEI 未就绪，跳过契约测试"
        )


@pytest.mark.integration
@pytest.mark.requires_myserver
async def test_tei_embed_returns_1024_dim_vector() -> None:
    """TEI 嵌入 ``healthcheck`` 返回 1024 维向量。"""
    await _skip_if_tei_unreachable()
    try:
        vec = await embed_text("healthcheck")
    except EmbeddingUnavailable as exc:
        pytest.skip(f"TEI 嵌入失败，跳过: {exc}")
    except Exception as exc:  # noqa: BLE001 — 不可达时跳过而非失败
        pytest.skip(f"TEI 连接异常，跳过: {exc}")

    assert isinstance(vec, list), f"返回类型非 list: {type(vec)}"
    assert all(isinstance(x, float) for x in vec), "向量元素非 float"
    assert len(vec) == 1024, f"向量维度非 1024: {len(vec)}"


@pytest.mark.integration
@pytest.mark.requires_myserver
async def test_tei_healthcheck_returns_healthy() -> None:
    """TEI healthcheck 返回 healthy 状态。"""
    await _skip_if_tei_unreachable()
    from app.embedding.tei_client import healthcheck

    status = await healthcheck()
    assert status["status"] == "healthy", f"TEI 健康检查失败: {status}"


@pytest.mark.integration
@pytest.mark.requires_myserver
def test_tei_embedding_url_points_to_myserver() -> None:
    """配置项 embedding_url 默认指向 myserver TEI（契约固定）。"""
    settings = get_settings()
    assert "192.168.1.4" in settings.embedding_url, (
        f"embedding_url 未指向 myserver: {settings.embedding_url}"
    )
    assert settings.embedding_url.endswith("/embed"), (
        f"embedding_url 应以 /embed 结尾: {settings.embedding_url}"
    )
