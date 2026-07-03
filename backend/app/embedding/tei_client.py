"""TEI (HuggingFace Text Embeddings Inference) 客户端实现。

通过 HTTP 调用 myserver 上的 TEI 服务（默认 ``http://192.168.1.4:8080/embed``），
模型 ``bge-m3``，输出 1024 维向量。详见 spec embedding-service。
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.observability.langsmith import mark_redacted, trace_span
from app.observability.logger import logger


class EmbeddingUnavailable(RuntimeError):
    """TEI 嵌入服务不可用（连接失败 / 超时重试耗尽 / 错误状态码）。"""


class TextTooLongError(ValueError):
    """输入文本超过 ``embedding_max_chars`` 上限，未发送 HTTP 请求。"""


# tenacity 重试策略：初始尝试 + 3 次重试 = 4 次总尝试，退避 0.5s / 1s / 2s。
# 仅对超时与网络层错误重试；HTTP 状态码错误不重试。
_RETRY_STOP = stop_after_attempt(4)
_RETRY_WAIT = wait_exponential(multiplier=0.5, min=0.5, max=2.0)
_RETRY_RETRY = retry_if_exception_type(
    (httpx.TimeoutException, httpx.RequestError)
)


class TeiClient:
    """TEI HTTP 客户端。持有单个 ``httpx.AsyncClient``，由 ``get_embedding_client`` 缓存。"""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = httpx.AsyncClient(timeout=settings.embedding_timeout)

    async def embed_text(self, text: str) -> list[float]:
        """嵌入单段文本，返回 1024 维向量（已解包外层 list）。

        请求体为 ``{"inputs": "<text>"}``（字符串），响应 ``[[float, ...]]`` 解包外层 list。
        """
        settings = get_settings()
        if len(text) > settings.embedding_max_chars:
            raise TextTooLongError(
                f"文本长度 {len(text)} 超过上限 {settings.embedding_max_chars}，"
                "请通过分块器切短后重试"
            )
        vectors = await self._do_embed({"inputs": text}, batch_size=1)
        return vectors[0]

    async def embed_texts(
        self,
        texts: list[str],
        on_skip: Callable[[str, TextTooLongError], Any] | None = None,
    ) -> list[list[float] | None]:
        """批量嵌入。超长文本跳过（结果对应位置为 ``None``），其余按 ``embedding_max_batch`` 分片请求。

        Args:
            texts: 待嵌入文本列表，顺序保留。
            on_skip: 可选回调，遇到超长文本时调用 ``on_skip(text, error)``。
        """
        settings = get_settings()
        max_chars = settings.embedding_max_chars
        max_batch = settings.embedding_max_batch

        results: list[list[float] | None] = [None] * len(texts)
        valid: list[tuple[int, str]] = []
        for i, text in enumerate(texts):
            if len(text) > max_chars:
                err = TextTooLongError(
                    f"文本长度 {len(text)} 超过上限 {max_chars}，请通过分块器切短后重试"
                )
                if on_skip is not None:
                    on_skip(text, err)
                logger.warning(
                    "embedding text too long, skipped",
                    index=i,
                    length=len(text),
                    max_chars=max_chars,
                )
            else:
                valid.append((i, text))

        for start in range(0, len(valid), max_batch):
            chunk = valid[start : start + max_batch]
            indices = [idx for idx, _ in chunk]
            chunk_texts = [t for _, t in chunk]
            vectors = await self._do_embed(
                {"inputs": chunk_texts}, batch_size=len(chunk_texts)
            )
            for idx, vec in zip(indices, vectors):
                results[idx] = vec
        return results

    async def _do_embed(self, payload: dict, batch_size: int) -> list[list[float]]:
        """发送一次嵌入请求（带重试与 tracing），返回 ``list[list[float]]``。

        ``payload["inputs"]`` 由调用方决定是字符串（单文本）还是列表（批量）。
        """
        settings = get_settings()
        with trace_span(
            "embedding.tei.embed",
            model=settings.embedding_model,
            batch_size=batch_size,
            embedding_url=settings.embedding_url,
            inputs=mark_redacted(),
            outputs=mark_redacted(),
        ):
            response = await self._post_with_retry(payload)
            data = response.json()
            if not isinstance(data, list):
                raise EmbeddingUnavailable(
                    f"TEI 返回非预期格式: {type(data).__name__}"
                )
            return data

    async def _post_with_retry(self, payload: dict) -> httpx.Response:
        """带 tenacity 重试的 POST。重试耗尽或非重试错误转为 ``EmbeddingUnavailable``。"""
        settings = get_settings()
        try:
            async for attempt in AsyncRetrying(
                stop=_RETRY_STOP,
                wait=_RETRY_WAIT,
                retry=_RETRY_RETRY,
                reraise=True,
            ):
                with attempt:
                    response = await self._client.post(
                        settings.embedding_url,
                        json=payload,
                        timeout=settings.embedding_timeout,
                    )
                    response.raise_for_status()
                    return response
        except httpx.HTTPError as exc:
            raise EmbeddingUnavailable(f"TEI 嵌入服务不可用: {exc}") from exc
        # 理论不可达
        raise EmbeddingUnavailable("TEI 嵌入服务不可用: 未知原因")

    async def aclose(self) -> None:
        """关闭底层 ``httpx.AsyncClient``。"""
        await self._client.aclose()


# ---- 单例 ----
_client: TeiClient | None = None


def get_embedding_client() -> TeiClient:
    """返回缓存的 ``TeiClient`` 单例。"""
    global _client
    if _client is None:
        _client = TeiClient()
    return _client


# ---- 模块级便捷函数（委托给单例）----
async def embed_text(text: str) -> list[float]:
    """嵌入单段文本（委托给单例客户端）。"""
    return await get_embedding_client().embed_text(text)


async def embed_texts(
    texts: list[str],
    on_skip: Callable[[str, TextTooLongError], Any] | None = None,
) -> list[list[float] | None]:
    """批量嵌入（委托给单例客户端）。"""
    return await get_embedding_client().embed_texts(texts, on_skip=on_skip)


async def healthcheck() -> dict:
    """TEI 健康检查：嵌入 ``"healthcheck"`` 字符串，成功返回 ``healthy``。"""
    try:
        await embed_text("healthcheck")
        return {"status": "healthy"}
    except EmbeddingUnavailable as exc:
        return {"status": "unhealthy", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — 健康检查需兜底所有异常
        return {"status": "unhealthy", "error": str(exc)}


__all__ = [
    "EmbeddingUnavailable",
    "TextTooLongError",
    "TeiClient",
    "embed_text",
    "embed_texts",
    "get_embedding_client",
    "healthcheck",
]
