"""ConsoleReporter：用 rich 输出彩色表格到字符串。

列：Case ID / Passed / Score / Duration / Reason
末尾汇总：通过数/总数 + 平均分。
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.table import Table

from app.eval.models import CaseResult, EvalResult
from app.eval.reporters.base import avg_score, count_passed, format_duration


def _first_reason(cr: CaseResult) -> str:
    """取首个判分原因作为简述；无判分时回退到 error，再回退空串。"""
    if cr.judge_results:
        return cr.judge_results[0].reason
    return cr.error or ""


class ConsoleReporter:
    """控制台表格报告：Passed 用 ✓/✗ 表示，含颜色。

    render 仅返回字符串，不向真实 stdout 写入（file=StringIO 吞掉直接输出，
    由 record 缓冲 + export_text(styles=True) 还原带 ANSI 颜色的文本）。
    """

    def render(self, result: EvalResult) -> str:
        console = Console(
            file=StringIO(), record=True, width=100, force_terminal=True
        )
        table = Table(title=f"评测结果: {result.suite_id}")
        table.add_column("Case ID", style="cyan")
        table.add_column("Passed", style="bold")
        table.add_column("Score")
        table.add_column("Duration")
        table.add_column("Reason")

        for cr in result.case_results:
            mark = "✓" if cr.passed else "✗"
            table.add_row(
                cr.case.id,
                mark,
                f"{cr.avg_score:.1f}",
                format_duration(cr.duration_ms),
                _first_reason(cr),
            )

        console.print(table)
        passed = count_passed(result)
        total = len(result.case_results)
        console.print(f"通过: {passed}/{total}  平均分: {avg_score(result):.2f}")
        return console.export_text(styles=True)
