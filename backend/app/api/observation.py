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

import json
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
# 长轨迹分段：单段最大字符数
_TRACE_CHUNK_LIMIT = 5000


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


def _format_event_line(ev: dict[str, Any]) -> str:
    """把单条 event 格式化为 trace summary 中的一行。"""
    et = ev.get("event_type", "")
    seq = ev.get("seq", "")
    payload_str = ev.get("payload_json", "")
    if len(payload_str) > _EVENT_PAYLOAD_LIMIT:
        payload_str = payload_str[:_EVENT_PAYLOAD_LIMIT] + "…(截断)"
    return f"[{seq}] {et}: {payload_str}\n"


def _chunk_events(events: list[dict[str, Any]], max_chars: int) -> list[list[dict[str, Any]]]:
    """按字符上限把 events 切成若干段，避免一次性塞进超长 prompt。"""
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_len = 0
    for ev in events:
        line = _format_event_line(ev)
        # 单个 event 就超限也允许单独成段，避免死循环
        if current and current_len + len(line) > max_chars:
            chunks.append(current)
            current = [ev]
            current_len = len(line)
        else:
            current.append(ev)
            current_len += len(line)
    if current:
        chunks.append(current)
    return chunks


async def _stream_with_think_parse(
    text_stream: AsyncIterator[str],
    trace_id: str,
) -> AsyncIterator[dict[str, str]]:
    """解析 LLM token 流中的 <think>...</think>，分别输出 reasoning / token 事件。

    - `<think>` 内部内容作为 reasoning 事件输出，前端渲染为 Think/Reasoning 模块。
    - `</think>` 之后的内容作为 token 事件输出，渲染为正式复盘正文。

    鲁棒性优化（2026-07-10）：
    - think 块以「完整闭合」为分界；为让用户在思考中实时看到进度，在 think 期间
      每积累 ``_THINK_FLUSH_CHARS`` 字符就 yield 一次 reasoning（带可拼接后缀）。
    - 完整闭合 ``<think>...</think>`` 后，把 tag 之间的纯内容一次性 yield。
    - 流结束时若仍 in_think=True（LLM 忘了闭合），把全部内容作为 reasoning yield
      （前端仍会按未闭合表现为一个 ReasoningBlock，避免泄露 ``<think>`` 字面量）。
    """
    import re as _re

    buffer = ""
    in_think = False
    think_content = ""
    # 与 text.py THINK_OPEN/THINK_CLOSE 一致（允许可选闭合 '\>'）
    _think_open_re = _re.compile(r"<think>")
    _think_close_re = _re.compile(r"</think>")
    _think_flush_chars = 200  # 思考中每积累 200 字符 yield 一次，让用户实时看到进度

    async def _emit_reasoning(content: str) -> dict[str, str]:
        return {
            "event": "reasoning",
            "data": json.dumps(
                {"content": content.strip(), "source": "review", "trace_id": trace_id},
                ensure_ascii=False,
            ),
        }

    async def _emit_token(text: str) -> dict[str, str]:
        return {"event": "token", "data": text}

    async for text in text_stream:
        buffer += text
        # 多次 while 循环：当前 chunk 可能含多个 <think>/</think> 标签
        while buffer:
            if in_think:
                close_match = _think_close_re.search(buffer)
                if close_match is None:
                    think_content += buffer
                    buffer = ""
                    # 流式反馈：积累超过阈值 yield 一次中间进度（保留 buffer 续接，
                    # 下次 chunk 再补全；同时允许前端实时渲染思考过程）。
                    if len(think_content) >= _think_flush_chars:
                        yield await _emit_reasoning(think_content)
                        think_content = ""
                    break
                think_content += buffer[: close_match.start()]
                yield await _emit_reasoning(think_content)
                in_think = False
                think_content = ""
                buffer = buffer[close_match.end() :]
            else:
                open_match = _think_open_re.search(buffer)
                if open_match is None:
                    if buffer:
                        yield await _emit_token(buffer)
                        buffer = ""
                    break
                if open_match.start() > 0:
                    yield await _emit_token(buffer[: open_match.start()])
                in_think = True
                think_content = ""
                buffer = buffer[open_match.end() :]

    # 流结束：未闭合的 think 也作为 reasoning 输出，剩余 buffer 作为 token 输出
    if in_think:
        if think_content or buffer:
            yield await _emit_reasoning(think_content + buffer)
    else:
        if buffer:
            yield await _emit_token(buffer)


