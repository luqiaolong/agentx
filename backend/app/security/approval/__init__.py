"""安全审批层：决策类型 + 跨请求状态管理。

从 ``app.approval`` 迁移并升级（Enum 化 + TTL reaper + 原子原语），
与 ``app.approval`` 平行存在（Phase 5 统一迁移 import 后可删除旧包）。
"""

from app.security.approval.decision import ApprovalDecision, ApprovalResult
from app.security.approval.state import (
    clear_abort,
    clear_pause,
    is_aborted,
    pop_approval,
    set_abort,
    set_pause,
    start_reaper,
    submit_approval,
    wait_for_abort,
    wait_for_resume,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalResult",
    "submit_approval",
    "pop_approval",
    "set_abort",
    "is_aborted",
    "clear_abort",
    "wait_for_abort",
    "set_pause",
    "clear_pause",
    "wait_for_resume",
    "start_reaper",
]
