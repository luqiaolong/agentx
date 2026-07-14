"""AgentTeam Aggregator：综合黑板内容生成最终回复。

包含：
- ``_AGGREGATOR_PROMPT``：Aggregator LLM prompt 模板。
- ``_build_summary``：单个子任务输出汇总（截断 + 工具痕迹拼接）。
- ``_quality_gate``：Aggregator 质量门（拒绝全失败 / 全相同 / 全截断的情况）。
- ``_run_aggregator``：调用 Aggregator LLM，流式输出最终回复。
- ``_SIMPLE_TASK_KEYWORDS`` / ``_should_downgrade_to_single``：简单任务降级评估。
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, AsyncIterator, Mapping

from langchain_core.prompts import ChatPromptTemplate

from app.config import get_settings
from app.observability.logger import logger
from app.observability.trace import bind_trace, current_trace_id
from app.sse.events import make_sse_event
from app.utils.text import ThinkFilter, compile_keyword_patterns, extract_chunk_text, matches_any
from app.team.blackboard import _serialize_blackboard
from app.team.state import TeamOutcome

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

__all__ = [
    "_AGGREGATOR_PROMPT",
    "_run_aggregator",
    "_quality_gate",
    "_build_summary",
    "_should_downgrade_to_single",
    "_SIMPLE_TASK_KEYWORDS",
    "_compute_team_outcome",
    "_flatten_findings",
]


# Aggregator prompt：使用 LangChain ChatPromptTemplate 替代手写 f-string
_AGGREGATOR_PROMPT = ChatPromptTemplate.from_messages([
    (
        "human",
        "你是团队汇总专家。以下是一群专家针对用户问题的协作结果。\n\n"
        "用户问题：{user_message}\n\n"
        "专家发现：\n{blackboard_summary}\n\n"
        "失败说明：\n{error_summary}\n\n"
        "请综合以上信息，给出完整、准确的最终回答。"
        "如果专家结果有冲突，请说明并给出判断依据。"
        "保持回答简洁，使用标准 Markdown。",
    ),
])


def _build_summary(text_parts: list[str], tool_traces: list[str], agent_name: str) -> str:
    settings = get_settings()
    max_chars = settings.team_result_max_chars
    full_text = "".join(text_parts).strip()
    if not full_text and not tool_traces:
        return f"[{agent_name}] 未返回有效内容"
    # 若文本较长，取前 max_chars
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "\n[结果已截断]"
    summary = full_text
    if tool_traces:
        traces = "\n".join(tool_traces[:5])
        summary += f"\n\n工具痕迹：\n{traces}"
    return summary.strip()


def _flatten_findings(findings: Mapping) -> list:
    """把 ``dict[str, Finding | list[Finding]]`` 展平为 ``list[Finding]``。

    BE-N 修复后同 key 的 finding 会收集为 list，本函数统一展平，
    供 outcome 判定与 agents 列表构造使用。使用 duck typing（``hasattr``
    检查 ``success`` 属性）识别 Finding 对象，避免反向 import 造成循环依赖。
    """
    flat: list = []
    for val in (findings or {}).values():
        if isinstance(val, list):
            for f in val:
                if hasattr(f, "success"):
                    flat.append(f)
        elif hasattr(val, "success"):
            flat.append(val)
    return flat


def _compute_team_outcome(findings: Mapping, aborted: bool = False) -> TeamOutcome:
    """基于 ``Finding.success`` 判定 Team 最终 outcome（D4 / REQ-TEAM-OUTCOME-2）。

    判定规则：
    - ``aborted=True`` → ``ABORTED``（优先级最高，即使全部失败也返回 ABORTED）
    - 无有效 findings → ``ERROR``
    - 全部 ``Finding.success=True`` → ``SUCCESS``
    - 全部 ``Finding.success=False`` → ``ERROR``
    - 部分成功部分失败 → ``PARTIAL``

    Args:
        findings: ``dict[str, Finding | list[Finding]]``，state.findings 原始值。
        aborted: 是否因用户中止或上游取消而终止。

    Returns:
        ``TeamOutcome`` 枚举值。
    """
    if aborted:
        return TeamOutcome.ABORTED
    all_findings = _flatten_findings(findings)
    if not all_findings:
        return TeamOutcome.ERROR
    success_count = sum(1 for f in all_findings if f.success)
    total = len(all_findings)
    if success_count == total:
        return TeamOutcome.SUCCESS
    if success_count == 0:
        return TeamOutcome.ERROR
    return TeamOutcome.PARTIAL


def _quality_gate(blackboard: Mapping) -> tuple[bool, str]:
    """Aggregator 质量门：检查黑板结果质量。

    Args:
        blackboard: 含 ``findings`` / ``errors`` key 的 Mapping（通常为 TeamState dict）。

    Returns:
        (ok, reason) — ok=False 时 reason 说明拒绝原因
    """
    findings = blackboard.get("findings", {})
    if not findings:
        return False, "无任何成功的子任务结果"
    # M2: 仅当内容确实只剩截断标记时才拒绝（原 len<50 检查因 max_chars≥2000 永远为 False）
    truncated_only = all(
        v.strip() == "[结果已截断]"
        for v in findings.values()
    )
    if truncated_only:
        return False, "所有结果均为截断片段，无有效内容"
    # R5: 所有 findings 完全相同（且非空、非截断标记）→ 拒绝
    # 截断标记场景已由上方 truncated_only 分支处理，此处 identical 值不会是 "[结果已截断]"。
    values = [v.strip() for v in findings.values()]
    if len(values) >= 2 and values[0] and all(v == values[0] for v in values):
        logger.info("quality gate rejected: all findings identical")
        return False, "all_findings_identical"
    return True, ""


async def _run_aggregator(
    user_message: str,
    blackboard: Mapping,
    chat_model: BaseChatModel | None = None,
    abort_event: Any = None,
) -> AsyncIterator[dict[str, str]]:
    """调用 Aggregator LLM，流式输出最终回复。

    Args:
        blackboard: 含 ``findings`` / ``errors`` key 的 Mapping（通常为 TeamState dict）。
        chat_model: 可选注入的 ChatModel。非 None 时直接使用（评测框架注入 MockChatModel）；
            None 时调用 ``app.llm.get_chat_model()`` 获取真实 LLM。
        abort_event: 可选 ``asyncio.Event``，在流式输出过程中检查中止信号，
            已中止则提前返回部分结果（H3 修复）。
    """
    # 直接从 app.llm 获取 get_chat_model（v2：不再通过 orchestrator 模块属性访问）
    from app.llm import get_chat_model

    settings = get_settings()

    # trace_id 透传：_run_aggregator 通常由 LangGraph 节点（_aggregate_node）调用，
    # 节点用 asyncio.create_task 调度，ContextVar 不会自动跨协程传播。
    # 显式绑定让 aggregator 的 logger / make_sse_event 也能拿到 trace_id。
    _trace_id = current_trace_id() or ""
    _trace_cm = bind_trace(_trace_id) if _trace_id else contextlib.nullcontext()
    with _trace_cm:
        # 质量门检查
        ok, reason = _quality_gate(blackboard)
        if not ok:
            logger.warning("team aggregator quality gate rejected", reason=reason)
            yield make_sse_event(
                "error",
                {"message": f"专家结果质量不足: {reason}"},
            )
            return

        try:
            llm = chat_model if chat_model is not None else get_chat_model(
                temperature=settings.llm_temperature_aggregator, streaming=True
            )
        except ValueError as exc:
            yield make_sse_event("error", {"message": f"LLM 不可用: {exc}"})
            return

        errors = blackboard.get("errors", {})
        prompt = _AGGREGATOR_PROMPT.invoke({
            "user_message": user_message,
            "blackboard_summary": _serialize_blackboard(blackboard),
            "error_summary": "\n".join(f"{k}: {v}" for k, v in errors.items()) or "无",
        })

        think_filter = ThinkFilter(max_hold=settings.think_filter_max_hold, retain_think=True)
        token_count = 0
        reasoning_count = 0
        total_token_chars = 0
        try:
            async for chunk in llm.astream(prompt):
                # H3: 流式输出过程中检查 abort_event，已中止则提前返回部分结果
                if abort_event is not None and abort_event.is_set():
                    logger.info("team aggregator aborted mid-stream", token_events=token_count)
                    return
                raw = extract_chunk_text(chunk, strip=False)
                cleaned = think_filter.feed(raw)
                if getattr(think_filter, "_retain_think", False):
                    reasoning = think_filter.take_think()
                    if reasoning:
                        reasoning_count += 1
                        yield make_sse_event("reasoning", {"content": reasoning, "source": "team"})
                if cleaned:
                    token_count += 1
                    total_token_chars += len(cleaned)
                    yield make_sse_event("token", cleaned)
            tail = think_filter.flush()
            if tail:
                token_count += 1
                total_token_chars += len(tail)
                yield make_sse_event("token", tail)
            logger.info(
                "team aggregator stream completed",
                token_events=token_count,
                reasoning_events=reasoning_count,
                total_token_chars=total_token_chars,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("team aggregator stream failed", error=str(exc))
            yield make_sse_event("error", {"message": f"Aggregator 流式失败: {exc}"})


_SIMPLE_TASK_KEYWORDS = frozenset({
    # 仅匹配强语义：问候 / 致谢 / 简单命令式翻译
    # 注意：避免 "解释"/"什么是"/"总结"/"列出" 等普通动词，
    # 否则复杂任务（"请帮我分析并解释..." / "列出 3 个步骤并总结"）会被误降级
    "你好", "hello", "hi", "谢谢", "翻译一下",
})

# 匹配模式：中文 keywords 用子串匹配；英文 keywords 用单词边界匹配
# 避免 "hi" 子串命中 "this"/"think" 等英文词。
# D3: 统一使用 app.utils.text.compile_keyword_patterns，与 planner 共享逻辑。
_KEYWORD_PATTERNS = compile_keyword_patterns(list(_SIMPLE_TASK_KEYWORDS))


def _should_downgrade_to_single(message: str) -> tuple[bool, str]:
    """评估是否应降级到单 agent 路径。

    短消息阈值按字符类型自适应：
    - 含 ASCII（英文/数字）：< 12 字符视为短（避免英文 10 字符被吞掉）
    - 全中文/全角：< 6 字符视为短（中文 10 字符信息量已饱和）

    关键词匹配：中文字符用子串；英文字符用 ``\\b\\w+\\b`` 单词边界，
    避免 ``"hi" in "this"`` 等子串误命中。
    """
    lower = message.lower().strip()
    has_ascii = any(ord(c) < 128 and c.isalnum() for c in lower)
    threshold = 12 if has_ascii else 6
    if len(lower) < threshold:
        return True, "消息过短，无需 team 协作"
    if matches_any(lower, _KEYWORD_PATTERNS):
        return True, "命中简单任务关键词"
    return False, ""
