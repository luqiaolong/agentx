"""AgentTeam Aggregator：综合黑板内容生成最终回复。

包含：
- ``_AGGREGATOR_PROMPT``：Aggregator LLM prompt 模板。
- ``_build_summary``：单个子任务输出汇总（截断 + 工具痕迹拼接）。
- ``_quality_gate``：Aggregator 质量门（拒绝全失败 / 全相同 / 全截断的情况）。
- ``_run_aggregator``：调用 Aggregator LLM，流式输出最终回复。
- ``_SIMPLE_TASK_KEYWORDS`` / ``_should_downgrade_to_single``：简单任务降级评估。
"""

from __future__ import annotations

import re as _re
from typing import TYPE_CHECKING, AsyncIterator

from langchain_core.prompts import ChatPromptTemplate

from app.config import get_settings
from app.observability.logger import logger
from app.sse.events import make_sse_event
from app.utils.text import ThinkFilter, extract_chunk_text
from app.team.blackboard import Blackboard, _serialize_blackboard

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

__all__ = [
    "_AGGREGATOR_PROMPT",
    "_run_aggregator",
    "_quality_gate",
    "_build_summary",
    "_should_downgrade_to_single",
    "_SIMPLE_TASK_KEYWORDS",
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
    max_chars = settings.agent_team_result_max_chars
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


def _quality_gate(blackboard: Blackboard) -> tuple[bool, str]:
    """Aggregator 质量门：检查黑板结果质量。

    Returns:
        (ok, reason) — ok=False 时 reason 说明拒绝原因
    """
    if not blackboard.findings:
        return False, "无任何成功的子任务结果"
    unique_findings = set(blackboard.findings.values())
    if len(unique_findings) == 1 and len(blackboard.findings) > 1:
        return False, "所有子任务返回相同内容，疑似未实际执行"
    truncated_only = all(
        "[结果已截断]" in v and len(v.strip()) < 50
        for v in blackboard.findings.values()
    )
    if truncated_only:
        return False, "所有结果均为截断片段，无有效内容"
    return True, ""


async def _run_aggregator(
    user_message: str,
    blackboard: Blackboard,
    chat_model: BaseChatModel | None = None,
) -> AsyncIterator[dict[str, str]]:
    """调用 Aggregator LLM，流式输出最终回复。

    Args:
        chat_model: 可选注入的 ChatModel。非 None 时直接使用（评测框架注入 MockChatModel）；
            None 时调用 ``orchestrator.get_chat_model()`` 获取真实 LLM。
    """
    # 通过 orchestrator 模块属性访问 get_chat_model，
    # 以便测试通过 monkeypatch app.team.orchestrator.get_chat_model 替换。
    # 延迟 import 避免与 orchestrator.py 顶部的 import 形成循环。
    from app.team import orchestrator

    settings = get_settings()

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
        llm = chat_model if chat_model is not None else orchestrator.get_chat_model(
            temperature=settings.llm_temperature_aggregator, streaming=True
        )
    except ValueError as exc:
        yield make_sse_event("error", {"message": f"LLM 不可用: {exc}"})
        return

    prompt = _AGGREGATOR_PROMPT.invoke({
        "user_message": user_message,
        "blackboard_summary": _serialize_blackboard(blackboard),
        "error_summary": "\n".join(f"{k}: {v}" for k, v in blackboard.errors.items()) or "无",
    })

    think_filter = ThinkFilter(max_hold=settings.think_filter_max_hold, retain_think=True)
    try:
        async for chunk in llm.astream(prompt):
            raw = extract_chunk_text(chunk, strip=False)
            cleaned = think_filter.feed(raw)
            if getattr(think_filter, "_retain_think", False):
                reasoning = think_filter.take_think()
                if reasoning:
                    yield make_sse_event("reasoning", {"content": reasoning, "source": "team"})
            if cleaned:
                yield make_sse_event("token", cleaned)
        tail = think_filter.flush()
        if tail:
            yield make_sse_event("token", tail)
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
_KEYWORD_PATTERNS = tuple(
    _re.compile(rf"\b{_re.escape(kw)}\b") if all(ord(c) < 128 for c in kw)
    else _re.compile(_re.escape(kw))
    for kw in _SIMPLE_TASK_KEYWORDS
)


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
    if any(pat.search(lower) for pat in _KEYWORD_PATTERNS):
        return True, "命中简单任务关键词"
    return False, ""
