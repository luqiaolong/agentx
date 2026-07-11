"""Blocklist policy.

Thin wrapper around ``app.security.command_filter.is_command_blocked`` that
produces a :class:`RiskAssessment` when the command name (or the command
hidden behind a wrapper such as ``cmd /c`` / ``powershell -Command``) is in
``DEFAULT_BLOCKLIST``.
"""

from __future__ import annotations

from typing import Any

from app.security.command_filter import is_command_blocked
from app.security.policies._command_name import extract_command_names
from app.security.risk import RiskAssessment, RiskLevel

__all__ = ["BlocklistPolicy"]


class BlocklistPolicy:
    """Reject commands on ``DEFAULT_BLOCKLIST`` (HIGH risk)."""

    name = "blocklist"

    def assess(self, command: str, ctx: Any) -> list[RiskAssessment]:
        del ctx  # blocklist is context-independent
        names = extract_command_names(command)
        out: list[RiskAssessment] = []
        for name in names:
            if is_command_blocked(name):
                out.append(
                    RiskAssessment(
                        level=RiskLevel.HIGH,
                        policy_name=self.name,
                        rule_id=f"forbidden_cmd:{name.lower()}",
                        message=(
                            f"命令 '{name}' 在黑名单中，禁止执行"
                            "（删除/格式化/提权等极度危险操作）"
                        ),
                        suggestion=(
                            "改用安全替代命令（如 trash-put、PowerShell 的 "
                            "Remove-Item 走审批）"
                        ),
                    )
                )
        return out
