"""Git write policy.

Detects ``git commit`` / ``git push`` / etc. (including hidden behind
``cmd /c`` / ``powershell -Command`` / ``bash -c`` wrappers) and flags them
as MEDIUM risk so the caller can route them through the approval flow.
"""

from __future__ import annotations

import shlex
from typing import Any

from app.security.risk import RiskAssessment, RiskLevel

__all__ = ["GitWritePolicy"]

_GIT_WRITE_SUBCOMMANDS: frozenset[str] = frozenset(
    {"commit", "push", "checkout", "clone", "pull", "add", "merge", "rebase", "reset", "stash"}
)

# Wrappers we recursively peel off to reach the actual git invocation.
_WRAPPER_FLAGS: dict[str, set[str]] = {
    "cmd": {"/c", "/k", "-c"},
    "powershell": {"-command", "-c", "/c"},
    "pwsh": {"-command", "-c"},
    "sh": {"-c"},
    "bash": {"-c"},
    "python": {"-c"},
    "python3": {"-c"},
}


def _basename(token: str) -> str:
    import os

    return os.path.splitext(os.path.basename(token))[0].lower()


def _extract_git_subcommand(command: str) -> str | None:
    """Return the git write subcommand if ``command`` is a git write, else None.

    Recursively unwraps ``cmd /c`` / ``powershell -Command`` / ``bash -c``.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or len(tokens) < 2:
        return None
    head = _basename(tokens[0])
    if head == "git":
        return tokens[1] if tokens[1] in _GIT_WRITE_SUBCOMMANDS else None
    flags = _WRAPPER_FLAGS.get(head)
    if not flags:
        return None
    for i, t in enumerate(tokens[1:], 1):
        if t.lower() in flags and i + 1 < len(tokens):
            return _extract_git_subcommand(" ".join(tokens[i + 1 :]))
    return None


class GitWritePolicy:
    """Flag ``git commit`` / ``git push`` / etc. (MEDIUM)."""

    name = "git_write"

    def assess(self, command: str, ctx: Any) -> list[RiskAssessment]:
        del ctx  # git-write detection is context-independent
        subcmd = _extract_git_subcommand(command)
        if subcmd is None:
            return []
        return [
            RiskAssessment(
                level=RiskLevel.MEDIUM,
                policy_name=self.name,
                rule_id=f"git_write:{subcmd}",
                message=(
                    f"git {subcmd} 是写操作，会改变仓库状态，需通过审批流执行"
                ),
                suggestion="通过审批面板批准后重试，或使用 git status / git diff 替代",
            )
        ]
