"""跨请求审批状态管理。

从 ``main.py`` 提取，解耦 ``deep/agent.py`` 对 ``main`` 模块级私有 dict 的反射访问
（``getattr(main, "_pending_approvals")`` / ``getattr(main, "_abort_flags")``）。

模块级 dict 仍为进程内单例——M2 计划迁移到 Redis 时替换本模块实现即可，
调用方无需变更。
"""

from __future__ import annotations

from app.approval.decision import ApprovalDecision

__all__ = [
    "submit_approval",
    "pop_approval",
    "set_abort",
    "is_aborted",
    "clear_abort",
]


# thread_id → 审批决策（含 decision/path/writable）。DeepAgent 经 wait_for_approval 消费。
_pending_approvals: dict[str, ApprovalDecision] = {}

# thread_id → 中止标志。SSE handler 每轮迭代检查。
_abort_flags: dict[str, bool] = {}


def submit_approval(thread_id: str, decision: ApprovalDecision) -> None:
    """写入审批决策，供 DeepAgent ``wait_for_approval`` 消费。"""
    _pending_approvals[thread_id] = decision


def pop_approval(thread_id: str) -> ApprovalDecision | None:
    """取出并移除审批决策（单次消费）。无决策时返回 None。"""
    return _pending_approvals.pop(thread_id, None)


def set_abort(thread_id: str) -> None:
    """设置中止标志，SSE handler 在下一轮迭代退出。"""
    _abort_flags[thread_id] = True


def is_aborted(thread_id: str) -> bool:
    """检查是否已设置中止标志。"""
    return _abort_flags.get(thread_id, False)


def clear_abort(thread_id: str) -> None:
    """清除中止标志（消费后清理）。"""
    _abort_flags.pop(thread_id, None)
