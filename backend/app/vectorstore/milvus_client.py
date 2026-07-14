"""Milvus 向量库客户端（pymilvus 经典 API）。

通过 ``connections.connect`` + ``Collection`` + ``Partition`` + ``utility`` 连接
myserver Milvus（默认 ``192.168.1.4:19530``），按 ``source_type`` 分区入库，
HNSW + COSINE 检索。MUST NOT 使用 ``AsyncMilvusClient`` Lite 模式。

设计要点：
- pymilvus 顶层 try/except import：模块导入不依赖 pymilvus 已安装，便于单测 mock。
- 嵌入服务 (``app.embedding``) 同样 try/except import：TEI 客户端未实现时不阻断导入。
- 同步 pymilvus 调用通过 ``asyncio.to_thread`` 包装以适配 FastAPI async。
- partition 仅用于批量删除（``drop_partition``），查询统一走 ``filter`` 表达式。
- LangSmith trace：ingest/search span metadata 不含原始文本（``mark_redacted``）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.config import get_settings
from app.observability.logger import logger

# ---- pymilvus 顶层 import（失败时降级为 None，便于单测 mock）----
try:  # pragma: no cover - 真实环境 pymilvus 已安装
    from pymilvus import (  # type: ignore[import-not-found]
        Collection,
        CollectionSchema,
        DataType,
        FieldSchema,
        Partition,
        connections,
        utility,
    )
except ImportError:  # pragma: no cover
    Collection = None  # type: ignore[assignment]
    CollectionSchema = None  # type: ignore[assignment]
    DataType = None  # type: ignore[assignment]
    FieldSchema = None  # type: ignore[assignment]
    Partition = None  # type: ignore[assignment]
    connections = None  # type: ignore[assignment]
    utility = None  # type: ignore[assignment]


# ---- 嵌入服务 import（tei_client 可能尚未实现，try/except 兜底）----
try:
    from app.embedding import TextTooLongError, embed_text, embed_texts
except ImportError:  # pragma: no cover - tei_client 尚未实现时兜底
    TextTooLongError = None  # type: ignore[assignment]
    embed_text = None  # type: ignore[assignment]
    embed_texts = None  # type: ignore[assignment]


__all__ = [
    "MilvusUnavailable",
    "MilvusClient",
    "LangChainMilvusVectorStore",
    "get_milvus_client",
    "ingest",
    "search",
    "delete_by_source",
    "delete_by_source_type",
]


class MilvusUnavailable(Exception):
    """Milvus 不可用时抛出（凭证缺失 / 鉴权失败 / DB 缺失 / 网络不可达 / collection 未就绪）。


    由 ``ingest`` / ``search`` / ``delete_*`` 在底层调用失败时抛出，``healthcheck``
    不抛出而是返回 unhealthy dict。
    """


# 合法 source_type 与 partition 名一一对应
_VALID_SOURCE_TYPES: tuple[str, ...] = ("file", "web", "manual")

# Milvus 连接 alias（全局唯一，避免重复 connect 警告）
_ALIAS = "agentx"

# 鉴权失败关键字（用于错误信息分类）
_AUTH_KEYWORDS: tuple[str, ...] = (
    "auth",
    "denied",
    "credential",
    "unauthorized",
    "login",
    "password",
)

# DB 不存在关键字（pymilvus 2.4+ 抛含 'database' / 'not found' 的异常）
_DB_NOT_FOUND_KEYWORDS: tuple[str, ...] = (
    "database",
    "db ",
    "not found",
    "doesn't exist",
    "does not exist",
)


def _classify_connect_error(exc: Exception) -> str:
    """将 pymilvus connect 异常映射为 error_code。

    优先级：auth_failed > db_not_found > unreachable。
    """
    msg = str(exc).lower()
    if any(kw in msg for kw in _AUTH_KEYWORDS):
        return "auth_failed"
    if any(kw in msg for kw in _DB_NOT_FOUND_KEYWORDS):
        return "db_not_found"
    return "unreachable"


class MilvusClient:
    """Milvus 客户端单例：持有 connection + Collection 引用。

    生命周期：
    - ``connect()`` 在 FastAPI lifespan 启动时调用，校验凭证/DB，自动建 collection。
    - ``ingest`` / ``search`` / ``delete_*`` 在 ``connect()`` 成功后可用。
    - ``healthcheck()`` 独立做轻量检查，不依赖 ``connect()`` 已调用。
    - ``disconnect()`` 在 lifespan 关闭时调用。
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._collection: Any = None  # pymilvus.Collection 实例
        self._connected: bool = False

    # ---------------- 连接管理 ----------------

    async def connect(self) -> None:
        """连接 Milvus，校验 DB 存在，自动创建 collection + 3 partition + HNSW index。

        raises MilvusUnavailable: 凭证缺失 / 鉴权失败 / DB 不存在 / 网络不可达 /
            collection 初始化失败。
        """
        settings = self._settings
        if not settings.milvus_credentials_configured:
            raise MilvusUnavailable(
                "Milvus credentials missing (AGENTX_MILVUS_USER/PASSWORD not set)"
            )
        if connections is None:  # pragma: no cover
            raise MilvusUnavailable("pymilvus not installed")

        # 1. 连接（鉴权失败 / 网络不可达在此分流）
        # auth disabled 时 user/password 传空字符串（pymilvus 要求 str 类型）
        connect_user = settings.milvus_user or ""
        connect_password = settings.milvus_password or ""
        try:
            await asyncio.to_thread(
                connections.connect,
                alias=_ALIAS,
                host=settings.milvus_host,
                port=str(settings.milvus_port),
                user=connect_user,
                password=connect_password,
                db_name=settings.milvus_db,
            )
        except MilvusUnavailable:
            raise
        except Exception as exc:
            code = _classify_connect_error(exc)
            if code == "auth_failed":
                raise MilvusUnavailable(f"Milvus auth failed: {exc}") from exc
            raise MilvusUnavailable(f"Milvus unreachable: {exc}") from exc

        # 2. 校验 DB 存在（pymilvus 2.4+ 提供 utility.list_databases）
        try:
            databases = await asyncio.to_thread(utility.list_databases)
        except Exception:
            # 老版本无 list_databases，依赖 connect 阶段已校验，跳过
            databases = [settings.milvus_db]
        if settings.milvus_db not in databases:
            # DB 不存在 → 尝试自动创建（需先切到 default DB）
            try:
                await asyncio.to_thread(
                    connections.connect,
                    alias=_ALIAS,
                    host=settings.milvus_host,
                    port=str(settings.milvus_port),
                    user=connect_user,
                    password=connect_password,
                    db_name="default",
                )
                await asyncio.to_thread(
                    utility.create_database, settings.milvus_db, using=_ALIAS
                )
                logger.info(
                    "Milvus database '{}' 自动创建成功", settings.milvus_db
                )
                # 重连到目标 DB
                await asyncio.to_thread(connections.disconnect, _ALIAS)
                await asyncio.to_thread(
                    connections.connect,
                    alias=_ALIAS,
                    host=settings.milvus_host,
                    port=str(settings.milvus_port),
                    user=connect_user,
                    password=connect_password,
                    db_name=settings.milvus_db,
                )
            except Exception as create_exc:
                try:
                    await asyncio.to_thread(connections.disconnect, _ALIAS)
                except Exception:
                    pass
                raise MilvusUnavailable(
                    f"db '{settings.milvus_db}' not found and auto-create failed: "
                    f"{create_exc}; please create via Attu or pymilvus create_database"
                ) from create_exc

        # 3. 自动创建 collection + partition + HNSW index
        try:
            has_collection = await asyncio.to_thread(
                utility.has_collection, settings.milvus_collection, using=_ALIAS
            )
            if not has_collection:
                schema = self._build_schema()
                self._collection = await asyncio.to_thread(
                    Collection,
                    name=settings.milvus_collection,
                    schema=schema,
                    using=_ALIAS,
                )
                await asyncio.to_thread(
                    self._collection.create_index,
                    field_name="vector",
                    index_params={
                        "index_type": settings.milvus_hnsw_index_type,
                        "metric_type": "COSINE",
                        "params": {
                            "M": settings.milvus_hnsw_m,
                            "efConstruction": settings.milvus_hnsw_ef_construction,
                        },
                    },
                )
                for partition_name in _VALID_SOURCE_TYPES:
                    await asyncio.to_thread(Partition, self._collection, partition_name)
            else:
                self._collection = await asyncio.to_thread(
                    Collection,
                    name=settings.milvus_collection,
                    using=_ALIAS,
                )
            await asyncio.to_thread(self._collection.load)
        except MilvusUnavailable:
            raise
        except Exception as exc:
            raise MilvusUnavailable(
                f"Milvus collection setup failed: {exc}"
            ) from exc

        self._connected = True
        logger.info(
            "Milvus connected: alias={} db={} collection={}",
            _ALIAS,
            settings.milvus_db,
            settings.milvus_collection,
        )

    def _build_schema(self) -> Any:
        """构造 collection schema（向量维度由 settings.embedding_dim 决定）。"""
        settings = get_settings()
        fields = [
            FieldSchema("id", DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema("text", DataType.VARCHAR, max_length=65535),
            FieldSchema("source", DataType.VARCHAR, max_length=512),
            FieldSchema("source_type", DataType.VARCHAR, max_length=32),
            FieldSchema("chunk_idx", DataType.INT32),
            FieldSchema("created_at", DataType.INT64),
            FieldSchema("vector", DataType.FLOAT_VECTOR, dim=settings.embedding_dim),
        ]
        return CollectionSchema(fields=fields, description="AgentX knowledge base")

    async def disconnect(self) -> None:
        """断开 Milvus 连接。"""
        if connections is None:  # pragma: no cover
            self._collection = None
            self._connected = False
            return
        try:
            await asyncio.to_thread(connections.disconnect, _ALIAS)
        except Exception as exc:
            logger.warning("Milvus disconnect failed: {}", exc)
        self._collection = None
        self._connected = False

    async def aclose(self) -> None:
        """``disconnect`` 的异步别名，统一双入口命名。"""
        await self.disconnect()

    def close(self) -> None:
        """同步入口关闭 Milvus 连接；运行事件循环中请用 ``aclose()``/``disconnect()``。"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
            return
        logger.warning(
            "%s.close() called inside a running event loop; "
            "use aclose() instead. Connection may leak.",
            self.__class__.__name__,
        )

    def _ensure_collection(self) -> Any:
        """返回已加载的 Collection，未连接时抛 MilvusUnavailable。"""
        if self._collection is None:
            raise MilvusUnavailable(
                "Milvus client not connected, call connect() first"
            )
        return self._collection

    # ---------------- 入库 ----------------

    async def ingest(
        self,
        texts: list[str],
        metadatas: list[dict],
        source_type: str,
    ) -> list[int]:
        """批量入库文本+元数据到指定 source_type 分区。

        - 先调用 ``embed_texts(texts, on_skip=...)`` 取向量（超长文本跳过并记日志）。
        - 再 ``Collection.insert(data=[...], partition_name=source_type)``。
        - 返回插入的 auto_id 列表（仅成功插入的条目）。

        raises MilvusUnavailable: embedding 不可用 / Milvus 未连接 / insert 失败。
        raises ValueError: source_type 非法 / texts 与 metadatas 长度不匹配。
        """
        if source_type not in _VALID_SOURCE_TYPES:
            raise ValueError(
                f"invalid source_type: {source_type!r}, expected one of {_VALID_SOURCE_TYPES}"
            )
        if len(texts) != len(metadatas):
            raise ValueError(
                f"texts ({len(texts)}) and metadatas ({len(metadatas)}) length mismatch"
            )
        if embed_texts is None:
            raise MilvusUnavailable("embedding module not available")
        collection = self._ensure_collection()

        # 1. 嵌入（on_skip 回调记录被跳过的 source + chunk_idx）
        # tei_client.embed_texts 调用约定：on_skip(text, err) — 2 个参数。
        # 此处通过闭包捕获 texts 索引以记录 source + chunk_idx。
        skipped_indices: list[int] = []

        def _on_skip(text: str, err: Any) -> None:
            # 在 texts 中定位被跳过的索引（按对象身份匹配，避免重复文本误判）
            meta = {}
            for i, t in enumerate(texts):
                if t is text:
                    if i not in skipped_indices:
                        skipped_indices.append(i)
                    meta = metadatas[i] if i < len(metadatas) else {}
                    break
            logger.warning(
                "skip chunk in ingest: source={!r} chunk_idx={!r} reason={}",
                meta.get("source"),
                meta.get("chunk_idx"),
                str(err),
            )

        try:
            vectors = await embed_texts(texts, on_skip=_on_skip)
        except Exception as exc:
            raise MilvusUnavailable(f"embedding failed: {exc}") from exc

        # vectors[i] 为 None 表示该位置文本被跳过
        kept_indices = [i for i, v in enumerate(vectors) if v is not None]
        if not kept_indices:
            logger.warning("ingest: all chunks skipped, nothing to insert")
            return []

        now = int(time.time())
        kept_texts = [texts[i] for i in kept_indices]
        kept_sources = [metadatas[i].get("source", "") for i in kept_indices]
        kept_source_types = [source_type for _ in kept_indices]
        kept_chunk_idx = [int(metadatas[i].get("chunk_idx", 0)) for i in kept_indices]
        kept_created_at = [
            int(metadatas[i].get("created_at", now)) for i in kept_indices
        ]
        kept_vectors = [vectors[i] for i in kept_indices]

        data = [
            kept_texts,
            kept_sources,
            kept_source_types,
            kept_chunk_idx,
            kept_created_at,
            kept_vectors,
        ]

        # 2. 插入到对应 partition
        try:
            result = await asyncio.to_thread(
                collection.insert,
                data=data,
                partition_name=source_type,
            )
        except Exception as exc:
            raise MilvusUnavailable(
                f"Milvus insert failed: {exc}"
            ) from exc

        # MutationResult.primary_keys -> numpy array of inserted ids
        primary_keys = getattr(result, "primary_keys", None)
        if primary_keys is None:
            return []
        try:
            return [int(pk) for pk in list(primary_keys)]
        except TypeError:
            return list(primary_keys)

    # ---------------- 检索 ----------------

    async def search(
        self,
        query: str,
        top_k: int = 5,
        filter: str | None = None,
    ) -> list[tuple[str, str, float]]:
        """向量检索：嵌入 query → HNSW 检索 top_k → 返回 (text, source, score) 降序。

        MUST NOT 传 partition_name（partition 仅用于删除，查询统一走 filter）。
        raises MilvusUnavailable: embedding 不可用 / Milvus 未连接 / search 失败。
        """
        if embed_text is None:
            raise MilvusUnavailable("embedding module not available")
        collection = self._ensure_collection()

        start = time.perf_counter()

        # 1. 嵌入 query
        try:
            vec = await embed_text(query)
        except Exception as exc:
            raise MilvusUnavailable(f"embedding failed: {exc}") from exc

        # 2. HNSW 检索（不传 partition_name，仅用 expr/filter）
        try:
            results = await asyncio.to_thread(
                collection.search,
                data=[vec],
                anns_field="vector",
                param={
                    "metric_type": "COSINE",
                    "params": {"ef": get_settings().milvus_hnsw_ef_search},
                },
                limit=top_k,
                expr=filter,
                output_fields=["text", "source"],
            )
        except Exception as exc:
            raise MilvusUnavailable(
                f"Milvus search failed: {exc}"
            ) from exc

        latency_ms = int((time.perf_counter() - start) * 1000)

        # 3. 解析结果（Milvus COSINE 越大越相似，已按 score 降序返回）
        hits: list[tuple[str, str, float]] = []
        search_results = results[0] if results else []
        for hit in search_results:
            entity = getattr(hit, "entity", None)
            if entity is not None and hasattr(entity, "get"):
                text_val = entity.get("text")
                source_val = entity.get("source")
            else:
                fields = getattr(hit, "fields", {}) or {}
                text_val = fields.get("text")
                source_val = fields.get("source")
            score_val = getattr(hit, "score", None)
            if score_val is None:
                score_val = getattr(hit, "distance", 0.0)
            hits.append((str(text_val or ""), str(source_val or ""), float(score_val)))

        # 防御性按 score 降序（Milvus 一般已排序）
        hits.sort(key=lambda x: x[2], reverse=True)

        logger.debug(
            "vectorstore.milvus.search done",
            top_k=top_k,
            filter=filter,
            result_count=len(hits),
            latency_ms=latency_ms,
        )

        return hits

    # ---------------- 删除 ----------------

    async def delete_by_source(self, source: str) -> int:
        """按 source 删除所有匹配记录，返回删除条数。

        raises MilvusUnavailable: Milvus 未连接 / delete 失败。
        """
        collection = self._ensure_collection()
        # 转义双引号防 filter 注入
        escaped = source.replace("\\", "\\\\").replace('"', '\\"')
        expr = f'source == "{escaped}"'
        try:
            result = await asyncio.to_thread(collection.delete, expr=expr)
        except Exception as exc:
            raise MilvusUnavailable(
                f"Milvus delete_by_source failed: {exc}"
            ) from exc
        # MutationResult.delete_count
        count = getattr(result, "delete_count", None)
        if count is None:
            count = getattr(result, "delete_cnt", None)
        if count is None:
            count = 0
        try:
            return int(count)
        except (TypeError, ValueError):
            return 0

    async def delete_by_source_type(self, source_type: str) -> None:
        """按 source_type 批量删除：drop_partition + 重建空 partition。

        raises ValueError: source_type 非法。
        raises MilvusUnavailable: Milvus 未连接 / drop/recreate 失败。
        """
        if source_type not in _VALID_SOURCE_TYPES:
            raise ValueError(
                f"invalid source_type: {source_type!r}, expected one of {_VALID_SOURCE_TYPES}"
            )
        collection = self._ensure_collection()
        if Partition is None:  # pragma: no cover
            raise MilvusUnavailable("pymilvus not installed")
        try:
            # drop_partition（幂等：不存在时忽略错误）
            try:
                await asyncio.to_thread(collection.drop_partition, source_type)
            except Exception as exc:
                logger.debug(
                    "drop_partition({}) ignored: {}",
                    source_type,
                    exc,
                )
            # 重建空 partition 保持 schema 完整
            await asyncio.to_thread(Partition, collection, source_type)
        except MilvusUnavailable:
            raise
        except Exception as exc:
            raise MilvusUnavailable(
                f"Milvus delete_by_source_type failed: {exc}"
            ) from exc

    # ---------------- 健康检查 ----------------

    async def healthcheck(self) -> dict:
        """轻量级连通性检查，返回 healthy / unhealthy dict。

        不抛异常；不依赖 ``connect()`` 已调用（独立做连接 + DB 校验）。
        """
        settings = self._settings
        if not settings.milvus_credentials_configured:
            return {
                "status": "unhealthy",
                "error_code": "no_credentials",
                "error": "AGENTX_MILVUS_USER/PASSWORD not set",
            }
        if connections is None:  # pragma: no cover
            return {
                "status": "unhealthy",
                "error_code": "unreachable",
                "error": "pymilvus not installed",
            }

        start = time.perf_counter()
        # 1. 连接（独立 alias 避免与 connect() 冲突；复用 _ALIAS 也行，因 connections.connect 幂等）
        # auth disabled 时 user/password 传空字符串
        connect_user = settings.milvus_user or ""
        connect_password = settings.milvus_password or ""
        try:
            await asyncio.to_thread(
                connections.connect,
                alias=_ALIAS,
                host=settings.milvus_host,
                port=str(settings.milvus_port),
                user=connect_user,
                password=connect_password,
                db_name=settings.milvus_db,
            )
        except Exception as exc:
            code = _classify_connect_error(exc)
            if code == "auth_failed":
                return {
                    "status": "unhealthy",
                    "error_code": "auth_failed",
                    "error": str(exc),
                }
            if code == "db_not_found":
                return {
                    "status": "unhealthy",
                    "error_code": "db_not_found",
                    "error": (
                        f"db '{settings.milvus_db}' not found, "
                        "please create via Attu or pymilvus create_database"
                    ),
                }
            return {
                "status": "unhealthy",
                "error_code": "unreachable",
                "error": str(exc),
            }

        # 2. 校验 DB 存在
        try:
            databases = await asyncio.to_thread(utility.list_databases)
        except Exception:
            databases = [settings.milvus_db]
        if settings.milvus_db not in databases:
            return {
                "status": "unhealthy",
                "error_code": "db_not_found",
                "error": (
                    f"db '{settings.milvus_db}' not found, please create via Attu "
                    f"or pymilvus create_database"
                ),
            }

        latency_ms = int((time.perf_counter() - start) * 1000)
        return {
            "status": "healthy",
            "latency_ms": latency_ms,
            "collection": settings.milvus_collection,
        }


# ---------------- 单例 + 模块级委托 ----------------

_client: MilvusClient | None = None


def get_milvus_client() -> MilvusClient:
    """返回缓存的 MilvusClient 单例。"""
    global _client
    if _client is None:
        _client = MilvusClient()
    return _client


async def ingest(
    texts: list[str],
    metadatas: list[dict],
    source_type: str,
) -> list[int]:
    """模块级委托：入库到指定 source_type 分区。"""
    return await get_milvus_client().ingest(texts, metadatas, source_type)


async def search(
    query: str,
    top_k: int = 5,
    filter: str | None = None,
) -> list[tuple[str, str, float]]:
    """模块级委托：向量检索。"""
    return await get_milvus_client().search(query, top_k=top_k, filter=filter)


async def delete_by_source(source: str) -> int:
    """模块级委托：按 source 删除。"""
    return await get_milvus_client().delete_by_source(source)


async def delete_by_source_type(source_type: str) -> None:
    """模块级委托：按 source_type 批量删除（drop + recreate partition）。"""
    return await get_milvus_client().delete_by_source_type(source_type)


# ---- LangChain VectorStore 适配器 ----

from langchain_core.documents import Document  # noqa: E402
from langchain_core.embeddings import Embeddings  # noqa: E402
from langchain_core.vectorstores import VectorStore  # noqa: E402


class LangChainMilvusVectorStore(VectorStore):
    """LangChain ``VectorStore`` 适配器：委托给现有 ``MilvusClient`` 单例。

    实现标准 ``similarity_search`` / ``add_texts`` / ``from_texts``，使
    Milvus 向量库可被 LangChain ``as_retriever()`` / LCEL chain /
    ``create_retrieval_chain`` 直接消费。

    ``embedding`` 参数在 ``from_texts`` 中被忽略——MilvusClient 内部已
    通过 ``app.embedding`` 集成 BGE-M3，不依赖外部 Embeddings 注入。
    同步方法通过专用线程 + 全新事件循环执行协程（方案 C），避免在已有
    事件循环中 ``asyncio.run`` 引发 ``RuntimeError``。
    """

    def __init__(self, embedding: Embeddings | None = None) -> None:
        self._milvus = get_milvus_client()
        # embedding 保留用于 as_retriever() 的元数据，实际嵌入由 MilvusClient 内部处理
        self._embedding = embedding

    @staticmethod
    def _run_coro_sync(coro: Any) -> Any:
        """在独立线程中运行协程，避免在已有事件循环中调用 ``asyncio.run`` 引发 ``RuntimeError``。

        方案 C：同步方法不通过 ``asyncio.run`` 包装异步方法，而是借助专用线程 +
        全新事件循环执行协程，保证 FastAPI async 上下文下可安全调用。
        """
        import threading

        result: list[Any] = []
        error: list[BaseException] = []

        def _runner() -> None:
            new_loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(new_loop)
                result.append(new_loop.run_until_complete(coro))
            except Exception as exc:  # noqa: BLE001
                error.append(exc)
            finally:
                new_loop.close()

        t = threading.Thread(target=_runner)
        t.start()
        t.join()
        if error:
            raise error[0]
        return result[0] if result else None

    def _sync_similarity_search(
        self, query: str, k: int = 4, **kwargs: Any
    ) -> list[Document]:
        """同步向量检索底层实现：在专用线程中执行异步检索，避免 ``asyncio.run`` 冲突。"""
        return self._run_coro_sync(self.asimilarity_search(query, k=k, **kwargs))

    def similarity_search(
        self, query: str, k: int = 4, **kwargs: Any
    ) -> list[Document]:
        """同步向量检索，返回 LangChain ``Document`` 列表。"""
        return self._sync_similarity_search(query, k=k, **kwargs)

    async def asimilarity_search(
        self, query: str, k: int = 4, **kwargs: Any
    ) -> list[Document]:
        """异步向量检索，委托给 ``MilvusClient.search``。"""
        filter_expr = kwargs.get("filter")
        hits = await self._milvus.search(query, top_k=k, filter=filter_expr)
        return [
            Document(page_content=text, metadata={"source": source, "score": score})
            for text, source, score in hits
        ]

    def _sync_add_texts(
        self,
        texts: list[str],
        metadatas: list[dict] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        """同步入库底层实现：在专用线程中执行异步入库，避免 ``asyncio.run`` 冲突。"""
        return self._run_coro_sync(
            self.aadd_texts(texts, metadatas=metadatas, **kwargs)
        )

    def add_texts(
        self,
        texts: list[str],
        metadatas: list[dict] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        """同步入库，返回已插入条目的 ID 列表（字符串化）。"""
        return self._sync_add_texts(texts, metadatas=metadatas, **kwargs)

    async def aadd_texts(
        self,
        texts: list[str],
        metadatas: list[dict] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        """异步入库，委托给 ``MilvusClient.ingest``。"""
        source_type = kwargs.get("source_type", "manual")
        metas = metadatas or [{} for _ in texts]
        ids = await self._milvus.ingest(texts, metas, source_type=source_type)
        return [str(i) for i in ids]

    @classmethod
    def from_texts(
        cls,
        texts: list[str],
        embedding: Embeddings,
        metadatas: list[dict] | None = None,
        **kwargs: Any,
    ) -> "LangChainMilvusVectorStore":
        """从文本列表创建 VectorStore 实例（LangChain 标准工厂方法）。

        ``embedding`` 参数被忽略——MilvusClient 内部已集成 BGE-M3 嵌入。
        """
        vs = cls(embedding=embedding)
        vs.add_texts(texts, metadatas=metadatas, **kwargs)
        return vs
