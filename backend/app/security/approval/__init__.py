"""安全审批层：决策类型 + 跨请求状态管理 + 审批流辅助函数。

从 ``app.approval`` 迁移并升级（Enum 化 + TTL reaper + 原子原语），
与 ``app.approval`` 平行存在（Phase 5 统一迁移 import 后可删除旧包）。

审批流辅助函数（路径提取 / 审批事件构造 / 审批等待 / 目录越界扩展授权）
原位于 ``app.security.approval_flow``，现合并到 ``app.security.approval.flow``。

注意：``flow`` 模块不在 ``__init__`` 中 re-export，因为 ``flow.py`` 依赖
``app.config`` / ``app.sandbox`` / ``app.sse.events`` 等重型模块，若经
``__init__`` 导入会在 ``app.config`` 加载阶段触发循环依赖
（``app.config`` → ``app.security`` → ``approval.__init__`` → ``flow`` → ``app.config``）。
调用方应直接 ``from app.security.approval.flow import X``。
"""

from app.security.approval.decision import ApprovalDecision, ApprovalResult
from app.security.approval.request import ApprovalRequest
from app.security.approval.state import (
    clear_abort,
    clear_pause,
    consume_approval,
    get_abort_event,
    get_active_approval_for_thread,
    get_pause_event,
    has_pending_approval,
    is_aborted,
    is_paused,
    peek_approval,
    pop_approval,
    register_approval_request,
    set_abort,
    set_pause,
    start_reaper,
    submit_approval,
    wait_for_abort,
    wait_for_resume,
    write_approval_decision,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalResult",
    "ApprovalRequest",
    "submit_approval",
    "write_approval_decision",
    "pop_approval",
    "register_approval_request",
    "consume_approval",
    "get_active_approval_for_thread",
    "has_pending_approval",
    "peek_approval",
    "set_abort",
    "is_aborted",
    "clear_abort",
    "wait_for_abort",
    "get_abort_event",
    "set_pause",
    "clear_pause",
    "is_paused",
    "get_pause_event",
    "wait_for_resume",
    "start_reaper",
]
