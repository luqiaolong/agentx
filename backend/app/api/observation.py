"""观测中心 REST API：6 个 REST 端点。

- ``POST /api/observation/feedback``          — 写 feedback（含 redact）
- ``GET  /api/observation/feedback``           — 查 feedback（?run_id=)
- ``GET  /api/observation/runs``               — 列 run（?thread_id=&limit=）
- ``GET  /api/observation/runs/{run_id}``      — 单 run 详情
- ``GET  /api/observation/runs/{run_id}/events`` — 事件流
- ``POST /api/observation/export-trace/{run_id}`` — 导出 trace 摘要 + 复盘 prompt 到文件
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from app.config.settings import PROJECT_ROOT, DATA_DIR
from app.observability.logger import logger
from app.observability.observation import get_observation_sink


class FeedbackRequest(BaseModel):
    """POST /api/observation/feedback 请求体。"""

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


# ============================================================
# 轨迹摘要（供复盘 prompt 使用）
# ============================================================

# 单事件 payload 截断（避免超长 tool_result 撑爆 prompt）
_EVENT_PAYLOAD_LIMIT = 800
# 合并后的 reasoning / token 块截断
_MERGED_BLOCK_LIMIT = 2000
# trace 摘要总长度上限（Claude CLI sonnet 200k token 上下文，可容纳 ~200KB 文本）
_TRACE_SUMMARY_LIMIT = 200000
# 最终输出截断
_FINAL_TEXT_LIMIT = 4000


def _build_trace_summary(
    run: dict[str, Any] | None, events: list[dict[str, Any]]
) -> str:
    """把 observation_run + events 压缩为可读文本（供 Claude CLI 分析）。

    - run：user_message / agent_mode / duration / error 等元信息
    - events：按 seq 顺序列出 event_type + 关键 payload（截断）
    - token_rollback：遇到 rollback 时，从摘要中删除之前 live token 事件行
    - result_text：从 live token 事件重建最终输出，标注「最终输出」段落
    - 连续 reasoning_delta / token 碎片合并为单块，大幅压缩冗余
    """
    lines: list[str] = []
    if run:
        lines.append("## Run 元信息")
        lines.append(f"- run_id: {run.get('run_id', '')}")
        lines.append(f"- agent_mode: {run.get('agent_mode', '')}")
        lines.append(f"- user_message: {run.get('user_message', '')[:500]}")
        if run.get("duration_ms"):
            lines.append(f"- duration_ms: {run['duration_ms']}")
        if run.get("error_type"):
            lines.append(f"- error_type: {run['error_type']}")
        if run.get("error_message"):
            lines.append(f"- error_message: {run['error_message'][:500]}")
        lines.append("")

    # 预处理：标记被 token_rollback 撤回的 live token 事件 seq
    rolled_back_seqs: set[int] = set()
    live_token_seqs: list[int] = []
    for ev in events:
        et = ev.get("event_type", "")
        seq = ev.get("seq", 0)
        if et == "token_rollback":
            while live_token_seqs:
                rolled_back_seqs.add(live_token_seqs.pop())
        elif et == "token":
            try:
                payload = json.loads(ev.get("payload_json", "{}"))
                if payload.get("live", False):
                    live_token_seqs.append(seq)
            except (json.JSONDecodeError, TypeError):
                pass

    # 重建最终输出文本
    final_text_parts: list[str] = []
    for ev in events:
        et = ev.get("event_type", "")
        seq = ev.get("seq", 0)
        if et == "token":
            try:
                payload = json.loads(ev.get("payload_json", "{}"))
                if seq in rolled_back_seqs:
                    continue
                final_text_parts.append(payload.get("content", ""))
            except (json.JSONDecodeError, TypeError):
                pass
    final_text = "".join(final_text_parts).strip()
    if final_text:
        if len(final_text) > _FINAL_TEXT_LIMIT:
            final_text = final_text[:_FINAL_TEXT_LIMIT] + "…(截断)"
        lines.append("## 最终输出")
        lines.append(final_text)
        lines.append("")

    # ---- 事件流：合并连续同类碎片事件 ----
    lines.append(f"## 事件流（共 {len(events)} 条）")

    # 过滤掉 rollback 和已撤回的 token 事件，保留其余事件
    filtered: list[dict[str, Any]] = []
    for ev in events:
        et = ev.get("event_type", "")
        seq = ev.get("seq", 0)
        if et == "token_rollback":
            continue
        if et == "token" and seq in rolled_back_seqs:
            continue
        filtered.append(ev)

    total = 0
    i = 0
    while i < len(filtered):
        ev = filtered[i]
        et = ev.get("event_type", "")
        seq = ev.get("seq", 0)

        # 合并连续 reasoning_delta
        if et == "reasoning_delta":
            merged: list[str] = []
            seq_start = seq
            seq_end = seq
            while i < len(filtered) and filtered[i].get("event_type") == "reasoning_delta":
                try:
                    p = json.loads(filtered[i].get("payload_json", "{}"))
                    merged.append(p.get("delta", ""))
                except (json.JSONDecodeError, TypeError):
                    pass
                seq_end = filtered[i].get("seq", seq_end)
                i += 1
            text = "".join(merged)
            if len(text) > _MERGED_BLOCK_LIMIT:
                text = text[:_MERGED_BLOCK_LIMIT] + "…(截断)"
            label = f"[{seq_start}-{seq_end}] reasoning_delta(合并{seq_end - seq_start + 1}条)"
            line = f"{label}: {text}"
            lines.append(line)
            total += len(line)
            if total > _TRACE_SUMMARY_LIMIT:
                lines.append("…(后续事件截断，已达摘要上限)")
                break
            continue

        # 合并连续 token（非 live 的增量 token 碎片）
        if et == "token":
            merged = []
            seq_start = seq
            seq_end = seq
            while i < len(filtered) and filtered[i].get("event_type") == "token":
                try:
                    p = json.loads(filtered[i].get("payload_json", "{}"))
                    merged.append(p.get("content", ""))
                except (json.JSONDecodeError, TypeError):
                    pass
                seq_end = filtered[i].get("seq", seq_end)
                i += 1
            text = "".join(merged)
            if len(text) > _MERGED_BLOCK_LIMIT:
                text = text[:_MERGED_BLOCK_LIMIT] + "…(截断)"
            label = f"[{seq_start}-{seq_end}] token(合并{seq_end - seq_start + 1}条)"
            line = f"{label}: {text}"
            lines.append(line)
            total += len(line)
            if total > _TRACE_SUMMARY_LIMIT:
                lines.append("…(后续事件截断，已达摘要上限)")
                break
            continue

        # 其他事件类型：单独输出，payload 截断
        payload_str = ev.get("payload_json", "")
        if len(payload_str) > _EVENT_PAYLOAD_LIMIT:
            payload_str = payload_str[:_EVENT_PAYLOAD_LIMIT] + "…(截断)"
        line = f"[{seq}] {et}: {payload_str}"
        lines.append(line)
        total += len(line)
        if total > _TRACE_SUMMARY_LIMIT:
            lines.append("…(后续事件截断，已达摘要上限)")
            break
        i += 1

    return "\n".join(lines)


def _build_review_prompt(trace_summary: str) -> str:
    """复盘 prompt：让 Claude CLI 分析执行轨迹+相关代码，输出问题与优化方案。

    Claude CLI 在 ``plan`` 模式下可自主使用 Read/Grep/Glob 工具浏览项目代码，
    无需后端手写 read_file 工具或预生成目录树。
    """
    return (
        "# 任务\n"
        "你是 AgentX 项目的资深复盘专家。请对以下执行轨迹进行结构化复盘，"
        "并结合项目代码分析问题根因与优化方向。\n\n"
        "# 可用工具\n"
        "你可以使用 Read / Grep / Glob 工具浏览项目代码辅助分析：\n"
        "- 项目根目录为当前工作目录\n"
        "- 核心代码在 backend/app/ 和 frontend/renderer/ 下\n"
        "- 可自主决定读取哪些文件，无需请求许可\n\n"
        "# 输出格式\n"
        "请输出 Markdown 报告，包含以下章节：\n\n"
        "## 用户意图\n"
        "（分析用户真正想做什么，而非字面表达）\n\n"
        "## 执行路径\n"
        "（按时间顺序梳理 agent 做了什么，关键决策点在哪）\n\n"
        "## 问题诊断\n"
        "（具体问题列表，每条包含：问题描述 / 影响程度 / 代码层面根因——"
        "指明具体文件:行号或函数名）\n\n"
        "## 优化方案\n"
        "（可执行的优化建议，每条包含：方案描述 / 目标文件 / 预期效果 / 优先级。\n"
        "方案需具体到「修改哪个文件的哪个函数、改成什么样」，避免空泛建议。\n"
        "用户确认后将直接据此执行代码修改。）\n\n"
        "## 优先级排序\n"
        "（按收益/成本比排序，标注短期/中期/长期）\n\n"
        "# 执行轨迹数据\n\n"
        f"{trace_summary}"
    )


def register_observation_routes(app: FastAPI) -> None:
    """注册观测中心 REST 端点。"""

    @app.post("/api/observation/feedback")
    async def post_feedback(req: FeedbackRequest) -> dict[str, Any]:
        """写 1 行 observation_feedback（comment 自动 redact）。"""
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
        """查指定 run 的 feedback 列表。"""
        sink = get_observation_sink()
        rows = await _async(sink.list_feedback_sync, run_id)
        return {"ok": True, "feedback": rows}

    @app.get("/api/observation/runs")
    async def list_runs(
        thread_id: str = Query(..., description="会话 ID"),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        """列 run（按 started_at 降序）。"""
        sink = get_observation_sink()
        rows = await _async(sink.list_runs_sync, thread_id, limit)
        return {"ok": True, "runs": rows}

    @app.get("/api/observation/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        """单 run 详情（含 state_snapshots_json）。"""
        sink = get_observation_sink()
        row = await _async(sink.get_run_sync, run_id)
        if row is None:
            return {"ok": False, "error": "run not found"}
        return {"ok": True, "run": row}

    @app.get("/api/observation/runs/{run_id}/events")
    async def get_events(run_id: str) -> dict[str, Any]:
        """按 seq 升序返回事件流。"""
        sink = get_observation_sink()
        rows = await _async(sink.list_events_sync, run_id)
        return {"ok": True, "events": rows}

    @app.post("/api/observation/export-trace/{run_id}")
    async def export_trace(run_id: str) -> dict[str, Any]:
        """导出 trace 摘要 + 复盘 prompt 到文件，返回路径。"""
        sink = get_observation_sink()
        run = await _async(sink.get_run_sync, run_id)
        events = await _async(sink.list_events_sync, run_id)
        if run is None and not events:
            return {"ok": False, "error": "未找到轨迹数据"}
        trace_summary = _build_trace_summary(run, events)
        prompt = _build_review_prompt(trace_summary)
        traces_dir = DATA_DIR / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{run_id}_{timestamp}.md"
        filepath = traces_dir / filename
        filepath.write_text(prompt, encoding="utf-8")
        logger.info("trace exported", run_id=run_id, file=str(filepath))
        return {"ok": True, "prompt_file": str(filepath.resolve())}


async def _async(fn: Any, *args: Any) -> Any:
    """把 sync 读方法包装为 async（via asyncio.to_thread）。"""
    import asyncio

    return await asyncio.to_thread(fn, *args)
