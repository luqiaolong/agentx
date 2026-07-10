"""命令过滤 + 参数脱敏单元测试。

覆盖：
1. DEFAULT_BLOCKLIST 黑名单命令
2. FORBIDDEN_ARG_PATTERN shell 元字符
3. is_command_blocked / has_forbidden_args
4. redact_args 各工具脱敏（write_file/edit_file/cli_execute/其他）
5. redact_args 输入类型处理（dict/list/str/None）
6. is_git_write_command Git 写操作检测（Phase B.2）
"""

from __future__ import annotations

import pytest

from app.security import (
    DEFAULT_BLOCKLIST,
    FORBIDDEN_ARG_PATTERN,
    has_forbidden_args,
    is_command_blocked,
    is_git_write_command,
    redact_args,
)


# ============================================================
# 1. DEFAULT_BLOCKLIST
# ============================================================


def test_default_blocklist_is_frozenset() -> None:
    assert isinstance(DEFAULT_BLOCKLIST, frozenset)


def test_default_blocklist_contains_rm() -> None:
    assert "rm" in DEFAULT_BLOCKLIST
    assert "del" in DEFAULT_BLOCKLIST
    assert "format" in DEFAULT_BLOCKLIST
    assert "sudo" in DEFAULT_BLOCKLIST
    assert "shutdown" in DEFAULT_BLOCKLIST


def test_default_blocklist_no_safe_commands() -> None:
    """安全命令不在黑名单。"""
    assert "git" not in DEFAULT_BLOCKLIST
    assert "npm" not in DEFAULT_BLOCKLIST
    assert "python" not in DEFAULT_BLOCKLIST


# ============================================================
# 2. FORBIDDEN_ARG_PATTERN
# ============================================================


def test_forbidden_arg_pattern_detects_semicolon() -> None:
    assert bool(FORBIDDEN_ARG_PATTERN.search("hello;world"))


def test_forbidden_arg_pattern_detects_pipe() -> None:
    assert bool(FORBIDDEN_ARG_PATTERN.search("a | b"))


def test_forbidden_arg_pattern_detects_backtick() -> None:
    assert bool(FORBIDDEN_ARG_PATTERN.search("`whoami`"))


def test_forbidden_arg_pattern_detects_dollar() -> None:
    assert bool(FORBIDDEN_ARG_PATTERN.search("$(whoami)"))


def test_forbidden_arg_pattern_detects_redirect() -> None:
    assert bool(FORBIDDEN_ARG_PATTERN.search("file > /dev/null"))
    assert bool(FORBIDDEN_ARG_PATTERN.search("file < input"))


def test_forbidden_arg_pattern_allows_safe_args() -> None:
    assert not bool(FORBIDDEN_ARG_PATTERN.search("normal_arg"))
    assert not bool(FORBIDDEN_ARG_PATTERN.search("--flag"))
    assert not bool(FORBIDDEN_ARG_PATTERN.search("path/to/file"))


# ============================================================
# 3. is_command_blocked / has_forbidden_args
# ============================================================


def test_is_command_blocked_case_insensitive() -> None:
    assert is_command_blocked("rm")
    assert is_command_blocked("RM")
    assert is_command_blocked("Format")
    assert not is_command_blocked("git")
    assert not is_command_blocked("npm")


def test_is_command_blocked_strips_whitespace() -> None:
    assert is_command_blocked("  rm  ")
    assert not is_command_blocked("  git  ")


def test_has_forbidden_args_true() -> None:
    assert has_forbidden_args("hello;world")
    assert has_forbidden_args("a && b")
    assert has_forbidden_args("a | b")


def test_has_forbidden_args_false() -> None:
    assert not has_forbidden_args("normal_arg")
    assert not has_forbidden_args("--flag")
    assert not has_forbidden_args("path/to/file")


@pytest.mark.parametrize(
    "payload",
    [
        "echo 1\necho 2",
        "echo 1\r\necho 2",
        "echo $'\n'",
    ],
)
def test_has_forbidden_args_blocks_newline(payload: str) -> None:
    """换行符可被 shell 利用来拼接多行命令，必须被拦截。"""
    assert has_forbidden_args(payload) is True


