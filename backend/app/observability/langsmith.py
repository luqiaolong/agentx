"""LangSmith 追踪 + 凭证 redaction + 双写 trace。

提供 ``trace_span`` 上下文管理器与 ``redact`` 过滤器。在 trace 发送前对 metadata
做字段名 redaction，过滤 ``*_PASSWORD`` / ``*_KEY`` / ``*_SECRET`` 字段（spec 要求）。

T2.1: ``trace_span`` 内部切换为 ``langsmith.trace`` 真实调用（凭证存在时），
凭证缺失时降级为 ``logger.debug`` 占位（向后兼容）。

``dual_trace`` 是观测中心五维度串联的入口（原 ``langsmith_dual`` 模块合并而来）：
- **本地**：``SqliteObservationSink.start_run`` / ``end_run`` + ``append_event``（必写，不依赖网络）
- **Remote**：``trace_span``（凭证存在时写 LangSmith；缺失/失败降级为本地 only）

降级策略（FR-3.6）：
- ``langsmith_api_key`` 缺失 → 仅本地，记 1 条 warning（启动时）
- 网络失败 → 仅本地，记 warning（每次 trace）
- ``langsmith_tracing=False`` → 仅本地，不记 warning（用户显式关闭）
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from loguru import logger

from app.config import get_settings
from app.observability.trace import new_trace_id

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


# ============================================================
# 双写 trace：本地 SQLite（必写）+ LangSmith remote（凭证存在时）
# 原 langsmith_dual 模块合并至此（P1 命名重构）
# ============================================================

# 延迟导入 get_observation_sink 以打破与 observation.py 的循环依赖
#（observation.py 顶部 from app.observability.langsmith import redact，
# 若本文件顶部导入 observation 会形成循环；此处 redact 已定义，安全导入）
from app.observability.observation import get_observation_sink  # noqa: E402


@dataclass
class DualTraceContext:
    """``dual_trace`` 产出的上下文，供 T3 五维度串联使用。"""

    run_id: str
    trace_id: str
    thread_id: str
    agent_mode: str
    start_ts: float
    remote_enabled: bool
    _metadata: dict[str, Any] = field(default_factory=dict)

    def add_metadata(self, key: str, value: Any) -> None:
        """运行中追加 metadata（如 prompt 拼接完成后的 final_prompt）。"""
        self._metadata[key] = value


@contextmanager
def dual_trace(
    *,
    thread_id: str,
    agent_mode: str,
    user_message: str,
    permission_mode: str | None = None,
    workspace_path: str | None = None,
    run_id: str | None = None,
) -> Iterator[DualTraceContext]:
    """双写 trace 入口：本地 SQLite 必写 + LangSmith remote（凭证存在时）。

    用法（T3 示例）::

        with dual_trace(thread_id=tid, agent_mode="chat",
                        user_message=msg) as ctx:
            await sink.record_prompt(ctx.run_id, ...)
            async for event in stream:
                await sink.append_event(ctx.run_id, seq, ...)
            # 退出时自动 end_run

    Args:
        thread_id: 会话 ID
        agent_mode: chat / single_tool / deep_task
        user_message: 用户原始输入
        permission_mode: 权限模式
        workspace_path: 工作区路径
        run_id: 可选，外部传入的 run_id（默认自动生成 16 字符 hex）

    Yields:
        DualTraceContext
    """
    trace_id = run_id or new_trace_id()
    # run_id = trace_id（单一锚点，FR-1.4）
    run_id = trace_id
    start_ts = time.perf_counter()
    settings = get_settings()
    remote_enabled = _langsmith_available()

    if not remote_enabled and settings.langsmith_tracing:
        # tracing 开 on 但凭证缺失 → 记 warning（FR-3.6）
        logger.warning(
            "langsmith_tracing enabled but API_KEY missing, "
            "degrade to local-only observation"
        )

    # 1. 本地 SQLite：start_run（必写）
    sink = get_observation_sink()
    try:
        sink.start_run_sync(
            run_id=run_id,
            trace_id=trace_id,
            thread_id=thread_id,
            agent_mode=agent_mode,
            permission_mode=permission_mode,
            user_message=user_message,
            workspace_path=workspace_path,
        )
    except Exception as exc:  # noqa: BLE001 — 本地写失败不阻塞 agent
        logger.warning("observation start_run failed: {}", exc)

    ctx = DualTraceContext(
        run_id=run_id,
        trace_id=trace_id,
        thread_id=thread_id,
        agent_mode=agent_mode,
        start_ts=start_ts,
        remote_enabled=remote_enabled,
    )

    # 2. Remote LangSmith trace（凭证存在时）— enter/exit 与 yield 解耦，
    #    remote 失败不阻止 yield（降级为本地 only）
    remote_cm = None
    if remote_enabled:
        try:
            remote_cm = trace_span(
                f"agentx:{agent_mode}",
                run_id=run_id,
                thread_id=thread_id,
                agent_mode=agent_mode,
                user_message=user_message,
            )
            remote_cm.__enter__()
        except Exception as exc:  # noqa: BLE001 — remote enter 失败降级
            logger.warning("langsmith remote trace failed, local data preserved: {}", exc)
            remote_cm = None

    try:
        yield ctx
    finally:
        if remote_cm is not None:
            try:
                remote_cm.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 — remote exit 失败不影响本地
                logger.warning("langsmith remote trace exit failed: {}", exc)
        _finalize_run(ctx, sink)


def _finalize_run(ctx: DualTraceContext, sink: Any) -> None:
    """退出 dual_trace 时写 end_run（duration_ms / error 信息由调用方通过 ctx 传入）。"""
    duration_ms = int((time.perf_counter() - ctx.start_ts) * 1000)
    try:
        sink.end_run_sync(
            run_id=ctx.run_id,
            duration_ms=duration_ms,
            result_text=ctx._metadata.get("result_text"),
            result_token_count=ctx._metadata.get("result_token_count"),
            error_type=ctx._metadata.get("error_type"),
            error_message=ctx._metadata.get("error_message"),
        )
    except Exception as exc:  # noqa: BLE001 — end_run 失败不阻塞
        logger.warning("observation end_run failed: {}", exc)
