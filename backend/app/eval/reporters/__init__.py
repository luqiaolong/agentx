"""eval Reporter 报告系统：Console / Markdown / JSON 三种输出格式。

模块边界与职责见 design.md §1。Reporter 协议与辅助函数见 base 模块。
"""

from __future__ import annotations

from app.eval.reporters.base import Reporter
from app.eval.reporters.console import ConsoleReporter
from app.eval.reporters.json_reporter import JsonReporter
from app.eval.reporters.markdown import MarkdownReporter

__all__ = [
    "Reporter",
    "ConsoleReporter",
    "MarkdownReporter",
    "JsonReporter",
]
