"""观测中心 REST API（FR-7）：5 个端点。

- ``POST /api/observation/feedback``          — 写 feedback（含 redact）
- ``GET  /api/observation/feedback``           — 查 feedback（?run_id=）
- ``GET  /api/observation/runs``               — 列 run（?thread_id=&limit=）
- ``GET  /api/observation/runs/{run_id}``      — 单 run 详情
- ``GET  /api/observation/runs/{run_id}/events`` — 事件流
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from app.observability.logger import logger
from app.observability.observation import get_observation_sink


class FeedbackRequest(BaseModel):
    """FR-7.1: POST /api/observation/feedback 请求体。"""

    run_id: str = Field(..., description="观测中心 run_id（=trace_id）")
    kind: str = Field(
        ...,
        description="反馈类型：thumb_up / thumb_down / rating / note",
    )
    score: float | None = Field(default=None, description="1-5（rating 类型）")
    comment: str | None = Field(default=None, description="用户评论（写入前 redact）")
    categories: list[str] | None = Field(
        default=None,
        description="分类标签：fact_error / tone / speed / wrong_tool / other",
    )


def register_observation_routes(app: FastAPI) -> None:
    """注册观测中心 5 个 REST 端点。"""

    @app.post("/api/observation/feedback")
    async def post_feedback(req: FeedbackRequest) -> dict[str, Any]:
        """FR-7.1: 写 1 行 observation_feedback（comment 自动 redact）。"""
        sink = get_observation_sink()
        feedback_id = await sink.write_feedback(
            run_id=req.run_id,
            kind=req.kind,
            score=req.score,
            comment=req.comment,
            categories=req.categories,
        )
        logger.info(
            "observation feedback written",
            run_id=req.run_id,
            kind=req.kind,
            feedback_id=feedback_id,
        )
        return {"ok": True, "feedback_id": feedback_id}

    @app.get("/api/observation/feedback")
    async def get_feedback(
        run_id: str = Query(..., description="run_id"),
    ) -> dict[str, Any]:
        """FR-7.2: 查指定 run 的 feedback 列表。"""
        sink = get_observation_sink()
        rows = await _async(sink.list_feedback_sync, run_id)
        return {"ok": True, "feedback": rows}

    @app.get("/api/observation/runs")
    async def list_runs(
        thread_id: str = Query(..., description="会话 ID"),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        """FR-7.3: 列 run（按 started_at 降序）。"""
        sink = get_observation_sink()
        rows = await _async(sink.list_runs_sync, thread_id, limit)
        return {"ok": True, "runs": rows}

    @app.get("/api/observation/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        """FR-7.4: 单 run 详情（含 state_snapshots_json）。"""
        sink = get_observation_sink()
        row = await _async(sink.get_run_sync, run_id)
        if row is None:
            return {"ok": False, "error": "run not found"}
        return {"ok": True, "run": row}

    @app.get("/api/observation/runs/{run_id}/events")
    async def get_events(run_id: str) -> dict[str, Any]:
        """FR-7.5: 按 seq 升序返回事件流。"""
        sink = get_observation_sink()
        rows = await _async(sink.list_events_sync, run_id)
        return {"ok": True, "events": rows}


async def _async(fn: Any, *args: Any) -> Any:
    """把 sync 读方法包装为 async（via asyncio.to_thread）。"""
    import asyncio

    return await asyncio.to_thread(fn, *args)