def _collect_project_summary() -> tuple[str, list[str]]:
    """收集 agentx 项目核心代码概览（供自进化分析）。

    扫描 backend/app 关键模块的目录树 + 读取少量核心文件内容（截断），
    让 LLM 基于真实代码结构分析问题。控制在 _PROJECT_SUMMARY_LIMIT 内。

    返回 (summary, files_read)：files_read 为实际成功读取并纳入摘要的核心文件
    相对路径，供调用方在 SSE 流中向用户展示「正在读取代码」的可观测进度。
    """
    from pathlib import Path

    files_read: list[str] = []
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
        files_read.append(rel)

    # 3. AGENTS.md 架构段（前 3000 字符）
    agents_md = project_root / "AGENTS.md"
    if agents_md.exists():
        try:
            md = agents_md.read_text(encoding="utf-8", errors="ignore")[:3000]
            lines.append(f"\n## AGENTS.md（前 3000 字符）\n{md}")
            files_read.append("AGENTS.md")
        except OSError:
            pass

    return "\n".join(lines), files_read


def _extract_chunk_text(chunk: Any) -> str:
    """从 LangChain chunk 中提取文本内容。"""
    text = getattr(chunk, "content", chunk)
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        parts: list[str] = []
        for item in text:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(text) if text is not None else ""


async def _stream_llm_analysis(
    prompt: str, trace_id: str
) -> AsyncIterator[dict[str, str]]:
    """调 LLM 流式输出分析结果，yield SSE 事件（reasoning → token* → done）。

    事件契约复用前端 useChatStream：
    - reasoning: 一次性说明分析方向（独立 part）
    - token: LLM 正文增量
    - done: 流结束
    - error: 异常（含 message）

    额外支持 <think>...</think> 解析：内部内容走 reasoning 事件，前端进入 Think 模块。
    """
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

        async def _text_stream() -> AsyncIterator[str]:
            async for chunk in model.astream([HumanMessage(content=prompt)]):
                text = _extract_chunk_text(chunk)
                if text:
                    yield text

        async for event in _stream_with_think_parse(_text_stream(), trace_id):
            yield event
        yield {"event": "done", "data": "{}"}
    except Exception as exc:  # noqa: BLE001 — SSE 必须兜底
        logger.warning("trace analysis LLM stream failed", error=str(exc), trace_id=trace_id)
        yield make_error_event(str(exc), trace_id=trace_id)
        yield {"event": "done", "data": "{}"}


# ============================================================
# 复盘 / 自进化 prompt 构建
# ============================================================

def _build_review_prompt(trace_summary: str) -> str:
    """短轨迹：一次性复盘的 prompt。"""
    return (
        "你是一名资深 AI Agent 复盘专家。请对以下执行轨迹进行结构化复盘。\n\n"
        "## 输出格式要求\n"
        "1. 先使用 `<think>` 标签输出你的内部推理过程（分析思路、关键观察）。\n"
        "2. 在 `</think>` 之后，使用 Markdown 输出正式复盘报告，包含以下章节：\n"
        "   - 用户意图\n"
        "   - 执行路径\n"
        "   - 结果评估\n"
        "   - 问题诊断\n"
        "   - 改进建议\n"
        "复盘报告应条理清晰、结论明确，不要简单罗列事件。\n\n"
        f"## 执行轨迹数据\n\n{trace_summary}"
    )


def _build_review_segment_prompt(trace_summary: str, idx: int, total: int) -> str:
    """长轨迹分段：单段分析的 prompt。"""
    return (
        f"你正在复盘一段较长的执行轨迹（第 {idx + 1}/{total} 段）。"
        "请仅针对本段内容进行分析，输出简洁要点，供后续汇总成完整复盘报告。\n\n"
        "## 输出要求\n"
        "- 关键操作与观察\n"
        "- 明显问题或低效之处\n"
        "- 改进建议\n"
        "用 Markdown bullet，控制在 400 字以内。\n\n"
        f"## 第 {idx + 1}/{total} 段轨迹数据\n\n{trace_summary}"
    )


