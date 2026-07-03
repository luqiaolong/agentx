"""LangSmith 追踪 + 凭证 redaction。

提供 ``trace_span`` 上下文管理器与 ``redact`` 过滤器。在 trace 发送前对 metadata
做字段名 redaction，过滤 ``*_PASSWORD`` / ``*_KEY`` / ``*_SECRET`` 字段（spec 要求）。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from loguru import logger

from app.config import get_settings

# 当前活跃 span 栈（用于嵌套 span 的 parent 关系，简化实现）
_current_span: ContextVar[dict[str, Any] | None] = ContextVar("current_span", default=None)

# 需 redaction 的字段名后缀/子串
_REDACT_SUFFIXES = ("PASSWORD", "KEY", "SECRET", "TOKEN", "CREDENTIAL")


def redact(metadata: dict[str, Any]) -> dict[str, Any]:
    """对 metadata 做字段名 redaction，匹配 *_PASSWORD/*_KEY/*_SECRET/*_TOKEN/*_CREDENTIAL。

    嵌套 dict 递归处理；list/tuple 中元素若是 dict 也递归。
    """
    redacted: dict[str, Any] = {}
    for k, v in metadata.items():
        upper_k = k.upper()
        if any(suf in upper_k for suf in _REDACT_SUFFIXES):
            redacted[k] = "<redacted>"
        elif isinstance(v, dict):
            redacted[k] = redact(v)
        elif isinstance(v, list):
            redacted[k] = [redact(i) if isinstance(i, dict) else i for i in v]
        else:
            redacted[k] = v
    return redacted


@contextmanager
def trace_span(name: str, **metadata: Any) -> Iterator[dict[str, Any]]:
    """记录一个 LangSmith trace span。

    metadata 会在发送前经 ``redact`` 过滤。inputs/outputs 由调用方显式标记 ``<redacted>``。
    LangSmith 未启用时仅记录结构化日志，不发送 trace。
    """
    settings = get_settings()
    start = time.perf_counter()
    parent = _current_span.get()
    span: dict[str, Any] = {
        "name": name,
        "metadata": redact(metadata),
        "parent": parent["name"] if parent else None,
        "start_ts": start,
    }
    token = _current_span.set(span)
    if settings.langsmith_tracing:
        # 真实 LangSmith 集成在 M1 阶段以日志形式占位，M2 接入 langsmith SDK
        logger.debug("trace.span.start", name=name, metadata=span["metadata"])
    try:
        yield span
    finally:
        latency_ms = int((time.perf_counter() - start) * 1000)
        span["latency_ms"] = latency_ms
        if settings.langsmith_tracing:
            logger.debug(
                "trace.span.end", name=name, latency_ms=latency_ms, metadata=span["metadata"]
            )
        _current_span.reset(token)


def mark_redacted() -> str:
    """显式标记某字段为 redacted（用于 inputs/outputs 原文不进 trace）。"""
    return "<redacted>"
