"""eval 评测框架：数据模型 + Runner + Judge + Reporter + Mock + pytest 插件。

模块边界与职责见 ``design.md`` §1。Phase 8 起导出执行/评分/替身能力，
供 pytest 插件与 CLI 直接 ``from app.eval import EvalRunner`` 使用。
"""

from app.eval.mocks.llm import MockChatModel
from app.eval.models import (
    AgentMode,
    CaseExpect,
    CaseResult,
    EvalCase,
    EvalResult,
    EvalSuite,
    EventAssertion,
    JudgeLayer,
    JudgeResult,
)
from app.eval.runner import EvalRunner

__all__ = [
    "AgentMode",
    "CaseExpect",
    "CaseResult",
    "EvalCase",
    "EvalResult",
    "EvalSuite",
    "EventAssertion",
    "JudgeLayer",
    "JudgeResult",
    "MockChatModel",
    "EvalRunner",
]
