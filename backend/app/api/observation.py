"""观测中心 REST API：5 个 REST 端点 + 2 个 Claude CLI 分析 SSE 端点。

- ``POST /api/observation/feedback``          — 写 feedback（含 redact）
- ``GET  /api/observation/feedback``           — 查 feedback（?run_id=)
- ``GET  /api/observation/runs``               — 列 run（?thread_id=&limit=）
- ``GET  /api/observation/runs/{run_id}``      — 单 run 详情
- ``GET  /api/observation/runs/{run_id}/events`` — 事件流
- ``POST /api/observation/review``             — 复盘：Claude CLI 分析执行轨迹+相关代码，输出问题与优化方案
- ``POST /api/observation/apply-optimization`` — 执行优化：用户确认后 Claude CLI 执行优化方案

复盘流程：
1. 用户点击「复盘」→ 后端调 ``claude -p --permission-mode plan``（只读分析）
2. Claude CLI 自主 Read/Grep/Glob 浏览代码 → 输出问题诊断 + 优化方案
3. 用户阅读复盘报告，点击「执行优化」→ 后端调 ``claude -p --permission-mode acceptEdits``
4. Claude CLI 执行代码修改 → 流式输出修改过程
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.config.settings import PROJECT_ROOT
from app.eval.claude_cli_runner import (
    ClaudeCliRunner,
    claude_cli_available,
)
from app.observability.logger import logger
from app.observability.observation import get_observation_sink
from app.sse.events import make_error_event


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


class ReviewRequest(BaseModel):
    """POST /api/observation/review 请求体：复盘指定 run 的执行轨迹。"""

    run_id: str = Field(..., description="待复盘的 run_id（=trace_id）")
    thread_id: str | None = Field(default=None, description="会话 ID（仅用于日志）")


class ApplyOptimizationRequest(BaseModel):
    """POST /api/observation/apply-optimization 请求体。

    用户确认复盘报告中的优化方案后，前端发起此请求。后端用 Claude CLI
    ``acceptEdits`` 模式执行优化方案，Claude 会直接修改项目代码。
    """

    run_id: str = Field(..., description="关联的 trace run_id（用于日志+持久化）")
    optimization_plan: str = Field(
        ...,
        description="复盘报告中产出的优化方案全文（Claude CLI 据此执行修改）",
    )
    thread_id: str | None = Field(default=None, description="会话 ID（仅用于日志）")


# ============================================================
# 轨迹摘要（供 Claude CLI prompt 使用）
# ============================================================

# 单事件 payload 截断（避免超长 tool_result 撑爆 prompt）
_EVENT_PAYLOAD_LIMIT = 800
# trace 摘要总长度上限
_TRACE_SUMMARY_LIMIT = 12000


def _build_trace_summary(
    run: dict[str, Any] | None, events: list[dict[str, Any]]
) -> str:
    """把 observation_run + events 压缩为可读文本（供 Claude CLI 分析）。

    - run：user_message / agent_mode / duration / error 等元信息
    - events：按 seq 顺序列出 event_type + 关键 payload（截断）
    - token_rollback：遇到 rollback 时，从摘要中删除之前 live token 事件行
    - result_text：从 live token 事件重建最终输出，标注「最终输出」段落
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
        if len(final_text) > 2000:
            final_text = final_text[:2000] + "…(截断)"
        lines.append("## 最终输出")
        lines.append(final_text)
        lines.append("")

    lines.append(f"## 事件流（共 {len(events)} 条）")
    total = 0
    for ev in events:
        et = ev.get("event_type", "")
        seq = ev.get("seq", 0)
        if et == "token_rollback":
            continue
        if et == "token" and seq in rolled_back_seqs:
            continue
        payload_str = ev.get("payload_json", "")
        if len(payload_str) > _EVENT_PAYLOAD_LIMIT:
            payload_str = payload_str[:_EVENT_PAYLOAD_LIMIT] + "…(截断)"
        line = f"[{seq}] {et}: {payload_str}"
        lines.append(line)
        total += len(line)
        if total > _TRACE_SUMMARY_LIMIT:
            lines.append("…(后续事件截断，已达摘要上限)")
            break
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


def _build_apply_optimization_prompt(optimization_plan: str) -> str:
    """执行优化 prompt：让 Claude CLI 按用户确认的方案修改代码。

    Claude CLI 在 ``acceptEdits`` 模式下可直接使用 Edit 工具修改代码，
    修改过程通过 thinking + text 流式输出。
    """
    return (
        "# 任务\n"
        "用户已确认以下优化方案，请立即执行代码修改。\n\n"
        "# 执行要求\n"
        "1. 严格按照优化方案逐条执行，不要自行增加或跳过方案外的修改\n"
        "2. 每个修改前先 Read 确认当前文件内容，再用 Edit 修改\n"
        "3. 修改过程中输出你的思考过程与修改说明\n"
        "4. 所有修改完成后，输出总结：修改了哪些文件、每个文件改了什么\n"
        "5. 如果某个修改无法执行（如文件不存在、方案有误），输出原因并跳过\n\n"
        "# 用户确认的优化方案\n\n"
        f"{optimization_plan}"
    )


