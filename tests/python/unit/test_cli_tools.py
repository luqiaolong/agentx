"""CLI 工具单元测试。

覆盖：
1. 黑名单命令被拒绝（rm/del/format/sudo 等）
2. shell 元字符参数被拒绝
3. 系统关键目录被拒绝
4. 未授权目录被拒绝（workspace 模式）
5. full_trust 模式下授权目录可执行
6. 禁用开关生效
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.tools.cli import (
    _DEFAULT_BLOCKLIST,
    _has_forbidden_chars,
    _is_command_blocked,
    cli_execute,
)
from app.utils.security import get_sandbox


def test_cli_tool_schema_uses_arguments_not_vargs() -> None:
    """@tool 包裹的 cli_execute schema 必须使用 arguments，避免 LangChain 重命名为 v__args。"""
    from app.subagents.base import make_cli_tools

    tools = make_cli_tools("t-schema")
    assert len(tools) == 1
    cli_tool = tools[0]
    schema = cli_tool.args_schema.model_json_schema()
    assert "arguments" in schema.get("properties", {})
    assert "v__args" not in schema.get("properties", {})


@pytest.fixture
def fresh_sandbox():
    """每个测试使用独立 sandbox 内存状态。"""
    import app.utils.security

    old = app.utils.security._sandbox
    from app.utils.security import SessionSandbox

    app.utils.security._sandbox = SessionSandbox()
    yield app.utils.security._sandbox
    app.utils.security._sandbox = old


# ============================================================
# 1. 黑名单
# ============================================================


def test_default_blocklist_contains_dangerous_commands() -> None:
    """默认黑名单包含删除/格式化/提权等命令。"""
    assert "rm" in _DEFAULT_BLOCKLIST
    assert "del" in _DEFAULT_BLOCKLIST
    assert "format" in _DEFAULT_BLOCKLIST
    assert "sudo" in _DEFAULT_BLOCKLIST
    assert "shutdown" in _DEFAULT_BLOCKLIST


def test_is_command_blocked() -> None:
    """黑名单命令被阻止（不区分大小写）。"""
    assert _is_command_blocked("rm")
    assert _is_command_blocked("RM")
    assert _is_command_blocked("Format")
    assert not _is_command_blocked("git")
    assert not _is_command_blocked("npm")
    assert not _is_command_blocked("python")


# ============================================================
# 2. shell 元字符
# ============================================================


def test_has_forbidden_chars() -> None:
    """包含 shell 元字符的参数被检测到。"""
    assert _has_forbidden_chars("hello;world")
    assert _has_forbidden_chars("a && b")
    assert _has_forbidden_chars("a | b")
    assert _has_forbidden_chars("$(whoami)")
    assert _has_forbidden_chars("`whoami`")
    assert _has_forbidden_chars("file > /dev/null")
    assert _has_forbidden_chars("file < input")
    assert not _has_forbidden_chars("normal_arg")
    assert not _has_forbidden_chars("--flag")
    assert not _has_forbidden_chars("path/to/file")


# ============================================================
# 3. 工具执行（黑名单/禁用）
# ============================================================


async def test_cli_execute_blocked_command(fresh_sandbox) -> None:
    """黑名单命令返回错误字符串。"""
    result = await cli_execute("t1", "rm", ["-rf", "/tmp/test"])
    assert "黑名单" in result


async def test_cli_execute_disabled(monkeypatch: pytest.MonkeyPatch, fresh_sandbox) -> None:
    """cli_tool_enabled=False 时返回禁用提示。"""
    monkeypatch.setenv("AGENTX_CLI_TOOL_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        result = await cli_execute("t1", "git", ["status"])
        assert "未启用" in result
    finally:
        monkeypatch.delenv("AGENTX_CLI_TOOL_ENABLED", raising=False)
        get_settings.cache_clear()


async def test_cli_execute_empty_command(fresh_sandbox) -> None:
    """空命令返回错误。"""
    result = await cli_execute("t1", "  ")
    assert "不能为空" in result


async def test_cli_execute_forbidden_arg(fresh_sandbox) -> None:
    """参数含 shell 元字符返回错误。"""
    result = await cli_execute("t1", "git", ["status; rm -rf /"])
    assert "非法字符" in result


# ============================================================
# 4. 授权检查
# ============================================================


async def test_cli_execute_unauthorized_cwd(fresh_sandbox, tmp_path: Path) -> None:
    """workspace 模式下未授权目录被拒绝。"""
    result = await cli_execute("t1", "git", ["status"], cwd=str(tmp_path))
    assert "未授权" in result or "仅授权读取" in result


async def test_cli_execute_authorized_cwd(fresh_sandbox, tmp_path: Path) -> None:
    """workspace 模式下已授权目录可执行。"""
    sandbox = get_sandbox()
    sandbox.authorize("t1", str(tmp_path), writable=True)

    # 使用 git --version（不依赖 cwd 是 git 仓库）
    result = await cli_execute("t1", "git", ["--version"], cwd=str(tmp_path))
    assert "git version" in result


async def test_cli_execute_full_trust(fresh_sandbox, tmp_path: Path) -> None:
    """full_trust 模式下跳过授权检查。"""
    sandbox = get_sandbox()
    sandbox.set_full_trust("t1", True)

    result = await cli_execute("t1", "git", ["--version"], cwd=str(tmp_path))
    assert "git version" in result
