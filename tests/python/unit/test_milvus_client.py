"""Milvus 客户端单测：mock pymilvus，不依赖真实 Milvus 连接
覆盖1. ingest 成功 ?partition_name=source_type，返id 列表
2. search 成功 ?(text, source, score) 降序，无 partition_name
3. delete_by_source_type ?drop_partition + Partition 重建
4. delete_by_source ?Collection.delete(filter=...)
5. healthcheck 凭证缺失 ?no_credentials
6. healthcheck 鉴权失败 ?auth_failed
7. healthcheck DB 不存db_not_found
8. ingest 超长文本 ?on_skip 调用，被跳过文本不入9. search 失败 ??MilvusUnavailable
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import get_settings
from app.vectorstore.milvus_client import (
    MilvusClient,
    MilvusUnavailable,
    _VALID_SOURCE_TYPES,
)


# ---------------- fixtures ----------------

@pytest.fixture(autouse=True)
def _isolate_settings_and_singleton():
    """每个用例前后清理 settings lru_cache + MilvusClient 单例，避免污染"""
    get_settings.cache_clear()
    import app.vectorstore.milvus_client as mod
    mod._client = None
    yield
    get_settings.cache_clear()
    mod._client = None


def _make_client_with_collection() -> MilvusClient:
    """构造一个已连接（_collection=MagicMock）的 MilvusClient，供 ingest/search/delete 用例使用"""
    client = MilvusClient()
    client._collection = MagicMock()
    client._connected = True
    return client


def _fake_hit(text: str, source: str, score: float) -> MagicMock:
    """构造一个模拟的 Milvus search hit?"""
    hit = MagicMock()
    hit.score = score
    hit.distance = score
    entity = MagicMock()
    entity.get = lambda key, default=None: {"text": text, "source": source}.get(key, default)
    hit.entity = entity
    return hit


# ---------------- 1. ingest 成功 ----------------

@pytest.mark.asyncio
async def test_ingest_success_returns_ids_and_uses_partition():
    client = _make_client_with_collection()
    fake_vectors = [[0.1] * 1024, [0.2] * 1024]

    with patch(
        "app.vectorstore.milvus_client.embed_texts",
        new=AsyncMock(return_value=fake_vectors),
    ):
        # collection.insert 返回 MutationResult-like 对象
        mutation_result = MagicMock()
        mutation_result.primary_keys = [101, 102]
        client._collection.insert = MagicMock(return_value=mutation_result)

        ids = await client.ingest(
            texts=["hello world", "foo bar"],
            metadatas=[
                {"source": "a.pdf", "chunk_idx": 0},
                {"source": "a.pdf", "chunk_idx": 1},
            ],
            source_type="file",
        )

    assert ids == [101, 102]
    # insert 调用必须partition_name="file"
    client._collection.insert.assert_called_once()
    call_kwargs = client._collection.insert.call_args.kwargs
    assert call_kwargs["partition_name"] == "file"
    # data 第一组应kept_texts
    data = call_kwargs["data"]
    assert data[0] == ["hello world", "foo bar"]
    assert data[2] == ["file", "file"]  # source_type ?

# ---------------- 2. search 成功 ----------------

@pytest.mark.asyncio
async def test_search_success_returns_sorted_tuples_without_partition_name():
    client = _make_client_with_collection()
    fake_query_vec = [0.5] * 1024

    with patch(
        "app.vectorstore.milvus_client.embed_text",
        new=AsyncMock(return_value=fake_query_vec),
    ):
        # Milvus search 返回 [[hit, hit, ...]]；故意打乱顺序验证降
        hits = [
            _fake_hit("text_b", "src_b", 0.75),
            _fake_hit("text_a", "src_a", 0.95),
            _fake_hit("text_c", "src_c", 0.85),
        ]
        client._collection.search = MagicMock(return_value=[hits])

        results = await client.search(
            query="hello",
            top_k=3,
            filter='source_type == "file"',
        )

    assert len(results) == 3
    # 验证 (text, source, score) 元组
    assert results[0] == ("text_a", "src_a", 0.95)
    assert results[1] == ("text_c", "src_c", 0.85)
    assert results[2] == ("text_b", "src_b", 0.75)
    # 验证 search 调用未传 partition_name（partition 仅用于删除）
    client._collection.search.assert_called_once()
    search_kwargs = client._collection.search.call_args.kwargs
    assert "partition_name" not in search_kwargs
    assert search_kwargs["expr"] == 'source_type == "file"'
    assert search_kwargs["anns_field"] == "vector"
    assert search_kwargs["limit"] == 3
    assert search_kwargs["output_fields"] == ["text", "source"]


# ---------------- 3. delete_by_source_type ----------------

@pytest.mark.asyncio
async def test_delete_by_source_type_drops_and_recreates_partition():
    client = _make_client_with_collection()
    client._collection.drop_partition = MagicMock()
    fake_partition_cls = MagicMock()

    with patch("app.vectorstore.milvus_client.Partition", new=fake_partition_cls):
        await client.delete_by_source_type("web")

    # drop_partition 必须"web" 调用
    client._collection.drop_partition.assert_called_once_with("web")
    # Partition 必须(collection, "web") 重建
    fake_partition_cls.assert_called_once_with(client._collection, "web")


# ---------------- 4. delete_by_source ----------------

@pytest.mark.asyncio
async def test_delete_by_source_calls_delete_with_filter_expr():
    client = _make_client_with_collection()
    mutation_result = MagicMock()
    mutation_result.delete_count = 5
    client._collection.delete = MagicMock(return_value=mutation_result)

    count = await client.delete_by_source("/data/uploads/old.pdf")

    assert count == 5
    client._collection.delete.assert_called_once()
    call_kwargs = client._collection.delete.call_args.kwargs
    assert call_kwargs["expr"] == 'source == "/data/uploads/old.pdf"'


@pytest.mark.asyncio
async def test_delete_by_source_escapes_quotes_safely():
    """source 含双引号时必须转义，避免 filter 注入"""
    client = _make_client_with_collection()
    mutation_result = MagicMock()
    mutation_result.delete_count = 0
    client._collection.delete = MagicMock(return_value=mutation_result)

    await client.delete_by_source('path/with"quote')

    call_kwargs = client._collection.delete.call_args.kwargs
    # 双引号被转义
    assert call_kwargs["expr"] == 'source == "path/with\\"quote"'


# ---------------- 5. healthcheck 凭证缺失 ----------------

@pytest.mark.asyncio
async def test_healthcheck_no_credentials(monkeypatch):
    # 清空凭证环境变量，auth ?enabled（默认）
    monkeypatch.delenv("AGENTX_MILVUS_USER", raising=False)
    monkeypatch.delenv("AGENTX_MILVUS_PASSWORD", raising=False)
    monkeypatch.delenv("AGENTX_MILVUS_AUTH_ENABLED", raising=False)
    get_settings.cache_clear()

    client = MilvusClient()
    result = await client.healthcheck()

    assert result["status"] == "unhealthy"
    assert result["error_code"] == "no_credentials"
    assert "not set" in result["error"].lower()


# ---------------- 5b. healthcheck auth disabled 跳过凭证校验 ----------------

@pytest.mark.asyncio
async def test_healthcheck_auth_disabled_skips_credentials(monkeypatch):
    # auth disabled 时不需要凭
    monkeypatch.delenv("AGENTX_MILVUS_USER", raising=False)
    monkeypatch.delenv("AGENTX_MILVUS_PASSWORD", raising=False)
    monkeypatch.setenv("AGENTX_MILVUS_AUTH_ENABLED", "false")
    get_settings.cache_clear()

    client = MilvusClient()
    fake_connections = MagicMock()
    fake_utility = MagicMock()
    fake_utility.list_databases = MagicMock(return_value=["agentx", "default"])

    with patch("app.vectorstore.milvus_client.connections", new=fake_connections), \
         patch("app.vectorstore.milvus_client.utility", new=fake_utility):
        result = await client.healthcheck()

    assert result["status"] == "healthy"
    assert "latency_ms" in result
    assert result["collection"] == "agentx_knowledge"


# ---------------- 6. healthcheck 鉴权失败 ----------------

@pytest.mark.asyncio
async def test_healthcheck_auth_failed(monkeypatch):
    monkeypatch.setenv("AGENTX_MILVUS_USER", "wrong_user")
    monkeypatch.setenv("AGENTX_MILVUS_PASSWORD", "wrong_pass")
    get_settings.cache_clear()

    client = MilvusClient()

    fake_connections = MagicMock()
    fake_connections.connect.side_effect = Exception(
        "auth failed: invalid username or password"
    )

    with patch("app.vectorstore.milvus_client.connections", new=fake_connections):
        result = await client.healthcheck()

    assert result["status"] == "unhealthy"
    assert result["error_code"] == "auth_failed"
    assert "auth failed" in result["error"].lower()


# ---------------- 7. healthcheck DB 不存----------------

@pytest.mark.asyncio
async def test_healthcheck_db_not_found(monkeypatch):
    monkeypatch.setenv("AGENTX_MILVUS_USER", "user")
    monkeypatch.setenv("AGENTX_MILVUS_PASSWORD", "pass")
    get_settings.cache_clear()

    client = MilvusClient()

    fake_connections = MagicMock()  # connect 成功
    fake_utility = MagicMock()
    # 返回不含 "agentx" 的数据库列表
    fake_utility.list_databases = MagicMock(return_value=["other_db", "default"])

    with patch("app.vectorstore.milvus_client.connections", new=fake_connections), \
         patch("app.vectorstore.milvus_client.utility", new=fake_utility):
        result = await client.healthcheck()

    assert result["status"] == "unhealthy"
    assert result["error_code"] == "db_not_found"
    assert "agentx" in result["error"]
    assert "attu" in result["error"].lower() or "create_database" in result["error"].lower()


# ---------------- 健康检healthy 路径（额外覆盖） ----------------

@pytest.mark.asyncio
async def test_healthcheck_healthy(monkeypatch):
    monkeypatch.setenv("AGENTX_MILVUS_USER", "user")
    monkeypatch.setenv("AGENTX_MILVUS_PASSWORD", "pass")
    get_settings.cache_clear()

    client = MilvusClient()
    fake_connections = MagicMock()
    fake_utility = MagicMock()
    fake_utility.list_databases = MagicMock(return_value=["agentx", "default"])

    with patch("app.vectorstore.milvus_client.connections", new=fake_connections), \
         patch("app.vectorstore.milvus_client.utility", new=fake_utility):
        result = await client.healthcheck()

    assert result["status"] == "healthy"
    assert "latency_ms" in result
    assert result["collection"] == "agentx_knowledge"


# ---------------- 8. ingest 超长文本跳过 ----------------

@pytest.mark.asyncio
async def test_ingest_skips_too_long_text_via_on_skip():
    client = _make_client_with_collection()

    captured_on_skip = {}

    async def _fake_embed_texts(texts, on_skip=None):
        # 模拟 TEI 客户端契约：on_skip(text, err) ?2 个参
        from app.embedding import TextTooLongError
        vectors = []
        for idx, text in enumerate(texts):
            if len(text) > 24000:
                if on_skip is not None:
                    err = TextTooLongError(
                        f"文本长度 {len(text)} 超过上限 24000，请通过分块器切短后重试"
                    )
                    on_skip(text, err)
                vectors.append(None)
            else:
                vectors.append([0.1] * 1024)
        return vectors

    with patch(
        "app.vectorstore.milvus_client.embed_texts",
        new=_fake_embed_texts,
    ):
        mutation_result = MagicMock()
        mutation_result.primary_keys = [201, 202]
        client._collection.insert = MagicMock(return_value=mutation_result)

        long_text = "x" * 30000
        ids = await client.ingest(
            texts=["short1", long_text, "short2"],
            metadatas=[
                {"source": "doc.pdf", "chunk_idx": 0},
                {"source": "doc.pdf", "chunk_idx": 1},
                {"source": "doc.pdf", "chunk_idx": 2},
            ],
            source_type="file",
        )

    # ?2 条短文本入库
    assert ids == [201, 202]
    client._collection.insert.assert_called_once()
    call_kwargs = client._collection.insert.call_args.kwargs
    data = call_kwargs["data"]
    # data[0] ?text 列表，应只有 2 条（不含长文本）
    assert data[0] == ["short1", "short2"]
    # partition 仍是 file
    assert call_kwargs["partition_name"] == "file"


# ---------------- 9. search 失败MilvusUnavailable ----------------

@pytest.mark.asyncio
async def test_search_raises_milvus_unavailable_on_failure():
    client = _make_client_with_collection()

    with patch(
        "app.vectorstore.milvus_client.embed_text",
        new=AsyncMock(return_value=[0.1] * 1024),
    ):
        client._collection.search = MagicMock(
            side_effect=Exception("connection lost: grpc unavailable")
        )

        with pytest.raises(MilvusUnavailable):
            await client.search(query="anything", top_k=5)


# ---------------- 额外：ingest 非法 source_type ----------------

@pytest.mark.asyncio
async def test_ingest_rejects_invalid_source_type():
    client = _make_client_with_collection()
    with pytest.raises(ValueError, match="invalid source_type"):
        await client.ingest(
            texts=["x"],
            metadatas=[{"source": "a", "chunk_idx": 0}],
            source_type="invalid_type",
        )


# ---------------- 额外：ingest 文本与元数据长度不匹----------------

@pytest.mark.asyncio
async def test_ingest_rejects_length_mismatch():
    client = _make_client_with_collection()
    with pytest.raises(ValueError, match="length mismatch"):
        await client.ingest(
            texts=["a", "b"],
            metadatas=[{"source": "a", "chunk_idx": 0}],
            source_type="file",
        )


# ---------------- 额外：未连接时调ingest ?MilvusUnavailable ----------------

@pytest.mark.asyncio
async def test_ingest_raises_when_not_connected():
    client = MilvusClient()  # _collection=None
    with patch(
        "app.vectorstore.milvus_client.embed_texts",
        new=AsyncMock(return_value=[[0.1] * 1024]),
    ):
        with pytest.raises(MilvusUnavailable, match="not connected"):
            await client.ingest(
                texts=["x"],
                metadatas=[{"source": "a", "chunk_idx": 0}],
                source_type="file",
            )


# ---------------- 额外：get_milvus_client 单例 ----------------

def test_get_milvus_client_returns_singleton():
    import app.vectorstore.milvus_client as mod
    a = mod.get_milvus_client()
    b = mod.get_milvus_client()
    assert a is b


# ---------------- 额外：模块级委托 ingest ----------------

@pytest.mark.asyncio
async def test_module_level_ingest_delegates_to_singleton():
    import app.vectorstore.milvus_client as mod

    fake_vectors = [[0.1] * 1024]
    with patch(
        "app.vectorstore.milvus_client.embed_texts",
        new=AsyncMock(return_value=fake_vectors),
    ):
        # 替换单例为一个已连接mock 客户
        singleton_client = _make_client_with_collection()
        mutation_result = MagicMock()
        mutation_result.primary_keys = [999]
        singleton_client._collection.insert = MagicMock(return_value=mutation_result)
        mod._client = singleton_client

        ids = await mod.ingest(
            texts=["x"],
            metadatas=[{"source": "a", "chunk_idx": 0}],
            source_type="web",
        )

    assert ids == [999]
    singleton_client._collection.insert.assert_called_once()
    assert singleton_client._collection.insert.call_args.kwargs["partition_name"] == "web"


# ---------------- 额外：delete_by_source_type 非法类型 ----------------

@pytest.mark.asyncio
async def test_delete_by_source_type_rejects_invalid_type():
    client = _make_client_with_collection()
    with pytest.raises(ValueError, match="invalid source_type"):
        await client.delete_by_source_type("invalid")


# ---------------- 额外：所有合source_type 可被 delete_by_source_type ----------------

@pytest.mark.asyncio
async def test_delete_by_source_type_accepts_all_valid_types():
    for st in _VALID_SOURCE_TYPES:
        client = _make_client_with_collection()
        client._collection.drop_partition = MagicMock()
        with patch("app.vectorstore.milvus_client.Partition", new=MagicMock()):
            await client.delete_by_source_type(st)
        client._collection.drop_partition.assert_called_once_with(st)
