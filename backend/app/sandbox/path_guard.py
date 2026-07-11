"""Sandbox path normalization and critical-directory protection (pure functions).

Migrated from ``app.utils.security``; sits parallel to ``deep/``, ``team/``, ``tools/``.

Public API:
- ``PathNotAuthorized``: raised when a path is not authorized.
- ``normalize_path``: normalize a path (delegates to ``app.utils.paths``).
- ``is_under``: check whether ``child`` equals ``base`` or lives under it
  (uses ``relative_to``, not string prefix).
- ``is_critical``: is the path a system-critical directory / ancestor / descendant?
- ``CRITICAL_DIRS``: platform-dependent critical directory list.
- ``SCRATCH_DIR``: scratch workspace under ``WORKSPACE_DIR``
  (default: ``data/workspace/.scratch``).
- ``DEFAULT_WHITELIST``: always-read-writable whitelist
  (``WORKSPACE_DIR`` + ``UPLOADS_DIR`` + ``SCRATCH_DIR``).

Linux bug fix:
    The original ``_critical_dirs()`` included ``Path("/")`` on non-win32
    platforms; ``_is_under(resolved, "/")`` succeeded for any absolute path,
    so the sandbox became unusable on Linux. Fix: for root paths
    (``parent == self``), reject only ``path == root`` itself, not its
    descendants.

O2 improvement (scratch directory):
    New ``SCRATCH_DIR = WORKSPACE_DIR / ".scratch"`` acts as the agent's
    always-authorized scratch area (read+write, no user grant needed).
    Combined with ``SafeLocalShellBackend``'s ``rm/del/unlink`` path check,
    the agent can clean up its own scratch files without polluting the
    authorized directory.

    User rule: scratch files may live under ``WORKSPACE_DIR``; if
    ``WORKSPACE_DIR`` is empty, fall back to ``<startup_dir>/WORKSPACE_DIR``.
    The current implementation always resolves ``WORKSPACE_DIR`` via
    ``app.config.settings`` (no runtime fallback needed). If runtime
    redirection is ever added, wire the fallback here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.config import PROJECT_ROOT, UPLOADS_DIR, WORKSPACE_DIR

__all__ = [
    "PathNotAuthorized",
    "normalize_path",
    "is_under",
    "is_critical",
    "CRITICAL_DIRS",
    "SCRATCH_DIR",
    "DEFAULT_WHITELIST",
]


def _resolve_scratch_dir() -> Path:
    """Resolve the scratch directory, creating it if missing.

    Always returns ``WORKSPACE_DIR / ".scratch"`` (resolved). Falls back to
    ``PROJECT_ROOT / "WORKSPACE_DIR"`` if ``WORKSPACE_DIR`` is empty/missing
    (per user rule: if ``WORKSPACE_DIR`` is empty, use
    ``<startup_dir>/WORKSPACE_DIR``; ``PROJECT_ROOT`` is the startup dir).
    """
    base = WORKSPACE_DIR if WORKSPACE_DIR and str(WORKSPACE_DIR).strip() else PROJECT_ROOT / "WORKSPACE_DIR"
    scratch = (base / ".scratch").resolve()
    try:
        scratch.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return scratch


class PathNotAuthorized(Exception):
    """Path not authorized."""


def is_under(child: Path, base: Path) -> bool:
    """Return True if ``child`` equals ``base`` or lives under it.

    Uses ``relative_to`` rather than string prefix to avoid
    ``d:/docs`` accidentally matching ``d:/docs-other``.
    """
    try:
        child.relative_to(base)
        return True
    except ValueError:
        return False


def _build_critical_dirs() -> list[Path]:
    """Return the OS-specific system critical directory list (normalized)."""
    home = Path.home().resolve()
    if sys.platform == "win32":
        candidates = [
            Path("C:/Windows"),
            Path("C:/Program Files"),
            Path("C:/Program Files (x86)"),
            home,
        ]
    else:
        candidates = [
            Path("/"),
            Path("/etc"),
            Path("/usr"),
            Path("/bin"),
            Path("/sbin"),
            Path("/var"),
            Path("/boot"),
            Path("/root"),
            home,
        ]
    return [c.resolve() for c in candidates]


# Module-level constants: computed once at import time.
CRITICAL_DIRS: list[Path] = _build_critical_dirs()
SCRATCH_DIR: Path = _resolve_scratch_dir()
DEFAULT_WHITELIST: list[Path] = [
    WORKSPACE_DIR.resolve(),
    UPLOADS_DIR.resolve(),
    SCRATCH_DIR,
]


def is_critical(path: Path) -> bool:
    """Whether the path is a system-critical directory / ancestor / descendant.

    - Root paths (``parent == self``): only reject ``path == root`` itself, not
      descendants (Linux bug fix).
    - System dirs (C:/Windows, etc.): reject ``resolved`` as the dir itself,
      its ancestor (e.g. ``C:/``), or its descendant (e.g.
      ``C:/Windows/System32``) -- bidirectional ``is_under``.
    - User home: only reject ``resolved`` as home itself or its ancestor
      (e.g. ``C:/Users``); do not reject home subdirectories (so the user
      can grant ``C:/Users/me/Projects``).
    """
    resolved = path.resolve()
    home = Path.home().resolve()
    for crit in CRITICAL_DIRS:
        # Root: only reject itself, not descendants (Linux bug fix).
        if crit.parent == crit:
            if resolved == crit:
                return True
            continue
        if crit == home:
            # home: only reject home itself or its parent.
            if is_under(crit, resolved):
                return True
            continue
        # System dirs: reject both ancestors and descendants.
        if is_under(resolved, crit) or is_under(crit, resolved):
            return True
    return False


def normalize_path(path: str | Path, base: str | Path | None = None) -> Path:
    """Normalize a path. Relative paths resolve against ``base`` or ``PROJECT_ROOT``.

    On Windows we additionally normalize the drive letter (lowercase) and
    separator (forward slash) so that ``D:/workspace`` and ``d:\\workspace``
    are treated as the same path (BUG-3 fix).

    Args:
        path: Input path (string or ``Path``).
        base: Optional base directory. If provided, relative paths resolve
            against this directory; otherwise against ``PROJECT_ROOT``.

    Returns:
        Normalized absolute ``Path`` (via ``resolve()`` to handle ``..``,
        symlinks, and case).
    """
    p = Path(path)
    if not p.is_absolute():
        root = Path(base) if base else PROJECT_ROOT
        p = root / p
    resolved = p.resolve()

    # Windows path normalization: lowercase drive letter + forward slash separator.
    if sys.platform == "win32" and resolved.parts:
        drive = resolved.parts[0]
        if len(drive) == 2 and drive[1] == ":":
            normalized = drive.lower() + "/" + "/".join(resolved.parts[1:])
            return Path(normalized)

    return resolved