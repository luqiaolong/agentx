"""观测中心 REST API（FR-7）：5 个端点 + 2 个分析 SSE 端点。

- ``POST /api/observation/feedback``          — 写 feedback（含 redact）
- ``GET  /api/observation/feedback``           — 查 feedback（?run_id=)
- ``GET  /api/observation/runs``               — 列 run（?thread_id=&limit=）
- ``GET  /api/observation/runs/{run_id}``      — 单 run 详情
- ``GET  /api/observation/runs/{run_id}/events`` — 事件流
- ``POST /api/observation/review``             — 复盘指定 run 的执行轨迹（SSE 流式）
- ``POST /api/observation/self-evolve``        — 自进化：结合 agentx 代码分析轨迹问题（SSE 流式）
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.observability.logger import logger
from app.observability.observation import get_observation_sink
from app.sse.events import make_error_event


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


class ReviewRequest(BaseModel):
    """POST /api/observation/review 请求体：复盘指定 run 的执行轨迹。"""

    run_id: str = Field(..., description="待复盘的 run_id（=trace_id）")
    thread_id: str | None = Field(default=None, description="会话 ID（仅用于日志）")


class SelfEvolveRequest(BaseModel):
    """POST /api/observation/self-evolve 请求体：结合 agentx 代码分析轨迹问题。"""

    run_id: str = Field(..., description="待分析的 run_id（=trace_id）")
    thread_id: str | None = Field(default=None, description="会话 ID（仅用于日志）")


# ============================================================
# 轨迹摘要 + 项目代码概览（供复盘/自进化 prompt 使用）
# ============================================================

# 单事件 payload 截断（避免超长 tool_result 撑爆 prompt）
_EVENT_PAYLOAD_LIMIT = 800
# trace 摘要总长度上限
_TRACE_SUMMARY_LIMIT = 12000
# 项目代码概览总长度上限
_PROJECT_SUMMARY_LIMIT = 12000
# 单文件内容截断
_FILE_CONTENT_LIMIT = 1500


def _build_trace_summary(
    run: dict[str, Any] | None, events: list[dict[str, Any]]
) -> str:
    """把 observation_run + events 压缩为可读文本（供 LLM 复盘）。

    - run：user_message / agent_mode / duration / error 等元信息
    - events：按 seq 顺序列出 event_type + 关键 payload（截断）
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
        result = run.get("result_text")
        if result:
            lines.append(f"- result_text: {result[:500]}")
        lines.append("")

    lines.append(f"## 事件流（共 {len(events)} 条）")
    total = 0
    for ev in events:
        et = ev.get("event_type", "")
        seq = ev.get("seq", "")
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


