"""审批决策类型（Enum 化设计）。

从 ``app.approval.decision`` 迁移，修复旧 ``ApprovalDecision(approved: bool, decision: str)``
的可矛盾问题（如 ``approved=True, decision="deny"``）。

新设计：
- ``ApprovalDecision``：str Enum，四种互斥决策值（approve / once / session / deny）。
- ``ApprovalResult``：携带决策 + 路径 + writable 的数据类，``approved`` 为 property，
  由 ``decision != DENY`` 派生，消除人为矛盾。

消费者兼容性：
- 旧 ``ApprovalDecision.approved``（字段）→ 新 ``ApprovalResult.approved``（property）。
- 旧 ``ApprovalDecision.decision``（str 字段）→ 新 ``ApprovalResult.decision``
  （``ApprovalDecision`` Enum 实例，``str(Enum)`` 比较需用 ``.value`` 或 ``==``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["ApprovalDecision", "ApprovalResult"]


class ApprovalDecision(str, Enum):
    """审批决策枚举（互斥，消除旧 dataclass 的 approved/decision 矛盾）。

    继承 ``str`` 便于与字符串直接比较（``ApprovalDecision.DENY == "deny"`` 为 True），
    以及 JSON 序列化时输出字符串值。
    """

    APPROVE = "approve"
    ONCE = "once"
    SESSION = "session"
    FULL_TRUST = "full_trust"
    DENY = "deny"


@dataclass
class ApprovalResult:
    """审批结果（携带决策 + 路径 + writable）。

    替代旧 ``ApprovalDecision`` dataclass 的使用场景：
    - ``decision`` 字段为 ``ApprovalDecision`` Enum（不再允许任意字符串）。
    - ``approved`` 为 property，由 ``decision != DENY`` 派生，无法人为制造矛盾。
    - ``path`` / ``writable`` 用于 directory_extension 场景描述目标。
    """

    decision: ApprovalDecision
    path: str | None = None
    writable: bool = False

    @property
    def approved(self) -> bool:
        """是否批准（deny 之外均视为批准）。"""
        return self.decision != ApprovalDecision.DENY
