"""Path hint formatter — produce an LLM-friendly error message that
lists authorised directories and recommends the scratch directory as a
fallback.

The output is consumed by ``session_sandbox._unauthorized_read_hint`` /
``_unauthorized_write_hint``. Keeping the formatter standalone and pure
makes it trivially testable; the sandbox hooks just pass the current
authorised-paths snapshot in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

__all__ = ["format_unauthorized_hint", "MAX_LISTED_DIRS"]

MAX_LISTED_DIRS = 5


def _filename_of(raw: str) -> str:
    """Best-effort filename extraction; falls back to ``tmp`` on empty / dot."""
    name = Path(raw).name
    if not name or name in {".", ".."}:
        return "tmp"
    return name


def format_unauthorized_hint(
    rejected_path: str | Path,
    action: Literal["read", "write"],
    authorized_paths: list[tuple[Path, bool]],
    scratch_path: Path,
) -> str:
    """Return the LLM-facing message describing what to do next.

    Args:
        rejected_path: The path the agent just tried to use.
        action: ``"read"`` or ``"write"`` — drives the dialog hint copy.
        authorized_paths: Snapshot of ``(path, writable)`` pairs for the thread.
        scratch_path: Always-writable scratch directory for the agent.

    Returns:
        A multi-line Chinese message starting with the rejection reason,
        then either the writable-dir list or the scratch fallback, then the
        dialog hint.
    """
    raw = str(rejected_path).strip()
    fname = _filename_of(raw)
    action_verb = "读取" if action == "read" else "写入"
    lines: list[str] = [f"路径 {raw} 未授权{action_verb}。\n"]

    writable = [p for p, w in authorized_paths if w][:MAX_LISTED_DIRS]
    if writable:
        lines.append("当前会话已授权的可写目录：")
        for p in writable:
            lines.append(f"  • {p.as_posix()}")
        lines.append("")

    # 使用传入的 scratch_path（避免硬编码），统一 posix 格式便于 LLM 阅读
    scratch_posix = scratch_path.as_posix().rstrip("/")
    lines.append("推荐改写到 scratch（始终可写）：")
    lines.append(f"  {scratch_posix}/{fname}")
    if action == "write":
        lines.append("\n或通过 dialog 授权该目录（勾选'允许写入'）后重试。")
    else:
        lines.append("\n或通过 dialog 授权该目录（读取）后重试。")
    return "\n".join(lines)
