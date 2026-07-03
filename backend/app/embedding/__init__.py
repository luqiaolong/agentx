"""TEI 嵌入服务客户端。

通过 myserver HuggingFace TEI（``http://192.168.1.4:8080/embed``）调用 bge-m3，
替代本地 sentence-transformers。详见 design D1 与 spec embedding-service。
"""

from __future__ import annotations

from .tei_client import (
    EmbeddingUnavailable,
    TextTooLongError,
    embed_text,
    embed_texts,
    get_embedding_client,
)

__all__ = [
    "EmbeddingUnavailable",
    "TextTooLongError",
    "embed_text",
    "embed_texts",
    "get_embedding_client",
]
