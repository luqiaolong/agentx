"""Milvus 向量库客户端。

通过 pymilvus 经典 API 连接 myserver Milvus（``192.168.1.4:19530``），
替代本地 Chroma。详见 design D2 与 spec vector-store。
"""

from __future__ import annotations

from .milvus_client import (
    MilvusUnavailable,
    delete_by_source,
    delete_by_source_type,
    get_milvus_client,
    ingest,
    search,
)

__all__ = [
    "MilvusUnavailable",
    "delete_by_source",
    "delete_by_source_type",
    "get_milvus_client",
    "ingest",
    "search",
]
