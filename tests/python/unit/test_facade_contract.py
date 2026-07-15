"""Facade contract test: all ``app.deepagent.__all__`` symbols remain importable.

This test guards the public API boundary during the maintainability refactor.
If any symbol is removed or renamed, this test fails before deeper tests run.

Task 1.5 — see ``openspec/changes/deepagent-maintainability-refactor/tasks.md``.
"""

from __future__ import annotations

import app.deepagent as deepagent


def test_all_symbols_listed_in_all_are_importable() -> None:
    """Every name in ``__all__`` must resolve on the package object."""
    missing = [name for name in deepagent.__all__ if not hasattr(deepagent, name)]
    assert not missing, f"Symbols listed in __all__ but not importable: {missing}"


def test_all_count_is_twelve() -> None:
    """The refactor must preserve all 12 existing public symbols."""
    assert len(deepagent.__all__) == 12, (
        f"Expected 12 public symbols, got {len(deepagent.__all__)}: "
        f"{deepagent.__all__}"
    )


def test_expected_symbol_set() -> None:
    """The exact symbol set must match the pre-refactor contract."""
    expected = {
        "build_deep_agent",
        "run_deep_path",
        "trigger_profile_auto_extract",
        "create_agent",
        "run_agent_with_approval",
        "stream_agent_events",
        "DANGEROUS_TOOLS",
        "compute_runtime_dangerous",
        "make_deep_tools",
        "load_mcp_tools",
        "current_thread_id",
        "current_parent_thread_id",
    }
    actual = set(deepagent.__all__)
    assert actual == expected, (
        f"Public symbol set changed.\n"
        f"  Missing: {expected - actual}\n"
        f"  Extra:   {actual - expected}"
    )
