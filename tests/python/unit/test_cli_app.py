"""app.cli.app 模块单元测试。

覆盖：
- _build_parser：参数解析
- _resolve_agent_mode：agent_mode 解析
- _read_stdin_if_piped：管道输入检测
- _handle_config_subcommand：config 子命令分发
- _config_show / _config_get / _config_set：配置 CRUD
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch


from app.cli.app import (
    _build_parser,
    _config_get,
    _config_set,
    _config_show,
    _handle_config_subcommand,
    _read_stdin_if_piped,
    _resolve_agent_mode,
)


# ============================================================
# 参数解析测试
# ============================================================

class TestBuildParser:
    """命令行参数解析。"""

    def test_no_args_defaults_to_coding(self):
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.message is None
        assert args.work is False
        assert args.coding is False
        assert args.coding_team is False

    def test_message_positional(self):
        parser = _build_parser()
        args = parser.parse_args(["hello world"])
        assert args.message == "hello world"

    def test_work_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["--work", "test"])
        assert args.work is True

    def test_coding_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["--coding", "test"])
        assert args.coding is True

    def test_coding_team_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["--coding-team", "test"])
        assert args.coding_team is True

    def test_verbose_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["-v"])
        assert args.verbose is True

    def test_json_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["--json"])
        assert args.json_mode is True

    def test_thread_option(self):
        parser = _build_parser()
        args = parser.parse_args(["--thread", "abc123"])
        assert args.thread == "abc123"

    def test_workspace_option(self):
        parser = _build_parser()
        args = parser.parse_args(["-w", "/tmp/project"])
        assert args.workspace == "/tmp/project"


# ============================================================
# _resolve_agent_mode 测试
# ============================================================

class TestResolveAgentMode:
    """agent_mode 解析。"""

    def test_default_is_coding(self):
        args = argparse.Namespace(work=False, coding=False, coding_team=False)
        assert _resolve_agent_mode(args) == "coding"

    def test_work_flag(self):
        args = argparse.Namespace(work=True, coding=False, coding_team=False)
        assert _resolve_agent_mode(args) == "work"

    def test_coding_team_flag(self):
        args = argparse.Namespace(work=False, coding=False, coding_team=True)
        assert _resolve_agent_mode(args) == "coding_team"

    def test_work_overrides_coding(self):
        """--work 优先于 --coding。"""
        args = argparse.Namespace(work=True, coding=True, coding_team=False)
        assert _resolve_agent_mode(args) == "work"

    def test_coding_team_overrides_coding(self):
        """--coding-team 优先于 --coding。"""
        args = argparse.Namespace(work=False, coding=True, coding_team=True)
        assert _resolve_agent_mode(args) == "coding_team"


# ============================================================
# _read_stdin_if_piped 测试
# ============================================================

class TestReadStdinIfPiped:
    """管道输入检测。"""

    def test_tty_returns_none(self):
        with patch("sys.stdin.isatty", return_value=True):
            assert _read_stdin_if_piped() is None

    def test_piped_returns_content(self):
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        mock_stdin.read.return_value = "piped content\n"
        with patch("sys.stdin", mock_stdin):
            result = _read_stdin_if_piped()
            assert result == "piped content"

    def test_piped_strips_whitespace(self):
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        mock_stdin.read.return_value = "  trimmed content  \n"
        with patch("sys.stdin", mock_stdin):
            assert _read_stdin_if_piped() == "trimmed content"

    def test_piped_read_exception_returns_none(self):
        """读取异常返回 None。"""
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        mock_stdin.read.side_effect = OSError("read failed")
        with patch("sys.stdin", mock_stdin):
            assert _read_stdin_if_piped() is None


# ============================================================
# _handle_config_subcommand 测试
# ============================================================

class TestHandleConfigSubcommand:
    """config 子命令分发。"""

    def test_no_subcommand_returns_1(self, capsys):
        """未提供子命令返回 1。"""
        rc = _handle_config_subcommand(["config"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "用法" in out

    def test_unknown_subcommand_returns_1(self, capsys):
        rc = _handle_config_subcommand(["config", "bogus"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "未知子命令" in out

    def test_show_dispatch(self):
        """config show 调用 _config_show。"""
        with patch("app.cli.app._config_show", return_value=0) as mock_show:
            rc = _handle_config_subcommand(["config", "show"])
            assert rc == 0
            mock_show.assert_called_once()

    def test_get_dispatch(self):
        """config get <key> 调用 _config_get。"""
        with patch("app.cli.app._config_get", return_value=0) as mock_get:
            rc = _handle_config_subcommand(["config", "get", "model"])
            assert rc == 0
            mock_get.assert_called_once_with("model")

    def test_get_missing_key_returns_1(self, capsys):
        """config get 不带 key 返回 1。"""
        rc = _handle_config_subcommand(["config", "get"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "用法" in out

    def test_set_dispatch(self):
        """config set <key> <value> 调用 _config_set。"""
        with patch("app.cli.app._config_set", return_value=0) as mock_set:
            rc = _handle_config_subcommand(["config", "set", "model", "gpt-4o"])
            assert rc == 0
            mock_set.assert_called_once_with("model", "gpt-4o")

    def test_set_missing_value_returns_1(self, capsys):
        """config set 不带 value 返回 1。"""
        rc = _handle_config_subcommand(["config", "set", "model"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "用法" in out


# ============================================================
# _config_show 测试
# ============================================================

class TestConfigShow:
    """config show 实现。"""

    def test_no_config_returns_1(self, capsys):
        """无 config.json 返回 1。"""
        with patch("app.cli.store.read_config_json", return_value=None):
            rc = _config_show()
            assert rc == 1
            out = capsys.readouterr().out
            assert "无法读取" in out

    def test_show_dumps_json(self, capsys):
        """有配置时输出 JSON。"""
        config = {"llm": {"defaultModel": "test-model"}}
        with patch("app.cli.store.read_config_json", return_value=config):
            rc = _config_show()
            assert rc == 0
            out = capsys.readouterr().out
            parsed = json.loads(out)
            assert parsed == config


# ============================================================
# _config_get 测试
# ============================================================

class TestConfigGet:
    """config get <key> 实现。"""

    def test_no_config_returns_1(self, capsys):
        with patch("app.cli.store.read_config_json", return_value=None):
            rc = _config_get("llm.defaultModel")
            assert rc == 1

    def test_top_level_key(self, capsys):
        config = {"systemPrompt": "hello"}
        with patch("app.cli.store.read_config_json", return_value=config):
            rc = _config_get("systemPrompt")
            assert rc == 0
            out = capsys.readouterr().out.strip()
            assert out == "hello"

    def test_nested_key(self, capsys):
        config = {"llm": {"defaultModel": "deepseek-chat"}}
        with patch("app.cli.store.read_config_json", return_value=config):
            rc = _config_get("llm.defaultModel")
            assert rc == 0
            out = capsys.readouterr().out
            assert "deepseek-chat" in out

    def test_missing_key_returns_1(self, capsys):
        config = {"llm": {"defaultModel": "x"}}
        with patch("app.cli.store.read_config_json", return_value=config):
            rc = _config_get("nonexistent.key")
            assert rc == 1
            out = capsys.readouterr().out
            assert "未找到" in out

    def test_dict_value_printed_as_json(self, capsys):
        config = {"llm": {"defaultModel": "m1", "openaiBaseUrl": "url"}}
        with patch("app.cli.store.read_config_json", return_value=config):
            rc = _config_get("llm")
            assert rc == 0
            out = capsys.readouterr().out
            parsed = json.loads(out)
            assert parsed == config["llm"]


# ============================================================
# _config_set 测试
# ============================================================

class TestConfigSet:
    """config set <key> <value> 实现。"""

    def test_set_new_key_in_existing_config(self, tmp_path: Path, capsys):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"existing": True}), encoding="utf-8")

        with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
            rc = _config_set("llm.defaultModel", "gpt-4o")
            assert rc == 0

        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["existing"] is True
        assert saved["llm"]["defaultModel"] == "gpt-4o"

    def test_set_creates_config_if_missing(self, tmp_path: Path, capsys):
        config_path = tmp_path / "config.json"
        assert not config_path.exists()

        with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
            rc = _config_set("key", "value")
            assert rc == 0

        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["key"] == "value"

    def test_set_parses_bool_true(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
            rc = _config_set("flag", "true")
            assert rc == 0
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["flag"] is True

    def test_set_parses_bool_false(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
            rc = _config_set("flag", "false")
            assert rc == 0
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["flag"] is False

    def test_set_parses_int(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
            rc = _config_set("count", "42")
            assert rc == 0
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["count"] == 42

    def test_set_parses_float(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
            rc = _config_set("ratio", "3.14")
            assert rc == 0
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["ratio"] == 3.14

    def test_set_keeps_string_when_unparseable(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
            rc = _config_set("model", "gpt-4o-mini")
            assert rc == 0
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["model"] == "gpt-4o-mini"

    def test_set_overwrites_existing_value(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"llm": {"defaultModel": "old"}}), encoding="utf-8")
        with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
            rc = _config_set("llm.defaultModel", "new-model")
            assert rc == 0
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["llm"]["defaultModel"] == "new-model"

    def test_set_no_candidate_paths_returns_1(self, capsys):
        """候选路径为空时返回 1。"""
        with patch("app.cli.store._candidate_config_paths", return_value=[]):
            rc = _config_set("key", "value")
            assert rc == 1
            out = capsys.readouterr().out
            assert "无法确定配置文件路径" in out
