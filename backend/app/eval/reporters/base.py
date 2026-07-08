"""Reporter 协议与共享辅助函数。

模块职责单一（AGENTS.md P2）：仅定义输出协议与跨 Reporter 复用的纯函数，
不含具体渲染逻辑。渲染逻辑在 console/markdown/json_reporter 各自模块内。
"""

from __future__ import annotations

from typing import Any, Protocol

from app.eval.models import EvalResult


class Reporter(Protocol):
    """评测报告输出协议。"""

    def render(self, result: EvalResult) -> str:
        """渲染报告，返回字符串。"""
        ...


def extract_agent_reply(events: list[dict[str, Any]]) -> str:
    """从 events 中提取所有 ``event == "token"`` 的 data 拼接为完整回复。

    SSE 事件契约（AGENTS.md §13）：token 事件 data 为纯字符串增量。
    非 token 事件（tool_call / done / error 等）的 data 被忽略。
    """
    return "".join(
        str(e.get("data", "")) for e in events if e.get("event") == "token"
    )


def format_duration(ms: int) -> str:
    """格式化毫秒为 ``1.2s``（>=1000ms）或 ``120ms``（<1000ms）。"""
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def count_passed(result: EvalResult) -> int:
    """统计通过的 case 数。"""
    return sum(1 for cr in result.case_results if cr.passed)


def avg_score(result: EvalResult) -> float:
    """计算所有 case 的 avg_score 字段均值；无 case 时返回 0.0。"""
    if not result.case_results:
        return 0.0
    return sum(cr.avg_score for cr in result.case_results) / len(result.case_results)


def l2_avg_score(result: EvalResult) -> float:
    """计算 L2（LLM-as-judge）判分 score 的均值；无 L2 判分时返回 0.0。"""
    l2_scores = [
        jr.score
        for cr in result.case_results
        for jr in cr.judge_results
        if jr.layer == "L2"
    ]
    if not l2_scores:
        return 0.0
    return sum(l2_scores) / len(l2_scores)


def has_l2(result: EvalResult) -> bool:
    """是否含 L2 判分结果。"""
    return any(
        jr.layer == "L2"
        for cr in result.case_results
        for jr in cr.judge_results
    )