def _collect_project_summary() -> str:
    """收集 agentx 项目核心代码概览（供自进化分析）。

    扫描 backend/app 关键模块的目录树 + 读取少量核心文件内容（截断），
    让 LLM 基于真实代码结构分析问题。控制在 _PROJECT_SUMMARY_LIMIT 内。
    """
    from pathlib import Path

    # 项目根：backend/app 向上两级
    app_dir = Path(__file__).resolve().parent.parent  # backend/app
    project_root = app_dir.parent.parent  # agentx 项目根

    lines: list[str] = []
    lines.append("# AgentX 项目代码概览（自进化分析输入）")

    # 1. backend/app 目录树（2 层）
    lines.append("\n## backend/app 目录结构")
    for sub in sorted(app_dir.iterdir()):
        if sub.name.startswith("__"):
            continue
        if sub.is_dir():
            lines.append(f"- {sub.name}/")
            try:
                for f in sorted(sub.iterdir())[:15]:
                    if f.name.startswith("__"):
                        continue
                    lines.append(f"  - {f.name}")
            except PermissionError:
                pass
        else:
            lines.append(f"- {sub.name}")

    # 2. 读取核心文件内容（截断）
    core_files = [
        "router/graph.py",
        "deepagent/agent.py",
        "llm.py",
        "config.py",
        "observability/observation.py",
    ]
    lines.append("\n## 核心文件内容摘要")
    total = 0
    for rel in core_files:
        fpath = app_dir / rel
        if not fpath.exists():
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # 取前 N 行 + 总行数
        file_lines = content.splitlines()
        head = "\n".join(file_lines[:_FILE_CONTENT_LIMIT // 4])
        snippet = head[:_FILE_CONTENT_LIMIT]
        block = f"\n### {rel}（共 {len(file_lines)} 行，摘要）\n```python\n{snippet}\n```\n"
        if total + len(block) > _PROJECT_SUMMARY_LIMIT:
            break
        lines.append(block)
        total += len(block)

    # 3. AGENTS.md 架构段（前 3000 字符）
    agents_md = project_root / "AGENTS.md"
    if agents_md.exists():
        try:
            md = agents_md.read_text(encoding="utf-8", errors="ignore")[:3000]
            lines.append(f"\n## AGENTS.md（前 3000 字符）\n{md}")
        except OSError:
            pass

    return "\n".join(lines)


async def _stream_llm_analysis(
    prompt: str, trace_id: str
) -> AsyncIterator[dict[str, str]]:
    """调 LLM 流式输出分析结果，yield SSE 事件（reasoning → token* → done）。

    事件契约复用前端 useChatStream：
    - reasoning: 一次性说明分析方向（独立 part）
    - token: LLM 正文增量
    - done: 流结束
    - error: 异常（含 message）
    """
    import json

    # 延迟导入：避免 top-level 依赖 + 测试可 monkeypatch
    from app.llm import get_chat_model
    from langchain_core.messages import HumanMessage

    try:
        model = get_chat_model(temperature=0.3, streaming=True)
        # 先发一个 reasoning 事件，前端渲染为 ReasoningBlock
        yield {
            "event": "reasoning",
            "data": json.dumps(
                {"content": "正在分析执行轨迹…", "source": "review", "trace_id": trace_id},
                ensure_ascii=False,
            ),
        }
        async for chunk in model.astream([HumanMessage(content=prompt)]):
            text = chunk.content
            if isinstance(text, str) and text:
                yield {"event": "token", "data": text}
        yield {"event": "done", "data": "{}"}
    except Exception as exc:  # noqa: BLE001 — SSE 必须兜底
        logger.warning("trace analysis LLM stream failed", error=str(exc), trace_id=trace_id)
        yield make_error_event(str(exc), trace_id=trace_id)
        yield {"event": "done", "data": "{}"}


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

    @app.post("/api/observation/review")
    async def review_trace(req: ReviewRequest) -> EventSourceResponse:
        """复盘指定 run 的执行轨迹：读 trace events → LLM 流式复盘。

        SSE 事件：reasoning（分析方向）→ token*（复盘正文）→ done。
        前端在当前会话追加一条 assistant 消息流式接收。
        """
        sink = get_observation_sink()
        run = await _async(sink.get_run_sync, req.run_id)
        events = await _async(sink.list_events_sync, req.run_id)

        if run is None and not events:
            # 无轨迹数据：直接返回错误流
            async def _empty() -> AsyncIterator[dict[str, str]]:
                yield make_error_event(
                    f"未找到 run_id={req.run_id} 的轨迹数据", trace_id=req.run_id
                )
                yield {"event": "done", "data": "{}"}

            return EventSourceResponse(_empty())

        trace_summary = _build_trace_summary(run, events)
        prompt = (
            "你是一名资深 AI Agent 复盘专家。请对以下执行轨迹进行复盘，"
            "输出复盘过程与结果。\n\n"
            "## 复盘要求\n"
            "1. **用户意图**：用户原本想达成什么\n"
            "2. **执行路径**：agent 实际做了哪些操作（工具调用、推理步骤）\n"
            "3. **结果评估**：是否达成目标，效果如何\n"
            "4. **问题诊断**：执行中有哪些问题（工具选错、推理偏差、效率低、冗余调用等）\n"
            "5. **改进建议**：针对每个问题给出可执行的改进方向\n\n"
            "用 Markdown 输出，条理清晰。\n\n"
            f"## 执行轨迹数据\n\n{trace_summary}"
        )
        logger.info(
            "trace review started",
            run_id=req.run_id,
            thread_id=req.thread_id,
            events_count=len(events),
        )
        return EventSourceResponse(_stream_llm_analysis(prompt, req.run_id))

    @app.post("/api/observation/self-evolve")
    async def self_evolve_trace(req: SelfEvolveRequest) -> EventSourceResponse:
        """自进化：结合 agentx 项目代码分析执行轨迹暴露的问题与优化点。

        读 trace events + 扫描 agentx 核心代码 → LLM 流式分析。
        SSE 事件：reasoning → token* → done。
        """
        sink = get_observation_sink()
        run = await _async(sink.get_run_sync, req.run_id)
        events = await _async(sink.list_events_sync, req.run_id)

        if run is None and not events:
            async def _empty() -> AsyncIterator[dict[str, str]]:
                yield make_error_event(
                    f"未找到 run_id={req.run_id} 的轨迹数据", trace_id=req.run_id
                )
                yield {"event": "done", "data": "{}"}

            return EventSourceResponse(_empty())

        trace_summary = _build_trace_summary(run, events)
        project_summary = await _async(_collect_project_summary)
        prompt = (
            "你是 AgentX 项目的架构师，负责系统的自我进化。请结合以下执行轨迹"
            "与 AgentX 项目代码，分析系统存在的问题与优化方向。\n\n"
            "## 分析要求\n"
            "1. **轨迹问题**：这次执行暴露了哪些具体问题（路由决策、工具选择、"
            "上下文管理、错误处理、性能等）\n"
            "2. **代码问题**：结合 AgentX 代码，指出导致这些问题的代码层面原因"
            "（具体到模块/文件）\n"
            "3. **优化建议**：给出可落地的优化方案，包括：\n"
            "   - 短期修复（快速止血）\n"
            "   - 中期改进（架构优化）\n"
            "   - 长期演进（方向性建议）\n"
            "4. **优先级排序**：按影响×成本给出实施优先级\n\n"
            "用 Markdown 输出，具体且可执行，避免空泛。\n\n"
            f"## 执行轨迹数据\n\n{trace_summary}\n\n"
            f"## AgentX 项目代码概览\n\n{project_summary}"
        )
        logger.info(
            "trace self-evolve started",
            run_id=req.run_id,
            thread_id=req.thread_id,
            events_count=len(events),
        )
        return EventSourceResponse(_stream_llm_analysis(prompt, req.run_id))


async def _async(fn: Any, *args: Any) -> Any:
    """把 sync 读方法包装为 async（via asyncio.to_thread）。"""
    import asyncio

    return await asyncio.to_thread(fn, *args)
