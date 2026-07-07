"""跨请求审批状态管理。

从 ``main.py`` 提取，解耦 ``deep/agent.py`` 对 ``main`` 模块级私有 dict 的反射访问
（``getattr(main, "_pending_approvals")`` / ``getattr(main, "_abort_flags")``）。

模块级 dict 仍为进程内单例——M2 计划迁移到 Redis 时替换本模块实现即可，
调用方无需变更。
"""

from __future__ import annotations

import asyncio

from app.approval.decision import ApprovalDecision

__all__ = [
    "submit_approval",
    "pop_approval",
    "set_abort",
    "is_aborted",
    "clear_abort",
    "get_abort_event",
]


# thread_id → 审批决策（含 decision/path/writable）。DeepAgent 经 wait_for_approval 消费。
_pending_approvals: dict[str, ApprovalDecision] = {}

# thread_id → 中止标志。SSE handler 每轮迭代检查。
_abort_flags: dict[str, bool] = {}

# thread_id → asyncio.Event，供 LLM 流式内部即时响应中止（无需轮询）。
_abort_events: dict[str, asyncio.Event] = {}


def submit_approval(thread_id: str, decision: ApprovalDecision) -> None:
    """写入审批决策，供 DeepAgent ``wait_for_approval`` 消费。"""
    _pending_approvals[thread_id] = decision


def pop_approval(thread_id: str) -> ApprovalDecision | None:
    """取出并移除审批决策（单次消费）。无决策时返回 None。"""
    return _pending_approvals.pop(thread_id, None)


def set_abort(thread_id: str) -> None:
    """设置中止标志并触发对应 asyncio.Event，供轮询和流式内部即时响应。"""
    _abort_flags[thread_id] = True
    event = _abort_events.get(thread_id)
    if event is None:
        event = asyncio.Event()
        _abort_events[thread_id] = event
    event.set()


def is_aborted(thread_id: str) -> bool:
    """检查是否已设置中止标志。"""
    return _abort_flags.get(thread_id, False)


def clear_abort(thread_id: str) -> None:
    """清除中止标志（消费后清理）。"""
    _abort_flags.pop(thread_id, None)
    event = _abort_events.pop(thread_id, None)
    if event:
        event.set()


def get_abort_event(thread_id: str) -> asyncio.Event:
    """获取或创建 thread_id 对应的中止事件。

    流式生成器内部可通过 ``event.is_set()`` 即时检查中止，避免依赖轮询。
    """
    if thread_id not in _abort_events:
        _abort_events[thread_id] = asyncio.Event()
    return _abort_events[thread_id]
