"""Metachar policy.

Two-mode scanner:

* **POSIX strict** (default): blocks ``; & | ` $ < >`` outside single
  quotes, and ``$`` plus backtick inside double quotes.
* **PowerShell lenient**: same strictness **outside** any quotes, but
  inside the double-quoted argument of ``-Command`` (or
  ``-NoProfile -Command``) every character is treated as a literal — the
  shell never re-parses inside the quoted argument, so ``$_`` / ``$Name`` /
  ``|`` / ``;`` / ``@{...}`` are all safe **in that region**.

The key invariant for PowerShell mode is *region-aware* scanning: only
what is actually delivered to bash outside of quotes is dangerous.

``ctx.is_argv`` short-circuits the whole policy: an argv list is not
parsed by the shell, so metachar filtering is unnecessary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.security.policies._command_name import extract_command_name
from app.security.risk import RiskAssessment, RiskLevel

__all__ = ["MetacharPolicy", "is_powershell_command"]


# Characters that are dangerous outside of any quoting in POSIX mode.
_DANGEROUS_OUTSIDE_POSIX = frozenset(";&|`$<>")
# Characters that are also dangerous inside double quotes (POSIX mode).
_DANGEROUS_IN_DQ_POSIX = frozenset("$`")


def is_powershell_command(command: str) -> bool:
    """True if the *outer* command name is powershell/pwsh."""
    name = extract_command_name(command)
    return name in {"powershell", "pwsh"}


@dataclass
class _ScanResult:
    hits: list[str]  # unique dangerous chars, in order of first appearance


def _scan_posix(value: str) -> _ScanResult:
    """Original strict algorithm (preserves legacy behaviour)."""
    hits: list[str] = []
    seen: set[str] = set()
    in_double = False
    in_single = False
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
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
        if in_single:
            pass
        elif in_double:
            if ch in _DANGEROUS_IN_DQ_POSIX and ch not in seen:
                seen.add(ch)
                hits.append(ch)
        else:
            if ch in _DANGEROUS_OUTSIDE_POSIX and ch not in seen:
                seen.add(ch)
                hits.append(ch)
        i += 1
    if in_double or in_single:
        # Unclosed quote: safe-fallback to a fully strict scan.
        return _scan_posix_strict(value)
    return _ScanResult(hits=hits)


def _scan_posix_strict(value: str) -> _ScanResult:
    """No-quote-awareness strict sweep. Used as a safe-fallback only."""
    hits: list[str] = []
    seen: set[str] = set()
    for ch in value:
        if ch in _DANGEROUS_OUTSIDE_POSIX and ch not in seen:
            seen.add(ch)
            hits.append(ch)
    return _ScanResult(hits=hits)


def _scan_powershell(command: str) -> _ScanResult:
    """Region-aware scan for ``powershell -Command "..."`` style invocations.

    Walks the command character-by-character. Inside any quoted region
    (single or double quotes that are not escaped) every character is
    treated as a literal. Outside quotes, every metacharacter is a hit.

    This mirrors what bash actually does with the command: the quoted
    argument is delivered as one string and never re-parsed.
    """
    hits: list[str] = []
    seen: set[str] = set()
    in_double = False
    in_single = False
    i = 0
    n = len(command)
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
        if in_single or in_double:
            # The whole quoted region is delivered as a literal to the
            # underlying process — bash does not re-parse it.
            pass
        else:
            if ch in _DANGEROUS_OUTSIDE_POSIX and ch not in seen:
                seen.add(ch)
                hits.append(ch)
        i += 1
    if in_double or in_single:
        # Unclosed quote: fall back to strict mode (safer).
        return _scan_posix_strict(command)
    return _ScanResult(hits=hits)


def _run_scan(command: str, *, ps_mode: bool) -> _ScanResult:
    return _scan_powershell(command) if ps_mode else _scan_posix(command)


class MetacharPolicy:
    """Shell metacharacter policy (Phase 5)."""

    name = "metachar"

    def assess(self, command: str, ctx: Any) -> list[RiskAssessment]:
        if ctx is not None and getattr(ctx, "is_argv", False):
            return []
        ps_mode = is_powershell_command(command)
        try:
            result = _run_scan(command, ps_mode=ps_mode)
        except Exception:  # noqa: BLE001
            return [
                RiskAssessment(
                    level=RiskLevel.MEDIUM,
                    policy_name=self.name,
                    rule_id="scan_error",
                    message="元字符扫描异常，安全降级为拦截",
                    suggestion="简化命令结构后重试",
                )
            ]
        if not result.hits:
            return []
        rule_id = "ps_blocked_chars" if ps_mode else "posix_forbidden_chars"
        return [
            RiskAssessment(
                level=RiskLevel.MEDIUM,
                policy_name=self.name,
                rule_id=rule_id,
                message=(
                    "命令包含 PowerShell 模式下禁止的元字符（位于引号外）"
                    if ps_mode
                    else "命令包含禁止的 shell 元字符"
                ),
                matched_chars=tuple(result.hits),
                suggestion=(
                    "PowerShell 中：把整条脚本放在 -Command \"...\" 内即可（当前为引号外）"
                    if ps_mode
                    else "拆分复杂命令为多个简单命令，或使用 Python 标准库替代"
                ),
            )
        ]
