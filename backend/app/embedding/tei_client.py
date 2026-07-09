"""BGE-M3 嵌入服务客户端实现。

通过 HTTP 调用 myserver 上的 BGE-M3 服务（默认 ``http://192.168.1.4:8093/v1/embeddings``），
模型 ``bge-m3``，输出 1024 维向量。详见 spec embedding-service。
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
from tenacity import (
    AsyncRetrying,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.observability.langsmith import mark_redacted, trace_span
from app.observability.logger import logger


class EmbeddingUnavailable(RuntimeError):
    """BGE-M3 嵌入服务不可用（连接失败 / 超时重试耗尽 / 错误状态码）。"""


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
    """BGE-M3 HTTP 客户端。持有单个 ``httpx.AsyncClient``，由 ``get_embedding_client`` 缓存。"""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = httpx.AsyncClient(timeout=settings.embedding_timeout)

    async def embed_text(self, text: str) -> list[float]:
        """嵌入单段文本，返回 1024 维向量（已解包外层 list）。

        请求体为 ``{"input": ["<text>"], "task": "text-matching", "normalize": true}``，
        响应 ``{"embeddings": [[float, ...]]}`` 解包外层 list。
        """
        settings = get_settings()
        if len(text) > settings.embedding_max_chars:
            raise TextTooLongError(
                f"文本长度 {len(text)} 超过上限 {settings.embedding_max_chars}，"
                "请通过分块器切短后重试"
            )
        vectors = await self._do_embed(
            {"input": [text], "task": "text-matching", "normalize": True},
            batch_size=1,
        )
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
                {"input": chunk_texts, "task": "text-matching", "normalize": True},
                batch_size=len(chunk_texts),
            )
            for idx, vec in zip(indices, vectors):
                results[idx] = vec
        return results

    async def _do_embed(self, payload: dict, batch_size: int) -> list[list[float]]:
        """发送一次嵌入请求（带重试与 tracing），返回 ``list[list[float]]``。

        ``payload["input"]`` 由调用方决定是单元素列表（单文本）还是多元素列表（批量）。
        响应格式为 ``{"embeddings": [[float, ...], ...]}``，提取 ``embeddings`` 字段。
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
            # BGE-M3 服务返回 {"embeddings": [[float, ...], ...]}
            if isinstance(data, dict) and "embeddings" in data:
                embeddings = data["embeddings"]
                if not isinstance(embeddings, list):
                    raise EmbeddingUnavailable(
                        f"BGE-M3 返回 embeddings 字段非 list: {type(embeddings).__name__}"
                    )
                return embeddings
            # 兼容旧 TEI 格式 [[float, ...], ...]
            if isinstance(data, list):
                return data
            raise EmbeddingUnavailable(
                f"BGE-M3 返回非预期格式: {type(data).__name__}"
            )

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
            raise EmbeddingUnavailable(f"BGE-M3 嵌入服务不可用: {exc}") from exc
        # 理论不可达
        raise EmbeddingUnavailable("BGE-M3 嵌入服务不可用: 未知原因")

    async def aclose(self) -> None:
        """关闭底层 ``httpx.AsyncClient``。（异步入口）"""
        await self._client.aclose()

    def close(self) -> None:
        """关闭底层 ``httpx.AsyncClient``。（同步入口；运行事件循环中请用 ``aclose()``）"""
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
    """BGE-M3 健康检查：嵌入 ``"healthcheck"`` 字符串，成功返回 ``healthy``。"""
    try:
        await embed_text("healthcheck")
        return {"status": "healthy"}
    except EmbeddingUnavailable as exc:
        return {"status": "unhealthy", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — 健康检查需兜底所有异常
        return {"status": "unhealthy", "error": str(exc)}


from langchain_core.embeddings import Embeddings  # noqa: E402 — 适配器在文件末尾


class LangChainTeiEmbeddings(Embeddings):
    """LangChain ``Embeddings`` 适配器：委托给现有 ``TeiClient`` 单例。

    实现标准 ``embed_documents`` / ``embed_query``（同步）与
    ``aembed_documents`` / ``aembed_query``（异步），使 BGE-M3 嵌入服务
    可被所有 LangChain VectorStore / Retriever / LCEL chain 直接消费。

    同步方法直接走同步 ``httpx.Client`` + tenacity 重试（方案 C），避免在
    FastAPI 已有事件循环中 ``asyncio.run`` 触发
    ``RuntimeError: cannot be called from a running event loop``。
    异步方法仍委托 ``TeiClient``（async httpx），无额外线程开销。
    """

    def __init__(self) -> None:
        self._client = get_embedding_client()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """同步批量嵌入。超长文本位替换为零向量（保留顺序对齐）。

        直接走同步 HTTP（``_sync_embed``），不使用 ``asyncio.run``，
        因此可在已有事件循环（如 FastAPI async 上下文）中安全调用。
        """
        results = self._sync_embed(texts)
        settings = get_settings()
        dim = settings.embedding_dim
        return [vec if vec is not None else [0.0] * dim for vec in results]

    def embed_query(self, text: str) -> list[float]:
        """同步单文本嵌入。"""
        return self.embed_documents([text])[0]

    def _sync_embed(self, texts: list[str]) -> list[list[float] | None]:
        """同步批量嵌入（sync ``httpx.Client`` + tenacity 重试）。

        与 ``TeiClient.embed_texts`` 行为对齐：超长文本跳过（对应位置 ``None``），
        其余按 ``embedding_max_batch`` 分片请求。使用同步 HTTP 调用，
        避免在已有事件循环中 ``asyncio.run`` 崩溃。
        """
        settings = get_settings()
        max_chars = settings.embedding_max_chars
        max_batch = settings.embedding_max_batch

        results: list[list[float] | None] = [None] * len(texts)
        valid: list[tuple[int, str]] = []
        for i, text in enumerate(texts):
            if len(text) > max_chars:
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
            vectors = self._sync_post(
                {"input": chunk_texts, "task": "text-matching", "normalize": True},
                batch_size=len(chunk_texts),
            )
            for idx, vec in zip(indices, vectors):
                results[idx] = vec
        return results

    def _sync_post(self, payload: dict, batch_size: int) -> list[list[float]]:
        """同步 POST + tenacity 重试 + tracing，返回 ``list[list[float]]``。

        重试策略与异步 ``_post_with_retry`` 一致：超时 / 网络层错误重试 4 次，
        HTTP 状态码错误不重试。重试耗尽或非重试错误转为 ``EmbeddingUnavailable``。
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
            try:
                for attempt in Retrying(
                    stop=_RETRY_STOP,
                    wait=_RETRY_WAIT,
                    retry=_RETRY_RETRY,
                    reraise=True,
                ):
                    with attempt:
                        with httpx.Client(timeout=settings.embedding_timeout) as client:
                            response = client.post(
                                settings.embedding_url,
                                json=payload,
                                timeout=settings.embedding_timeout,
                            )
                            response.raise_for_status()
                            data = response.json()
                            if isinstance(data, dict) and "embeddings" in data:
                                embeddings = data["embeddings"]
                                if not isinstance(embeddings, list):
                                    raise EmbeddingUnavailable(
                                        f"BGE-M3 返回 embeddings 字段非 list: "
                                        f"{type(embeddings).__name__}"
                                    )
                                return embeddings
                            if isinstance(data, list):
                                return data
                            raise EmbeddingUnavailable(
                                f"BGE-M3 返回非预期格式: {type(data).__name__}"
                            )
            except httpx.HTTPError as exc:
                raise EmbeddingUnavailable(f"BGE-M3 嵌入服务不可用: {exc}") from exc
            raise EmbeddingUnavailable("BGE-M3 嵌入服务不可用: 未知原因")

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """异步批量嵌入。``None`` 跳过位替换为零向量（保留顺序对齐）。"""
        results = await self._client.embed_texts(texts)
        settings = get_settings()
        dim = settings.embedding_dim
        return [vec if vec is not None else [0.0] * dim for vec in results]

    async def aembed_query(self, text: str) -> list[float]:
        """异步单文本嵌入。"""
        return await self._client.embed_text(text)


__all__ = [
    "EmbeddingUnavailable",
    "TextTooLongError",
    "TeiClient",
    "LangChainTeiEmbeddings",
    "embed_text",
    "embed_texts",
    "get_embedding_client",
    "healthcheck",
]