# ============================================================
# 4. redact_args — write_file / edit_file
# ============================================================


def test_redact_args_write_file_hides_content() -> None:
    """write_file 的 content 字段被脱敏。"""
    args = {"path": "/tmp/a.txt", "content": "secret data"}
    result = redact_args("write_file", args)
    assert result["path"] == "/tmp/a.txt"
    assert result["content"] == "<redacted>"


def test_redact_args_edit_file_hides_text() -> None:
    """edit_file 的 old_string / new_string 被脱敏。"""
    args = {"file_path": "/tmp/a.txt", "old_string": "old", "new_string": "new"}
    result = redact_args("edit_file", args)
    assert result["file_path"] == "/tmp/a.txt"
    assert result["old_string"] == "<redacted>"
    assert result["new_string"] == "<redacted>"


def test_redact_args_write_file_preserves_other_fields() -> None:
    """非敏感字段保持不变。"""
    args = {"path": "/tmp/a.txt", "content": "secret", "encoding": "utf-8"}
    result = redact_args("write_file", args)
    assert result["encoding"] == "utf-8"
    assert result["path"] == "/tmp/a.txt"


def test_redact_args_does_not_mutate_input() -> None:
    """脱敏不修改原 dict。"""
    args = {"path": "/tmp/a.txt", "content": "secret"}
    redact_args("write_file", args)
    assert args["content"] == "secret"


# ============================================================
# 5. redact_args — cli_execute（凭证脱敏）
# ============================================================


def test_redact_args_cli_execute_token_in_command() -> None:
    """command 中的 token=xxx 被脱敏。"""
    args = {"command": "git clone https://host/repo?token=abc123", "arguments": []}
    result = redact_args("cli_execute", args)
    assert "token=abc123" not in result["command"]
    assert "***REDACTED***" in result["command"]


def test_redact_args_cli_execute_password_in_command() -> None:
    """command 中的 password=xxx 被脱敏。"""
    args = {"command": "curl -u user:pass?url=password=secret123", "arguments": []}
    result = redact_args("cli_execute", args)
    assert "password=secret123" not in result["command"]
    assert "***REDACTED***" in result["command"]


def test_redact_args_cli_execute_user_pass_at_host() -> None:
    """command 中的 user:pass@host 被脱敏。"""
    args = {"command": "git clone https://user:pass@github.com/repo.git", "arguments": []}
    result = redact_args("cli_execute", args)
    assert "user:pass@github.com" not in result["command"]
    assert "***REDACTED***" in result["command"]


def test_redact_args_cli_execute_arguments_list() -> None:
    """arguments 列表中的凭证被脱敏。"""
    args = {
        "command": "git",
        "arguments": ["clone", "https://host/repo?token=abc123"],
    }
    result = redact_args("cli_execute", args)
    assert "token=abc123" not in str(result["arguments"])
    assert "***REDACTED***" in str(result["arguments"])


def test_redact_args_cli_execute_no_credentials() -> None:
    """无凭证的 cli_execute 参数原样返回。"""
    args = {"command": "git status", "arguments": ["--porcelain"]}
    result = redact_args("cli_execute", args)
    assert result["command"] == "git status"
    assert result["arguments"] == ["--porcelain"]


def test_redact_args_cli_execute_case_insensitive_token() -> None:
    """TOKEN=xxx（大写）也被脱敏。"""
    args = {"command": "fetch URL?TOKEN=secret", "arguments": []}
    result = redact_args("cli_execute", args)
    assert "TOKEN=secret" not in result["command"]


# ============================================================
# 6. redact_args — 其他工具 + 输入类型
# ============================================================


def test_redact_args_unknown_tool_passes_through() -> None:
    """未知工具的 dict 参数原样返回（拷贝）。"""
    args = {"path": "/tmp/a.txt", "data": "value"}
    result = redact_args("read_file", args)
    assert result == args
    assert result is not args  # 应是新 dict


