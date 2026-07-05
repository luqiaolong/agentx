"""Milvus 向量库契约测试：直连 myserver Milvus（``192.168.1.4:19530``）。

- ``@pytest.mark.integration`` + ``@pytest.mark.requires_myserver``
- 凭证（``AGENTX_MILVUS_USER`` / ``AGENTX_MILVUS_PASSWORD``）需在环境变量中预置，
  或设置 ``AGENTX_MILVUS_AUTH_ENABLED=false``（myserver auth disabled 模式）。
  由于 conftest 的 autouse fixture 会清除 ``AGENTX_*`` 变量，此处模块级捕获原始
  凭证/配置，测试内通过 ``monkeypatch.setenv`` 重新注入并刷新 ``get_settings`` 缓存。
- myserver 不可达或凭证缺失时跳过。
- 测试流程：healthcheck healthy → ingest 1 doc → search 命中 → delete_by_source 清理。
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from app.config import get_settings
from app.vectorstore import MilvusUnavailable, delete_by_source, get_milvus_client, ingest, search

# 模块级捕获原始凭证/配置（在 autouse fixture 清除环境变量之前执行）。
_MILVUS_USER = os.environ.get("AGENTX_MILVUS_USER")
_MILVUS_PASSWORD = os.environ.get("AGENTX_MILVUS_PASSWORD")
_MILVUS_AUTH_ENABLED = os.environ.get("AGENTX_MILVUS_AUTH_ENABLED", "true")


def _skip_if_no_credentials() -> None:
    # auth disabled 模式不需要凭证
    if _MILVUS_AUTH_ENABLED.lower() == "false":
        return
    if not _MILVUS_USER or not _MILVUS_PASSWORD:
        pytest.skip("AGENTX_MILVUS_USER/PASSWORD 未设置，跳过 Milvus 契约测试")


@pytest.fixture
def milvus_settings(monkeypatch: pytest.MonkeyPatch):
    """重新注入 Milvus 凭证/配置并返回刷新后的 Settings。"""
    _skip_if_no_credentials()
    if _MILVUS_AUTH_ENABLED.lower() == "false":
        monkeypatch.setenv("AGENTX_MILVUS_AUTH_ENABLED", "false")
    else:
        monkeypatch.setenv("AGENTX_MILVUS_USER", _MILVUS_USER)  # type: ignore[arg-type]
        monkeypatch.setenv("AGENTX_MILVUS_PASSWORD", _MILVUS_PASSWORD)  # type: ignore[arg-type]
    get_settings.cache_clear()
    return get_settings()


@pytest.mark.integration
@pytest.mark.requires_myserver
async def test_milvus_healthcheck_healthy(milvus_settings) -> None:
    """Milvus healthcheck 返回 healthy。"""
    client = get_milvus_client()
    try:
        status = await client.healthcheck()
    except MilvusUnavailable as exc:
        pytest.skip(f"Milvus 不可达，跳过: {exc}")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Milvus 连接异常，跳过: {exc}")
    assert status["status"] == "healthy", f"Milvus 健康检查失败: {status}"


@pytest.mark.integration
@pytest.mark.requires_myserver
async def test_milvus_ingest_search_delete_roundtrip(milvus_settings) -> None:
    """ingest 1 doc → search 命中 → delete_by_source 清理（端到端往返）。"""
    client = get_milvus_client()

    # 1. 连接（不可达则跳过）
    try:
        await client.connect()
    except MilvusUnavailable as exc:
        pytest.skip(f"Milvus 不可达，跳过: {exc}")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Milvus 连接异常，跳过: {exc}")

    source = f"agentx-test-{uuid.uuid4()}"
    text = "agentx milvus contract test 端到端往返内容"
    metadata = {
        "source": source,
        "chunk_idx": 0,
        "created_at": int(time.time()),
    }

    try:
        # 2. ingest 1 条（source_type=manual 分区）
        ids = await ingest([text], [metadata], "manual")
        assert len(ids) >= 1, "ingest 未返回插入 id"

        # 3. search 命中（同一文本检索，应能召回刚入库的 source）
        hits = await search(text, top_k=5)
        sources = [s for (_t, s, _score) in hits]
        assert source in sources, f"search 未命中刚入库的 source，实际: {sources}"

        # 4. delete_by_source 清理
        deleted = await delete_by_source(source)
        assert deleted >= 1, f"delete_by_source 未删除记录，返回: {deleted}"

        # 5. 再次 search 确认已清理
        hits_after = await search(text, top_k=5)
        sources_after = [s for (_t, s, _score) in hits_after]
        assert source not in sources_after, "删除后仍能检索到该 source，清理失败"
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001 — 清理阶段兜底
            pass
