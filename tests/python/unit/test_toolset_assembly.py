"""Regression tests for unified AgentToolset assembly (RED phase).

Covers OpenSpec change ``deepagent-maintainability-refactor`` tasks 2.1–2.3.

Expected behavior (per spec):
- An immutable ``AgentToolset`` is assembled BEFORE ``create_deep_agent`` is called,
  unifying tool list, ``excluded_builtin_tools``, and ``approval_required_tools``.
- The same ``interrupt_on`` feeds both the graph's ``HumanInTheLoopMiddleware`` and
  the approval runner's ``runtime_dangerous`` set.
- ``tools_enabled`` applies to DeepAgents built-in fs tools (write_file/edit_file/...),
  not just project tools.
- ``request_permission`` has an explicit key in ``tools_enabled`` defaults.

These tests are expected to FAIL or ERROR against the current codebase because:
- ``AgentToolset`` / ``assemble_agent_toolset`` do not exist yet (2.1, 2.2).
- ``request_permission`` is absent from ``_ALL_TOOLS`` and relies on
  ``.get(name, True)`` fallback (2.3).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config import get_settings
from app.security.dangerous_tools import FORBIDDEN_SUBAGENT_TOOLS


# ============================================================
# 2.1 — Untrusted MCP tool appears in build-time interrupt_on
# ============================================================


def test_untrusted_mcp_tool_in_build_time_interrupt_on() -> None:
    """An untrusted MCP tool (trusted=False) MUST appear in the build-time
    ``interrupt_on`` configuration that would be passed to ``create_deep_agent``.

    Currently ``interrupt_on`` is derived post-build from ``DANGEROUS_TOOLS``
    only, so an MCP tool absent from the graph's
    ``HumanInTheLoopMiddleware.interrupt_on`` can execute before the runner
    can ask for approval.

    RED: ``assemble_agent_toolset`` does not exist yet → ImportError.
    """
    from app.deepagent.tool_assembly import assemble_agent_toolset  # noqa: F401

    untrusted_mcp_tool = MagicMock()
    untrusted_mcp_tool.name = "mcp_untrusted_query"

    toolset = assemble_agent_toolset(
        project_tools=[untrusted_mcp_tool],
        mcp_untrusted_names={"mcp_untrusted_query"},
        workspace_path="/tmp/ws",
    )

    interrupt_on = toolset.interrupt_on
    assert "mcp_untrusted_query" in interrupt_on, (
        "untrusted MCP tool must be present in build-time interrupt_on so the "
        "graph pauses before the runner asks for approval"
    )
    assert interrupt_on["mcp_untrusted_query"] is True


# ============================================================
# 2.2 — Disabled built-in fs tools merged into effective excluded_tools
# ============================================================


def test_disabled_builtin_fs_tools_union_with_forbidden_subagent_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``tools_enabled.write_file = False`` and a subagent is assembled with
    ``FORBIDDEN_SUBAGENT_TOOLS``, the effective ``excluded_builtin_tools`` MUST
    contain the union of both sets.

    Currently ``tools_enabled`` is only applied to project tools in
    ``make_deep_tools``, NOT to DeepAgents built-in fs tools injected by the
    backend. So disabling ``write_file`` has no effect on the built-in
    ``write_file`` tool visibility.

    RED: ``assemble_agent_toolset`` does not exist yet → ImportError.
    """
    from app.deepagent.tool_assembly import assemble_agent_toolset  # noqa: F401

    settings = get_settings()
    monkeypatch.setattr(
        type(settings),
        "tools_config",
        property(lambda self: {"write_file": False}),
    )
    get_settings.cache_clear()

    toolset = assemble_agent_toolset(
        project_tools=[],
        mcp_untrusted_names=set(),
        workspace_path="/tmp/ws",
        subagent_exclusions=FORBIDDEN_SUBAGENT_TOOLS,
    )

    effective_excluded = set(toolset.excluded_builtin_tools)
    assert "write_file" in effective_excluded, (
        "disabled built-in write_file must be in effective excluded_builtin_tools"
    )
    # Union with FORBIDDEN_SUBAGENT_TOOLS — all forbidden tools must be excluded
    assert FORBIDDEN_SUBAGENT_TOOLS.issubset(effective_excluded), (
        "FORBIDDEN_SUBAGENT_TOOLS must be a subset of effective excluded_builtin_tools"
    )


# ============================================================
# 2.3 — request_permission has explicit key in tools_enabled defaults
# ============================================================


def test_request_permission_has_explicit_key_in_tools_enabled() -> None:
    """``request_permission`` MUST have an explicit enabled state in the default
    ``tools_enabled`` settings, not relying on the ``.get(name, True)`` unknown-key
    fallback.

    Currently ``_ALL_TOOLS`` (the source of default ``tools_enabled`` keys) does
    NOT include ``request_permission``, so it always passes through
    ``make_deep_tools`` via ``enabled.get(t.name, True)``.

    RED: ``"request_permission"`` is absent from ``get_settings().tools_enabled``.
    """
    get_settings.cache_clear()
    tools_enabled = get_settings().tools_enabled

    assert "request_permission" in tools_enabled, (
        "request_permission must have an explicit key in tools_enabled defaults, "
        "not rely on .get(name, True) unknown-key fallback"
    )
