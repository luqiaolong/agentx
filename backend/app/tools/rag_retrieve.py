"""RAG 检索工具：经 TEI 嵌入 + Milvus 检索，返回带来源的上下文字符串。

设计要点：
- **显式降级**：嵌入服务或向量库不可用时，**不抛异常**，返回明确的中文错误字符串，
  由 DeepAgent 在 state.errors 中记录并决定是否继续。这与 filesystem 工具的契约一致。
- 单次调用 = 1 次 ``embed_text(query)`` + 1 次 Milvus ``search``，无重试（向量库侧
  已有降级）。
- trace 元数据中不包含查询原文（``mark_redacted``）。
- ``filter`` 透传到 Milvus（如 ``source_type == "manual"``），由调用方负责表达式安全。
"""

from __future__ import annotations

from app.embedding import EmbeddingUnavailable
from app.observability.langsmith import mark_redacted, trace_span
from app.observability.logger import logger
from app.vectorstore import MilvusUnavailable, search

# 降级提示：服务不可用时返回给 DeepAgent 的字符串
_EMBEDDING_DOWN = "嵌入服务不可用，无法检索知识库"
_MILVUS_DOWN = "向量库不可用，无法检索知识库"


async def rag_retrieve(
    query: str,
    thread_id: str = "",
    top_k: int = 5,
    filter: str | None = None,
) -> str:
    """向量检索知识库，返回带来源的上下文字符串。

    Args:
        query: 检索查询文本。
        thread_id: 会话 ID（用于 trace，不影响检索范围）。
        top_k: 返回条数上限。
        filter: Milvus filter 表达式（如 ``source_type == "manual"``）。

    Returns:
        - 成功：格式化的上下文字符串，含 source 与 score。
        - 嵌入服务不可用：``_EMBEDDING_DOWN``。
        - 向量库不可用：``_MILVUS_DOWN``。
    """
    with trace_span(
        "tool.rag_retrieve",
        query=mark_redacted(),
        top_k=top_k,
        filter=filter,
        thread_id=thread_id,
    ) as span:
        try:
            hits: list[tuple[str, str, float]] = await search(query, top_k=top_k, filter=filter)
        except EmbeddingUnavailable as exc:
            logger.warning(
                "rag_retrieve embedding unavailable",
                thread_id=thread_id,
                error=str(exc),
            )
            span["metadata"]["result"] = "embedding_unavailable"
            return _EMBEDDING_DOWN
        except MilvusUnavailable as exc:
            logger.warning(
                "rag_retrieve milvus unavailable",
                thread_id=thread_id,
                error=str(exc),
            )
            span["metadata"]["result"] = "milvus_unavailable"
            return _MILVUS_DOWN
        except Exception as exc:  # noqa: BLE001 — 工具层兜底，避免异常穿透到 SSE
            logger.warning(
                "rag_retrieve unexpected error",
                thread_id=thread_id,
                error=str(exc),
            )
            span["metadata"]["result"] = "error"
            return f"知识库检索失败: {exc}"

        span["metadata"]["result_count"] = len(hits)
        span["metadata"]["retrieved_text"] = mark_redacted()
        return _format_hits(hits)


def _format_hits(hits: list[tuple[str, str, float]]) -> str:
    """将检索结果格式化为带来源的上下文字符串。"""
    if not hits:
        return "知识库中未检索到相关内容。"
    lines: list[str] = []
    for i, (text, source, score) in enumerate(hits, start=1):
        score_pct = max(0.0, min(1.0, score)) * 100
        lines.append(f"[{i}] (来源: {source}, 相似度: {score_pct:.1f}%)\n{text}")
    return "\n\n".join(lines)


__all__ = ["rag_retrieve"]