def _build_review_synthesis_prompt(segment_reviews: list[str]) -> str:
    """长轨迹分段：汇总各段分析，输出最终复盘报告。"""
    sections = "\n\n".join(
        f"### 第 {i + 1} 段分析\n{review}" for i, review in enumerate(segment_reviews)
    )
    return (
        "你已完成对执行轨迹的分段分析。请综合以下各段分析结果，"
        "输出一份完整、结构化、无冗余的最终复盘报告。\n\n"
        f"## 分段分析摘要\n\n{sections}\n\n"
        "## 输出格式要求\n"
        "1. 先使用 `<think>` 标签输出你的综合推理过程。\n"
        "2. 在 `</think>` 之后，使用 Markdown 输出正式报告，包含：\n"
        "   - 用户意图\n"
        "   - 执行路径\n"
        "   - 结果评估\n"
        "   - 问题诊断\n"
        "   - 改进建议\n"
        "去重并结构化，避免简单拼接各段内容。"
    )


def _build_self_evolve_prompt(trace_summary: str, project_summary: str) -> str:
    """短轨迹：自进化一次性分析的 prompt。"""
    return (
        "你是 AgentX 项目的架构师，负责系统的自我进化。"
        "请结合以下执行轨迹与项目代码，分析系统存在的问题与优化方向。\n\n"
        "## 输出格式要求\n"
        "1. 先使用 `<think>` 标签输出你的内部推理过程。\n"
        "2. 在 `</think>` 之后，使用 Markdown 输出正式报告，包含：\n"
        "   - 轨迹问题\n"
        "   - 代码层面原因（具体到模块/文件）\n"
        "   - 优化建议（短期 / 中期 / 长期）\n"
        "   - 优先级排序\n"
        "具体、可执行，避免空泛。\n\n"
        f"## 执行轨迹数据\n\n{trace_summary}\n\n"
        f"## AgentX 项目代码概览\n\n{project_summary}"
    )


def _build_self_evolve_segment_prompt(trace_summary: str, idx: int, total: int) -> str:
    """长轨迹分段：自进化单段分析的 prompt。"""
    return (
        f"你正在分析 AgentX 执行轨迹的第 {idx + 1}/{total} 段，以找出系统问题与优化方向。\n\n"
        "## 输出要求\n"
        "- 本段暴露的问题（路由、工具、上下文、错误处理、性能等）\n"
        "- 可能涉及的代码模块/文件\n"
        "- 优化建议\n"
        "用 Markdown bullet，控制在 400 字以内。\n\n"
        f"## 第 {idx + 1}/{total} 段轨迹数据\n\n{trace_summary}"
    )


def _build_self_evolve_synthesis_prompt(
    segment_reviews: list[str], project_summary: str
) -> str:
    """长轨迹分段：自进化汇总 prompt。"""
    sections = "\n\n".join(
        f"### 第 {i + 1} 段分析\n{review}" for i, review in enumerate(segment_reviews)
    )
    return (
        "你已完成对执行轨迹的分段分析。请结合 AgentX 项目代码，"
        "综合以下各段分析，输出一份完整、结构化、可执行的自我进化报告。\n\n"
        f"## AgentX 项目代码概览\n\n{project_summary}\n\n"
        f"## 分段分析摘要\n\n{sections}\n\n"
        "## 输出格式要求\n"
        "1. 先使用 `<think>` 标签输出你的综合推理过程。\n"
        "2. 在 `</think>` 之后，使用 Markdown 输出正式报告，包含：\n"
        "   - 轨迹问题\n"
        "   - 代码层面原因（具体到模块/文件）\n"
        "   - 优化建议（短期 / 中期 / 长期）\n"
        "   - 优先级排序\n"
        "具体、可执行，避免空泛，避免简单拼接。"
    )


