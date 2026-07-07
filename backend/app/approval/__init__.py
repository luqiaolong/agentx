"""审批层：决策数据类 + 跨请求状态管理。

解耦 ``deep/agent.py`` 与 ``main.py`` 的反射依赖。
"""

from app.approval.decision import ApprovalDecision
from app.approval.state import (
    clear_abort,
    get_abort_event,
    is_aborted,
    pop_approval,
    set_abort,
    submit_approval,
)

__all__ = [
    "ApprovalDecision",
    "submit_approval",
    "pop_approval",
    "set_abort",
    "is_aborted",
    "clear_abort",
    "get_abort_event",
]
