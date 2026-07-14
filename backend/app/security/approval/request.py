"""审批请求实体：活跃请求的显式生命周期。

引入 ``ApprovalRequest`` 以区分"活跃请求"和"已提交决定"（D1）：

- ``ApprovalRequest``: 活跃请求，含 ``approval_id``、``thread_id``、``run_id``、
  ``tool_call_id``、``request_kind``、``created_at``、``expires_at``、``consumed_at``。
- ``ApprovalResult``: 用户决定（在 ``decision.py`` 中定义）。

``submit_approval`` 的语义改为"compare-and-consume active request"，
而不是"给线程写一个决定"。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ApprovalRequest"]


@dataclass
class ApprovalRequest:
    """活跃审批请求（REQ-APR-1）。

    每个请求拥有独立 ``approval_id``（UUID4），绑定 ``thread_id`` + ``run_id``。
    ``approval_id`` 是后续 ``submit_approval`` 的主键；
    ``thread_id`` 不得单独作为授权判断依据。

    Attributes:
        approval_id: UUID4 唯一标识。
        thread_id: 会话 ID。
        run_id: 所属 run 的 ID（= trace_id），用于跨 run 隔离。
        request_kind: 请求类型，``"dangerous_tool"`` | ``"directory_extension"``
                       | ``"sandbox_escalation"``。
        tool_call_id: 关联的工具调用 ID（如适用）。
        created_at: 创建时间（monotonic）。
        expires_at: 过期时间（created_at + TTL）。
        consumed_at: 消费时间（monotonic），None 表示未消费。
    """

    approval_id: str
    thread_id: str
    run_id: str
    request_kind: str
    tool_call_id: str | None
    created_at: float
    expires_at: float
    consumed_at: float | None = None
