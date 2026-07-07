"""审批决策数据类。

从 ``utils/security.py`` 迁入，与沙箱授权解耦——审批决策语义上属于审批层，
沙箱仅消费决策结果（如 ``decision="session"`` 触发持久授权）。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ApprovalDecision"]


@dataclass
class ApprovalDecision:
    """审批决策（兼容旧 bool 语义 + 扩展授权场景）。

    - dangerous_tool：approved=True/False，decision="approve"/"deny"
    - directory_extension：decision="once"/"session"/"deny"，path/writable 描述目标
    """

    approved: bool
    decision: str = "approve"  # "approve" | "once" | "session" | "deny"
    path: str | None = None
    writable: bool = False
