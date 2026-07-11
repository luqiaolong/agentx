"""Risk reporter — aggregate assessments into a single LLM-friendly message.

Used by both ``cli_execute`` and ``safe_shell_backend`` to format the
:class:`list[RiskAssessment]` produced by :class:`RiskClassifier` into
one human-readable string. Each assessment gets a numbered entry with
its policy, level, message, optional suggestion, and any matched chars.
"""

from __future__ import annotations

from typing import Any

from app.security.risk import RiskAssessment

__all__ = ["aggregate", "MAX_COMMAND_ECHO"]

MAX_COMMAND_ECHO = 200


def aggregate(
    assessments: list[RiskAssessment],
    command: str,
    ctx: Any,
) -> str:
    """Return an LLM-friendly aggregation of all assessments.

    Format::

        命令无法执行（N 项命中）：
          [1] policy.rule_id（LEVEL）
              原因：<message>
              建议：<suggestion>          # if non-empty
              命中字符：'x', 'y'          # if non-empty
          ...

        原命令：<command echo, truncated to MAX_COMMAND_ECHO>

    Args:
        assessments: Output of ``RiskClassifier.assess``.
        command: The original command string (echoed for LLM context).
        ctx: Execution context (reserved for future mode-specific formatting).

    Returns:
        Empty string when ``assessments`` is empty, otherwise the formatted
        block. ``ctx`` is accepted to keep the call site symmetric with
        the rest of the security package.
    """
    del ctx
    if not assessments:
        return ""
    lines: list[str] = [f"命令无法执行（{len(assessments)} 项命中）：\n"]
    for i, a in enumerate(assessments, 1):
        lines.append(f"  [{i}] {a.policy_name}.{a.rule_id}（{a.level.name}）")
        lines.append(f"      原因：{a.message}")
        if a.suggestion:
            lines.append(f"      建议：{a.suggestion}")
        if a.matched_chars:
            chars = ", ".join(repr(c) for c in a.matched_chars)
            lines.append(f"      命中字符：{chars}")
    cmd_echo = command if len(command) <= MAX_COMMAND_ECHO else command[:MAX_COMMAND_ECHO] + "..."
    lines.append(f"\n原命令：{cmd_echo}")
    return "\n".join(lines)
