"""隐式反馈信号采集（FR-9）。

3 种隐式信号（与显式 👍/👎 区分，kind 字段标识）：
- ``implicit_ok``  — auto_approve 倒计时归零 + 工具执行成功
- ``implicit_bad`` — 用户主动 abort（reason=aborted）
- ``implicit_bad`` — 审批 deny（reason=rejected_dangerous_tool）

静默采集，不写入 user 通知 / 不弹窗（FR-9.3）。
"""

from __future__ import annotations

from app.observability.logger import logger
from app.observability.observation import get_observation_sink


async def record_implicit_ok(
    run_id: str, reason: str = "auto_approved+success"
) -> None:
    """隐式好评：auto_approve 倒计时归零 + 工具执行成功。"""
    try:
        sink = get_observation_sink()
        await sink.write_feedback(
            run_id=run_id,
            kind="implicit_ok",
            comment=reason,
        )
    except Exception as exc:  # noqa: BLE001 — 隐式信号失败不阻塞
        logger.warning("record_implicit_ok failed", run_id=run_id, error=str(exc))


async def record_implicit_bad(run_id: str, reason: str) -> None:
    """隐式差评：用户 abort 或审批 deny。"""
    try:
        sink = get_observation_sink()
        await sink.write_feedback(
            run_id=run_id,
            kind="implicit_bad",
            comment=reason,
        )
    except Exception as exc:  # noqa: BLE001 — 隐式信号失败不阻塞
        logger.warning("record_implicit_bad failed", run_id=run_id, error=str(exc))


__all__ = ["record_implicit_ok", "record_implicit_bad"]