async def _stream_segmented_analysis(
    run: dict[str, Any] | None,
    events: list[dict[str, Any]],
    trace_id: str,
    *,
    single_prompt_builder: Any | None = None,
    segment_prompt_builder: Any,
    synthesis_prompt_builder: Any,
) -> AsyncIterator[dict[str, str]]:
    """长轨迹 map-reduce 分析：分段 → 汇总 → 流式输出最终结果。

    - 短轨迹（单段）：直接走一次性分析（使用 single_prompt_builder）。
    - 长轨迹（多段）：逐段调用 LLM 收集要点，最后汇总成完整报告并流式输出。
    分段过程中每完成一段会 emit 一个 reasoning 进度事件，让用户感知到"正在分段处理"。
    """
    chunks = _chunk_events(events, _TRACE_CHUNK_LIMIT)

    if len(chunks) <= 1:
        trace_summary = _build_trace_summary(run, events)
        if single_prompt_builder is not None:
            prompt = single_prompt_builder(trace_summary)
        else:
            prompt = segment_prompt_builder(trace_summary, 0, 1)
        async for event in _stream_llm_analysis(prompt, trace_id):
            yield event
        return

    # 多段：先收集各段要点
    segment_reviews: list[str] = []
    for idx, chunk in enumerate(chunks):
        trace_summary = _build_trace_summary(run, chunk)
        prompt = segment_prompt_builder(trace_summary, idx, len(chunks))

        # 进度感知：告知用户当前在分析第几段
        yield {
            "event": "reasoning",
            "data": json.dumps(
                {
                    "content": f"执行轨迹较长，正在分段分析（第 {idx + 1}/{len(chunks)} 段）…",
                    "source": "review",
                    "trace_id": trace_id,
                },
                ensure_ascii=False,
            ),
        }

        segment_text_parts: list[str] = []
        async for event in _stream_llm_analysis(prompt, trace_id):
            if event["event"] == "error":
                yield event
                return
            if event["event"] == "token":
                segment_text_parts.append(event["data"])
            # reasoning / done 不对外输出，避免中间段的 think 内容混入最终报告
        segment_reviews.append("".join(segment_text_parts).strip())

    # 汇总：流式输出最终报告
    synthesis_prompt = synthesis_prompt_builder(segment_reviews)
    async for event in _stream_llm_analysis(synthesis_prompt, trace_id):
        yield event


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
        logger.info(
            "trace review started",
            run_id=req.run_id,
            thread_id=req.thread_id,
            events_count=len(events),
            summary_len=len(trace_summary),
        )

        async def _review_stream() -> AsyncIterator[dict[str, str]]:
            async for event in _stream_segmented_analysis(
                run,
                events,
                req.run_id,
                single_prompt_builder=_build_review_prompt,
                segment_prompt_builder=_build_review_segment_prompt,
                synthesis_prompt_builder=_build_review_synthesis_prompt,
            ):
                yield event

        return EventSourceResponse(_review_stream())

    @app.post("/api/observation/self-evolve")
    async def self_evolve_trace(req: SelfEvolveRequest) -> EventSourceResponse:
        """自进化：结合 agentx 项目代码分析执行轨迹暴露的问题与优化点。

        读 trace events + 扫描 agentx 核心代码 → LLM 流式分析。
        SSE 事件：reasoning（扫描代码进度）→ token* → done。

        代码扫描在 SSE 流内执行，扫描前后发 reasoning 事件，让用户感知到
        「正在读取项目代码 → 已读取 X 个文件 → 开始分析」的完整过程，
        而非连接建立前的静默等待。
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

        async def _evolve_stream() -> AsyncIterator[dict[str, str]]:
            # 1. 扫描项目代码：在 SSE 流内执行，发 reasoning 让用户感知「读代码」步骤。
            #    原先此处在外层（EventSourceResponse 之前）同步执行，连接未建立，
            #    用户只能看到网络 pending，完全感知不到「正在读取代码」。
            yield {
                "event": "reasoning",
                "data": json.dumps(
                    {
                        "content": "正在扫描 AgentX 项目代码结构…",
                        "source": "review",
                        "trace_id": req.run_id,
                    },
                    ensure_ascii=False,
                ),
            }
            project_summary, files_read = await _async(_collect_project_summary)
            files_line = "、".join(files_read) if files_read else "（未读取到核心文件）"
            yield {
                "event": "reasoning",
                "data": json.dumps(
                    {
                        "content": f"已读取项目代码：{files_line}",
                        "source": "review",
                        "trace_id": req.run_id,
                    },
                    ensure_ascii=False,
                ),
            }
            logger.info(
                "trace self-evolve started",
                run_id=req.run_id,
                thread_id=req.thread_id,
                events_count=len(events),
                summary_len=len(trace_summary),
                files_read=files_read,
            )
            async for event in _stream_segmented_analysis(
                run,
                events,
                req.run_id,
                single_prompt_builder=lambda summary: _build_self_evolve_prompt(
                    summary, project_summary
                ),
                segment_prompt_builder=_build_self_evolve_segment_prompt,
                synthesis_prompt_builder=lambda reviews: _build_self_evolve_synthesis_prompt(
                    reviews, project_summary
                ),
            ):
                yield event

        return EventSourceResponse(_evolve_stream())


async def _async(fn: Any, *args: Any) -> Any:
    """把 sync 读方法包装为 async（via asyncio.to_thread）。"""
    import asyncio

    return await asyncio.to_thread(fn, *args)
