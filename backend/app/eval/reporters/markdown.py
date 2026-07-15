"""MarkdownReporter：生成 Markdown 格式评测报告。

结构：标题 + 元信息 + 汇总表（含 L2 平均分，若有）+ Case 详情表 + 失败 case 详情。
"""

from __future__ import annotations

import json

from app.eval.models import CaseResult, EvalResult
from app.eval.reporters.base import (
    avg_score,
    count_passed,
    format_duration,
    has_l2,
    l2_avg_score,
)


def _escape_md_cell(text: str) -> str:
    """转义 Markdown 表格单元格中的特殊字符。

    - ``|`` → ``\\|``（避免破坏表格列分隔）
    - 换行符 → 空格（避免破坏表格行结构）
    """
    if not text:
        return ""
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", "")


def _reasons(cr: CaseResult) -> str:
    """合并所有判分原因，分号分隔；无判分时回退 error，再回退 '-'。"""
    reasons = [jr.reason for jr in cr.judge_results if jr.reason]
    if reasons:
        return "; ".join(reasons)
    return cr.error or "-"


class MarkdownReporter:
    """Markdown 报告：标题 + 元信息 + 汇总表 + Case 详情表 + 失败 case 展开。"""

    def render(self, result: EvalResult) -> str:
        lines: list[str] = []
        lines.append(f"# 评测报告: {result.suite_id}")
        lines.append("")
        lines.append(
            f"- **开始时间**: {result.started_at.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        lines.append(f"- **总耗时**: {format_duration(result.duration_ms)}")
        lines.append("")

        passed = count_passed(result)
        total = len(result.case_results)

        # 汇总表
        lines.append("## 汇总")
        lines.append("")
        if has_l2(result):
            lines.append("| 通过 / 总数 | 平均分 | L2 平均分 |")
            lines.append("|---|---|---|")
            lines.append(
                f"| {passed} / {total} | {avg_score(result):.2f} | "
                f"{l2_avg_score(result):.2f} |"
            )
        else:
            lines.append("| 通过 / 总数 | 平均分 |")
            lines.append("|---|---|")
            lines.append(f"| {passed} / {total} | {avg_score(result):.2f} |")
        lines.append("")

        # Case 详情表
        lines.append("## Case 详情")
        lines.append("")
        lines.append("| Case ID | Passed | Score | Duration | Error | Judge Reasons |")
        lines.append("|---|---|---|---|---|---|")
        for cr in result.case_results:
            mark = "✓" if cr.passed else "✗"
            err = _escape_md_cell(cr.error or "-")
            reasons = _escape_md_cell(_reasons(cr))
            lines.append(
                f"| {_escape_md_cell(cr.case.id)} | {mark} | {cr.avg_score:.1f} | "
                f"{format_duration(cr.duration_ms)} | {err} | {reasons} |"
            )
        lines.append("")

        # 失败 case 详情：展开 JudgeResult.details
        failed = [cr for cr in result.case_results if not cr.passed]
        if failed:
            lines.append("## 失败 Case 详情")
            lines.append("")
            for cr in failed:
                lines.append(f"### {cr.case.id}")
                lines.append("")
                if cr.error:
                    lines.append(f"- **错误**: {cr.error}")
                for jr in cr.judge_results:
                    passed_str = "是" if jr.passed else "否"
                    lines.append(
                        f"- **{jr.layer}**: score={jr.score:.1f} "
                        f"passed={passed_str} reason={jr.reason}"
                    )
                    if jr.details:
                        for key, value in jr.details.items():
                            # 用 JSON 渲染复杂值（list/dict），避免 Python repr 污染 Markdown
                            if isinstance(value, (dict, list)):
                                value_str = json.dumps(value, ensure_ascii=False)
                            else:
                                value_str = str(value)
                            lines.append(f"  - {key}: {value_str}")
                lines.append("")

        return "\n".join(lines)
