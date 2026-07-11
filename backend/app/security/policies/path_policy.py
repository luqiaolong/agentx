"""Path policy — quote-aware quick-prescan.

Walks the command char-by-char and flags any Windows / POSIX absolute
path token that appears *outside* of any quoted region. Inside quoted
regions (single or double quotes that are not escaped) the characters
are literals and never reach the shell, so a ``/`` inside a
``-Command "..."`` is a division, not a path.

The real authorisation decision (is this path in any whitelist?) lives
in :mod:`app.sandbox.session_sandbox` and the new
:mod:`app.security.path_hint` formatter. This policy is a cheap signal
that helps the LLM understand "your command references a path the
sandbox will almost certainly reject".
"""

from __future__ import annotations

import re
from typing import Any

from app.security.risk import RiskAssessment, RiskLevel

__all__ = ["PathPolicy"]

# Windows absolute: drive letter + : + \ or / + at least one safe path char.
_WIN_RE = re.compile(r"[A-Za-z]:[\\/][A-Za-z0-9_.\-]+(?:[\\/][A-Za-z0-9_.\-]+)*")
# POSIX absolute: / + segment. Require the segment to look "pathy" (no
# trailing arithmetic / bracket access). At least one ``.`` in the path
# or a sub-path separator is a strong path signal.
_POSIX_RE = re.compile(
    r"/[A-Za-z0-9_.\-]+(?:[\\/][A-Za-z0-9_.\-]+)+"  # /a/b  (at least 2 segments)
    r"|/(?:home|etc|var|tmp|usr|opt|root|data|workspace|Users|Users/luqia)[/A-Za-z0-9_.\-]*"  # common roots
)


def _find_paths_outside_quotes(command: str) -> list[str]:
    """Return absolute path tokens that are not enclosed in any quotes."""
    in_double = False
    in_single = False
    i = 0
    n = len(command)
    out: list[tuple[int, str]] = []
    while i < n:
        ch = command[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if not in_single and not in_double:
            tail = command[i:]
            for regex in (_WIN_RE, _POSIX_RE):
                m = regex.match(tail)
                if m:
                    out.append((i, m.group(0)))
                    i += len(m.group(0))
                    break
            else:
                i += 1
        else:
            i += 1
    return [s for _, s in out]


class PathPolicy:
    """Detect absolute path tokens outside any quoted region (MEDIUM)."""

    name = "path"

    def assess(self, command: str, ctx: Any) -> list[RiskAssessment]:
        if ctx is not None and getattr(ctx, "is_path_unrestricted", False):
            return []
        paths = _find_paths_outside_quotes(command)
        if not paths:
            return []
        # De-dup while preserving first-occurrence order.
        seen: set[str] = set()
        unique: list[str] = []
        for p in paths:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        target = max(unique, key=len)
        return [
            RiskAssessment(
                level=RiskLevel.MEDIUM,
                policy_name=self.name,
                rule_id="path_token_present",
                message=f"命令包含绝对路径: {target}",
                suggestion=(
                    "改用相对路径（data/workspace/...）或已授权目录；"
                    "未授权时沙箱会拒绝并给出改写建议"
                ),
            )
        ]
