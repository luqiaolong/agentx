"""Shared helpers for policies that need to look at the command name.

Extracted from ``safe_shell_backend`` so blocklist / git_write / future
policies can all share the same wrapper-aware name extraction without
re-implementing ``shlex`` recursion.
"""

from __future__ import annotations

import os
import shlex

__all__ = ["extract_command_name", "extract_command_names"]

# Command wrappers that need recursive parsing of their inner command.
_WRAPPER_PREFIXES: dict[str, set[str]] = {
    "cmd": {"/c", "/k", "-c"},
    "powershell": {"-command", "-c", "/c"},
    "pwsh": {"-command", "-c"},
    "sh": {"-c"},
    "bash": {"-c"},
    "python": {"-c"},
    "python3": {"-c"},
}


def _basename_no_ext(token: str) -> str:
    base = os.path.basename(token)
    return os.path.splitext(base)[0]


def _safe_split(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def extract_command_name(command: str) -> str:
    """Return the *first* command name (basename, no extension)."""
    tokens = _safe_split(command)
    if not tokens:
        return ""
    return _basename_no_ext(tokens[0]).lower()


def _recursively_extract(command: str) -> list[str]:
    """Return ``[outer_name, inner_name, ...]`` for wrapper commands."""
    names: list[str] = []
    tokens = _safe_split(command)
    if not tokens:
        return names
    outer = _basename_no_ext(tokens[0]).lower()
    names.append(outer)
    flags = _WRAPPER_PREFIXES.get(outer)
    if not flags or len(tokens) < 3:
        return names
    for i, t in enumerate(tokens[1:], 1):
        if t.lower() in flags and i + 1 < len(tokens):
            inner = " ".join(tokens[i + 1 :])
            names.extend(_recursively_extract(inner))
            break
    return names


def extract_command_names(command: str) -> list[str]:
    """Return the outer name plus any names hidden behind wrappers."""
    return _recursively_extract(command)
