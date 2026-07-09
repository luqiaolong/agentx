"""双写 trace：本地 SQLite（必写）+ LangSmith remote（凭证存在时）。

``dual_trace`` 是观测中心五维度串联的入口（T3 调用）：
- **本地**：``SqliteObservationSink.start_run`` / ``end_run`` + ``append_event``（必写，不依赖网络）
- **Remote**：``langsmith.trace_span``（凭证存在时写 LangSmith；缺失/失败降级为本地 only）

降级策略（FR-3.6）：
- ``langsmith_api_key`` 缺失 → 仅本地，记 1 条 warning（启动时）
- 网络失败 → 仅本地，记 warning（每次 trace）
- ``langsmith_tracing=False`` → 仅本地，不记 warning（用户显式关闭）

凭证注入：``LANGSMITH_API_KEY`` 由 Rust 主进程从 tauri-plugin-store 读取后注入 env
（env.rs L47-49 已实现），``LANGSMITH_TRACING_V2`` / ``LANGSMITH_PROJECT`` 需在
tauri-plugin-store 补充配置后由 env.rs 注入。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from loguru import logger

from app.config import get_settings
from app.observability.langsmith import _langsmith_available, gen_trace_id, trace_span
from app.observability.observation import get_observation_sink


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
    trace_id = run_id or gen_trace_id()
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