_CLI_UNAVAILABLE_MSG = (
    "claude CLI 未安装或不在 PATH 中。"
    "请安装 Claude Code CLI：npm install -g @anthropic-ai/claude-code，"
    "并运行 claude 完成认证。"
)


def _cli_unavailable_stream(trace_id: str) -> AsyncIterator[dict[str, str]]:
    """CLI 不可用时的错误 SSE 流。"""

    async def _stream() -> AsyncIterator[dict[str, str]]:
        yield make_error_event(_CLI_UNAVAILABLE_MSG, trace_id=trace_id)
        yield {"event": "done", "data": "{}"}

    return _stream()


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

    @app.post("/api/observation/review")
    async def review_trace(req: ReviewRequest) -> EventSourceResponse:
        """复盘：Claude CLI 分析执行轨迹+相关代码，输出问题与优化方案。

        SSE 事件：reasoning（Claude 思考过程）→ token（复盘报告正文）→ done。
        Claude CLI 在 plan 模式下可自主 Read/Grep/Glob 浏览代码。
        """

        if not claude_cli_available():
            return EventSourceResponse(_cli_unavailable_stream(req.run_id))

        sink = get_observation_sink()
        run = await _async(sink.get_run_sync, req.run_id)
        events = await _async(sink.list_events_sync, req.run_id)

        if run is None and not events:
            return EventSourceResponse(_empty_error_stream(req.run_id))

        trace_summary = _build_trace_summary(run, events)
        logger.info(
            "trace review started (claude cli)",
            run_id=req.run_id,
            thread_id=req.thread_id,
            events_count=len(events),
            summary_len=len(trace_summary),
        )

        prompt = _build_review_prompt(trace_summary)
        runner = ClaudeCliRunner(
            model="sonnet",
            max_turns=25,
            permission_mode="plan",  # 只读分析
            working_dir=str(PROJECT_ROOT),
            timeout=600.0,
        )

        async def _review_stream() -> AsyncIterator[dict[str, str]]:
            async for event in runner.run_stream(
                prompt, req.run_id, source="review"
            ):
                yield event

        return EventSourceResponse(_review_stream())

    @app.post("/api/observation/apply-optimization")
    async def apply_optimization(req: ApplyOptimizationRequest) -> EventSourceResponse:
        """执行优化：用户确认后 Claude CLI 执行优化方案。

        Claude CLI 在 acceptEdits 模式下可直接使用 Edit 工具修改代码。
        SSE 事件：reasoning（修改思考）→ token（修改说明）→ done。
        """

        if not claude_cli_available():
            return EventSourceResponse(_cli_unavailable_stream(req.run_id))

        if not req.optimization_plan.strip():
            return EventSourceResponse(_empty_error_stream(req.run_id, "优化方案为空"))

        logger.info(
            "apply optimization started (claude cli)",
            run_id=req.run_id,
            thread_id=req.thread_id,
            plan_len=len(req.optimization_plan),
        )

        prompt = _build_apply_optimization_prompt(req.optimization_plan)
        runner = ClaudeCliRunner(
            model="sonnet",
            max_turns=40,
            permission_mode="acceptEdits",  # 允许修改代码
            working_dir=str(PROJECT_ROOT),
            timeout=900.0,  # 改代码比分析更慢，放宽超时
        )

        async def _apply_stream() -> AsyncIterator[dict[str, str]]:
            async for event in runner.run_stream(
                prompt, req.run_id, source="apply-optimization"
            ):
                yield event

        return EventSourceResponse(_apply_stream())


def _empty_error_stream(
    run_id: str, msg: str | None = None
) -> AsyncIterator[dict[str, str]]:
    """构造空错误 SSE 流（run 不存在或参数非法时用）。"""
    error_msg = msg or f"未找到 run_id={run_id} 的轨迹数据"

    async def _stream() -> AsyncIterator[dict[str, str]]:
        yield make_error_event(error_msg, trace_id=run_id)
        yield {"event": "done", "data": "{}"}

    return _stream()


async def _async(fn: Any, *args: Any) -> Any:
    """把 sync 读方法包装为 async（via asyncio.to_thread）。"""
    import asyncio

    return await asyncio.to_thread(fn, *args)
