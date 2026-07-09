"""LangSmith 追踪 + 凭证 redaction。

提供 ``trace_span`` 上下文管理器与 ``redact`` 过滤器。在 trace 发送前对 metadata
做字段名 redaction，过滤 ``*_PASSWORD`` / ``*_KEY`` / ``*_SECRET`` 字段（spec 要求）。

T2.1: ``trace_span`` 内部切换为 ``langsmith.trace`` 真实调用（凭证存在时），
凭证缺失时降级为 ``logger.debug`` 占位（向后兼容）。
"""

from __future__ import annotations

import time
import uuid
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


def _langsmith_available() -> bool:
    """检查 LangSmith 凭证是否配置（决定是否走真实 remote trace）。"""
    settings = get_settings()
    return bool(settings.langsmith_tracing and settings.langsmith_api_key)


@contextmanager
def trace_span(
    name: str,
    *,
    run_id: str | None = None,
    thread_id: str | None = None,
    **metadata: Any,
) -> Iterator[dict[str, Any]]:
    """记录一个 LangSmith trace span。

    metadata 会在发送前经 ``redact`` 过滤。inputs/outputs 由调用方显式标记 ``<redacted>``。

    行为分级：
    - ``langsmith_tracing=True`` 且 ``langsmith_api_key`` 存在 → 调 ``langsmith.trace`` 真实上报
    - 否则 → ``logger.debug`` 占位（不发送 remote）

    ``run_id`` / ``thread_id`` 透传 trace_id 锚点（FR-3.1），用于关联 observation_run。
    """
    settings = get_settings()
    start = time.perf_counter()
    parent = _current_span.get()
    safe_metadata = redact(metadata)
    span: dict[str, Any] = {
        "name": name,
        "metadata": safe_metadata,
        "parent": parent["name"] if parent else None,
        "start_ts": start,
        "run_id": run_id,
        "thread_id": thread_id,
    }
    token = _current_span.set(span)

    remote_run = None
    use_remote = _langsmith_available()
    if use_remote:
        try:
            from langsmith import trace

            remote_run = trace(
                name=name,
                run_type="chain",
                metadata=safe_metadata,
                tags=[f"thread:{thread_id}"] if thread_id else [],
            )
            remote_run.__enter__()
        except Exception as exc:  # noqa: BLE001 — remote 失败降级为本地
            logger.warning("langsmith.trace remote start failed, degrade to local: {}", exc)
            remote_run = None
            use_remote = False

    if not use_remote and settings.langsmith_tracing:
        # 凭证缺失但 tracing 开关 on → logger.debug 占位（受 settings.langsmith_tracing 控制）
        logger.debug("trace.span.start", name=name, metadata=safe_metadata)

    try:
        yield span
    finally:
        latency_ms = int((time.perf_counter() - start) * 1000)
        span["latency_ms"] = latency_ms
        if remote_run is not None:
            try:
                remote_run.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 — remote 关闭失败不影响主流程
                logger.warning("langsmith.trace remote end failed: {}", exc)
        elif not use_remote and settings.langsmith_tracing:
            logger.debug(
                "trace.span.end", name=name, latency_ms=latency_ms, metadata=safe_metadata
            )
        try:
            _current_span.reset(token)
        except ValueError:
            # ContextVar 跨上下文（如子任务在独立事件循环中创建 span，
            # 主上下文关闭生成器时 reset 会报 ValueError），安全忽略
            pass


def mark_redacted() -> str:
    """显式标记某字段为 redacted（用于 inputs/outputs 原文不进 trace）。"""
    return "<redacted>"


def gen_trace_id() -> str:
    """生成 16 字符 hex trace_id（作为 run_id = trace_id 单一锚点）。"""
    return uuid.uuid4().hex[:16]