def test_redact_args_list_input_for_cli_execute() -> None:
    """list 输入包装为 {"arguments": [...]} 并脱敏。"""
    args = ["clone", "https://host/repo?token=secret"]
    result = redact_args("cli_execute", args)
    assert "arguments" in result
    assert "token=secret" not in str(result["arguments"])


def test_redact_args_str_input_for_cli_execute() -> None:
    """str 输入包装为 {"command": "..."} 并脱敏。"""
    args = "git clone https://user:pass@host/repo.git"
    result = redact_args("cli_execute", args)
    assert "command" in result
    assert "user:pass@host" not in result["command"]


def test_redact_args_list_input_for_other_tool() -> None:
    """非 cli_execute 的 list 输入包装为 {"arguments": [...]} 不脱敏。"""
    args = ["a", "b", "c"]
    result = redact_args("read_file", args)
    assert result == {"arguments": ["a", "b", "c"]}


def test_redact_args_str_input_for_other_tool() -> None:
    """非 cli_execute 的 str 输入包装为 {"input": "..."} 不脱敏。"""
    args = "some text"
    result = redact_args("read_file", args)
    assert result == {"input": "some text"}


def test_redact_args_none_input() -> None:
    """None 输入返回空 dict。"""
    result = redact_args("write_file", None)
    assert result == {}


def test_redact_args_int_input() -> None:
    """非 dict/list/str 输入返回空 dict。"""
    result = redact_args("write_file", 123)
    assert result == {}


# ============================================================
# 7. is_git_write_command — Git 写操作检测（Phase B.2）
# ============================================================


def test_is_git_write_command_commit() -> None:
    """git commit -m "msg" 是写操作。"""
    assert is_git_write_command('git commit -m "msg"') is True


def test_is_git_write_command_push() -> None:
    """git push origin main 是写操作。"""
    assert is_git_write_command("git push origin main") is True


def test_is_git_write_command_clone() -> None:
    """git clone https://... 是写操作。"""
    assert is_git_write_command("git clone https://github.com/user/repo.git") is True


def test_is_git_write_command_status_is_readonly() -> None:
    """git status 是只读操作，不是写操作。"""
    assert is_git_write_command("git status") is False


def test_is_git_write_command_diff_is_readonly() -> None:
    """git diff 是只读操作。"""
    assert is_git_write_command("git diff") is False


def test_is_git_write_command_log_is_readonly() -> None:
    """git log 是只读操作。"""
    assert is_git_write_command("git log") is False


def test_is_git_write_command_branch_is_readonly() -> None:
    """git branch（无 -d/-D）是只读操作。"""
    assert is_git_write_command("git branch") is False


def test_is_git_write_command_show_is_readonly() -> None:
    """git show 是只读操作。"""
    assert is_git_write_command("git show") is False


def test_is_git_write_command_non_git_command() -> None:
    """非 git 命令返回 False。"""
    assert is_git_write_command("echo hello") is False


def test_is_git_write_command_empty_string() -> None:
    """空字符串返回 False。"""
    assert is_git_write_command("") is False


def test_is_git_write_command_whitespace_only() -> None:
    """纯空白字符串返回 False。"""
    assert is_git_write_command("   ") is False


def test_is_git_write_command_git_alone() -> None:
    """只有 git 无子命令返回 False。"""
    assert is_git_write_command("git") is False


def test_is_git_write_command_other_write_subcommands() -> None:
    """其他写子命令（add/merge/rebase/reset/stash/pull/checkout）也被拦截。"""
    assert is_git_write_command("git add file.txt") is True
    assert is_git_write_command("git merge feature") is True
    assert is_git_write_command("git rebase main") is True
    assert is_git_write_command("git reset --hard HEAD~1") is True
    assert is_git_write_command("git stash") is True
    assert is_git_write_command("git pull") is True
    assert is_git_write_command("git checkout main") is True


def test_is_git_write_command_shlex_error_returns_false() -> None:
    """shlex.split 解析失败（不匹配的引号）安全降级返回 False。"""
    # 不匹配的引号会让 shlex.split 抛 ValueError
    assert is_git_write_command('git commit -m "unclosed quote') is False
