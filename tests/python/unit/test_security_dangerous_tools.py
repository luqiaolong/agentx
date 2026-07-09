"""危险工具分类 + compute_runtime_dangerous 单元测试。

覆盖：
1. DANGEROUS_TOOLS 不含 shell_exec（死代码移除）
2. FORBIDDEN_SUBAGENT_TOOLS 不含 shell_exec
3. compute_runtime_dangerous：交集 + 并集逻辑
4. frozenset 类型校验
"""

from __future__ import annotations


from app.security import (
    DANGEROUS_TOOLS,
    FORBIDDEN_SUBAGENT_TOOLS,
    compute_runtime_dangerous,
)


# ============================================================
# 1. DANGEROUS_TOOLS
# ============================================================


def test_dangerous_tools_is_frozenset() -> None:
    """DANGEROUS_TOOLS 是 frozenset。"""
    assert isinstance(DANGEROUS_TOOLS, frozenset)


def test_dangerous_tools_contains_expected_tools() -> None:
    """包含写操作 + Git 写操作 + delete_file。execute 已移除，审批改为 directory_extension 机制。"""
    expected = {
        "edit_file",
        "write_file",
        "delete_file",
        "git_clone",
        "git_pull",
        "git_checkout",
        "git_stage",
        "git_commit",
    }
    assert DANGEROUS_TOOLS == frozenset(expected)


def test_dangerous_tools_no_shell_exec() -> None:
    """DANGEROUS_TOOLS 不含 shell_exec（死代码已移除）。"""
    assert "shell_exec" not in DANGEROUS_TOOLS


# ============================================================
# 2. FORBIDDEN_SUBAGENT_TOOLS
# ============================================================


def test_forbidden_subagent_tools_is_frozenset() -> None:
    """FORBIDDEN_SUBAGENT_TOOLS 是 frozenset。"""
    assert isinstance(FORBIDDEN_SUBAGENT_TOOLS, frozenset)


def test_forbidden_subagent_tools_contains_write_ops() -> None:
    """包含写/编辑/git 写操作。"""
    assert "write_file" in FORBIDDEN_SUBAGENT_TOOLS
    assert "edit_file" in FORBIDDEN_SUBAGENT_TOOLS
    assert "git_clone" in FORBIDDEN_SUBAGENT_TOOLS
    assert "git_commit" in FORBIDDEN_SUBAGENT_TOOLS


def test_forbidden_subagent_tools_no_shell_exec() -> None:
    """FORBIDDEN_SUBAGENT_TOOLS 不含 shell_exec（死代码已移除）。"""
    assert "shell_exec" not in FORBIDDEN_SUBAGENT_TOOLS


def test_forbidden_subagent_tools_forbids_execute_and_cli_execute() -> None:
    """execute + cli_execute 均禁止子代理绑定（subagent 无审批流，shell 操作会绕过审批）。

    - ``execute`` = SafeLocalShellBackend 内置工具（新名）
    - ``cli_execute`` = 旧名，保留以过滤仍引用旧名的陈旧配置
    """
    assert "execute" in FORBIDDEN_SUBAGENT_TOOLS
    assert "cli_execute" in FORBIDDEN_SUBAGENT_TOOLS


# ============================================================
# 3. compute_runtime_dangerous
# ============================================================


def test_compute_runtime_dangerous_intersection() -> None:
    """仅返回 DANGEROUS_TOOLS ∩ enabled。execute 已不在 DANGEROUS_TOOLS 中。"""
    enabled = {"write_file", "read_file", "list_dir", "execute"}
    result = compute_runtime_dangerous(enabled, set())
    # execute 已从 DANGEROUS_TOOLS 移除，不再出现在 runtime_dangerous 中
    assert result == frozenset({"write_file"})


def test_compute_runtime_dangerous_union_with_mcp() -> None:
    """结果 = (DANGEROUS ∩ enabled) ∪ mcp_untrusted。"""
    enabled = {"write_file", "read_file"}
    mcp_untrusted = {"mcp_search", "mcp_delete"}
    result = compute_runtime_dangerous(enabled, mcp_untrusted)
    assert result == frozenset({"write_file", "mcp_search", "mcp_delete"})


def test_compute_runtime_dangerous_empty_enabled() -> None:
    """enabled 为空时仅含 mcp_untrusted。"""
    result = compute_runtime_dangerous(set(), {"mcp_evil"})
    assert result == frozenset({"mcp_evil"})


def test_compute_runtime_dangerous_all_empty() -> None:
    """全空输入返回空 frozenset。"""
    result = compute_runtime_dangerous(set(), set())
    assert result == frozenset()


def test_compute_runtime_dangerous_returns_frozenset() -> None:
    """返回类型为 frozenset。"""
    result = compute_runtime_dangerous({"write_file"}, {"mcp_x"})
    assert isinstance(result, frozenset)


def test_compute_runtime_dangerous_disabled_tool_excluded() -> None:
    """被禁用的危险工具不出现在结果中。"""
    enabled = {"read_file", "list_dir"}  # 不含任何危险工具
    result = compute_runtime_dangerous(enabled, set())
    assert result == frozenset()


def test_compute_runtime_dangerous_mcp_untrusted_always_included() -> None:
    """mcp_untrusted 即使不在 enabled 中也包含。"""
    enabled = {"read_file"}
    mcp_untrusted = {"mcp_dangerous_tool"}
    result = compute_runtime_dangerous(enabled, mcp_untrusted)
    assert "mcp_dangerous_tool" in result
