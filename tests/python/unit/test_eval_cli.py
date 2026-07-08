"""eval CLI 子命令单元测试（Phase 7）。

覆盖：
- ``_build_parser`` 向后兼容（``agentx "你好"`` 不走 eval 子命令）
- ``_load_suite`` 加载 YAML / 文件不存在 sys.exit(2)
- ``_list_suites`` 返回列表
- ``_cmd_run`` suite 不存在返回 2；mock 模式跑通返回 0
- ``_cmd_list`` 输出
- ``_cmd_show`` 输出
- ``run_eval_command`` 各子命令分发
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
import yaml

from app.cli import _build_eval_parser, _build_parser
from app.eval import cli as eval_cli
from app.eval.cli import (
    _cmd_list,
    _cmd_run,
    _cmd_show,
    _list_suites,
    _load_suite,
    _make_reporter,
    _ext_for,
    run_eval_command,
)
from app.eval.models import (
    CaseExpect,
    CaseResult,
    EvalCase,
    EvalResult,
    EvalSuite,
    EventAssertion,
)
from app.eval.reporters import ConsoleReporter, JsonReporter, MarkdownReporter


# ============================================================
# 辅助函数
# ============================================================


def _suite_yaml(
    *,
    sid: str = "test-smoke",
    name: str = "Test Smoke",
    description: str = "测试 suite",
) -> dict:
    """构造一个最小可用 suite 的 dict（用于写 YAML）。"""
    return {
        "id": sid,
        "name": name,
        "description": description,
        "cases": [
            {
                "id": "c1",
                "user_message": "你好",
                "agent_mode": "work",
                "expect": {
                    "events": [
                        {"type": "done", "count_min": 1}
                    ]
                },
                "timeout": 5,
                "tags": ["smoke"],
            }
        ],
    }


def _write_suite(dir_path: Path, name: str, data: dict | None = None) -> Path:
    """在 dir_path 下写 {name}.yaml suite 文件，返回文件路径。"""
    dir_path.mkdir(parents=True, exist_ok=True)
    if data is None:
        data = _suite_yaml()
    path = dir_path / f"{name}.yaml"
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True)
    return path


def _make_fake_run_router(events: list[dict[str, Any]]):
    """构造 fake run_router：yield 指定 events，忽略所有入参。"""

    async def _fake(
        message: str,
        thread_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        for ev in events:
            yield ev

    return _fake


def _make_run_args(
    *,
    suite: str = "test-smoke",
    live: bool = False,
    format: str = "console",
    no_rubric: bool = False,
) -> argparse.Namespace:
    """构造 ``_cmd_run`` 需要的 Namespace。"""
    return argparse.Namespace(
        eval_command="run",
        suite=suite,
        live=live,
        format=format,
        no_rubric=no_rubric,
    )


def _make_show_args(suite_name: str) -> argparse.Namespace:
    """构造 ``_cmd_show`` 需要的 Namespace。"""
    return argparse.Namespace(eval_command="show", suite_name=suite_name)


@pytest.fixture
def suites_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """monkeypatch ``_SUITES_DIR`` 指向 tmp_path/suites，返回该路径。"""
    d = tmp_path / "suites"
    monkeypatch.setattr(eval_cli, "_SUITES_DIR", d)
    return d


@pytest.fixture
def reports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """monkeypatch ``_REPORTS_DIR`` 指向 tmp_path/reports，返回该路径。"""
    d = tmp_path / "reports"
    monkeypatch.setattr(eval_cli, "_REPORTS_DIR", d)
    return d


# ============================================================
# 1. 向后兼容：_build_parser
# ============================================================


class TestBuildParserCompat:
    """``_build_parser`` 向后兼容测试：eval 子命令不影响原有用法。"""

    def test_message_positional_not_eval(self):
        """``agentx "你好"`` 不走 eval 子命令，_build_parser 无 command 属性。"""
        parser = _build_parser()
        args = parser.parse_args(["你好"])
        # _build_parser 不含 subparsers，args 无 command 属性
        assert getattr(args, "command", None) is None
        assert args.message == "你好"

    def test_no_args_message_is_none(self):
        """``agentx`` 无参数时 args.message 为 None。"""
        parser = _build_parser()
        args = parser.parse_args([])
        assert getattr(args, "command", None) is None
        assert args.message is None

    def test_existing_flags_still_work(self):
        """原有 --work / --json / --thread 等标志仍可用，不被 subparsers 破坏。"""
        parser = _build_parser()
        args = parser.parse_args(
            ["--work", "--json", "--thread", "abc", "hello"]
        )
        assert args.work is True
        assert args.json_mode is True
        assert args.thread == "abc"
        assert args.message == "hello"
        assert getattr(args, "command", None) is None

    def test_coding_team_with_positional_arg(self):
        """``agentx --coding-team "test"`` 位置参数不被当作 subparser choice。"""
        parser = _build_parser()
        args = parser.parse_args(["--coding-team", "test"])
        assert args.coding_team is True
        assert args.message == "test"

    def test_eval_run_subcommand(self):
        """``agentx eval run --suite=smoke`` 用 _build_eval_parser 解析。"""
        parser = _build_eval_parser()
        args = parser.parse_args(["run", "--suite=smoke"])
        assert args.eval_command == "run"
        assert args.suite == "smoke"
        assert args.live is False
        assert args.format == "console"
        assert args.no_rubric is False

    def test_eval_run_defaults(self):
        """``agentx eval run`` 默认 suite=smoke, format=console。"""
        parser = _build_eval_parser()
        args = parser.parse_args(["run"])
        assert args.eval_command == "run"
        assert args.suite == "smoke"
        assert args.format == "console"

    def test_eval_run_live_and_no_rubric(self):
        """``--live`` 和 ``--no-rubric`` 标志解析。"""
        parser = _build_eval_parser()
        args = parser.parse_args(
            ["run", "--live", "--no-rubric", "--format=md,json"]
        )
        assert args.live is True
        assert args.no_rubric is True
        assert args.format == "md,json"

    def test_eval_list_subcommand(self):
        """``agentx eval list`` 用 _build_eval_parser 解析。"""
        parser = _build_eval_parser()
        args = parser.parse_args(["list"])
        assert args.eval_command == "list"

    def test_eval_show_subcommand(self):
        """``agentx eval show smoke`` 用 _build_eval_parser 解析。"""
        parser = _build_eval_parser()
        args = parser.parse_args(["show", "smoke"])
        assert args.eval_command == "show"
        assert args.suite_name == "smoke"


# ============================================================
# 2. _load_suite
# ============================================================


class TestLoadSuite:
    """``_load_suite`` 测试。"""

    def test_load_existing_suite(self, suites_dir: Path):
        """加载存在的 suite YAML 文件。"""
        _write_suite(suites_dir, "demo")
        suite = _load_suite("demo")
        assert suite.id == "test-smoke"
        assert suite.name == "Test Smoke"
        assert len(suite.cases) == 1
        assert suite.cases[0].id == "c1"

    def test_load_suite_with_rubric(self, suites_dir: Path):
        """加载含 rubric 配置的 suite。"""
        data = {
            "id": "lj",
            "name": "LLM Judge Suite",
            "description": "test",
            "cases": [
                {
                    "id": "c1",
                    "user_message": "hi",
                    "agent_mode": "work",
                    "expect": {
                        "events": [{"type": "done", "count_min": 1}],
                        "rubric": "回复必须包含代码示例",
                    },
                }
            ],
        }
        _write_suite(suites_dir, "lj", data)
        suite = _load_suite("lj")
        assert suite.cases[0].expect.rubric is not None
        assert suite.cases[0].expect.rubric == "回复必须包含代码示例"

    def test_load_suite_not_exist_exits_2(self, suites_dir: Path):
        """suite 文件不存在时 sys.exit(2)。"""
        with pytest.raises(SystemExit) as exc_info:
            _load_suite("nonexistent")
        assert exc_info.value.code == 2


# ============================================================
# 3. _list_suites
# ============================================================


class TestListSuites:
    """``_list_suites`` 测试。"""

    def test_empty_dir_returns_empty_list(self, suites_dir: Path):
        """空目录返回空列表。"""
        suites_dir.mkdir(parents=True, exist_ok=True)
        assert _list_suites() == []

    def test_lists_suites_sorted(self, suites_dir: Path):
        """列出所有 suite 名，按字母序。"""
        _write_suite(suites_dir, "beta", {"id": "b", "name": "B", "cases": []})
        _write_suite(suites_dir, "alpha", {"id": "a", "name": "A", "cases": []})
        _write_suite(suites_dir, "gamma", {"id": "g", "name": "G", "cases": []})
        assert _list_suites() == ["alpha", "beta", "gamma"]


# ============================================================
# 4. _make_reporter / _ext_for
# ============================================================


class TestMakeReporter:
    """``_make_reporter`` 和 ``_ext_for`` 测试。"""

    def test_make_reporter_console(self):
        assert isinstance(_make_reporter("console"), ConsoleReporter)

    def test_make_reporter_md(self):
        assert isinstance(_make_reporter("md"), MarkdownReporter)

    def test_make_reporter_json(self):
        assert isinstance(_make_reporter("json"), JsonReporter)

    def test_make_reporter_unknown_raises(self):
        with pytest.raises(ValueError, match="未知 format"):
            _make_reporter("xml")

    def test_ext_for(self):
        """``md`` / ``json`` → 对应扩展名；``console`` 不入映射（永远走 print 分支）。"""
        assert _ext_for("md") == "md"
        assert _ext_for("json") == "json"
        with pytest.raises(KeyError):
            _ext_for("console")


# ============================================================
# 5. _cmd_run
# ============================================================


class TestCmdRun:
    """``_cmd_run`` 测试。"""

    def test_suite_not_exist_returns_2(self, suites_dir: Path, capsys):
        """suite 不存在时返回 2。"""
        suites_dir.mkdir(parents=True, exist_ok=True)
        args = _make_run_args(suite="nonexistent")
        assert _cmd_run(args) == 2
        captured = capsys.readouterr()
        assert "不存在" in captured.err

    def test_run_all_no_suites_returns_2(
        self, suites_dir: Path, capsys
    ):
        """``--suite=all`` 但无 suite 时返回 2。"""
        suites_dir.mkdir(parents=True, exist_ok=True)
        args = _make_run_args(suite="all")
        assert _cmd_run(args) == 2
        captured = capsys.readouterr()
        assert "未找到" in captured.err

    def test_run_mock_mode_passes(
        self,
        suites_dir: Path,
        reports_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys,
    ):
        """mock 模式跑通：case 通过 → 返回 0。"""
        _write_suite(suites_dir, "test-smoke")
        # monkeypatch run_router 返回 done 事件，满足 expect.events 断言
        monkeypatch.setattr(
            "app.router.graph.run_router",
            _make_fake_run_router([{"event": "done", "data": "{}"}]),
        )
        args = _make_run_args()
        # 确保 _FIXTURES_DIR 存在（用真实 fixtures 目录，反正 run_router 被 mock）
        # _run_suite_async 会调 MockChatModel.from_fixtures(_FIXTURES_DIR)
        assert _cmd_run(args) == 0
        captured = capsys.readouterr()
        assert "评测结果" in captured.out

    def test_run_mock_mode_with_failure_returns_1(
        self,
        suites_dir: Path,
        reports_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys,
    ):
        """case 失败（events 不满足断言）→ 返回 1。"""
        # 构造期望 done 事件，但 run_router 只返回 token 事件
        data = _suite_yaml()
        data["cases"][0]["expect"]["events"] = [
            {"type": "done", "count_min": 1}
        ]
        _write_suite(suites_dir, "test-smoke", data)
        monkeypatch.setattr(
            "app.router.graph.run_router",
            _make_fake_run_router([{"event": "token", "data": "partial"}]),
        )
        args = _make_run_args()
        assert _cmd_run(args) == 1

    def test_run_with_error_case_returns_1(
        self,
        suites_dir: Path,
        reports_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """run_router 抛异常 → case error → 返回 1。"""
        _write_suite(suites_dir, "test-smoke")

        async def _raising_run_router(message, thread_id, **kwargs):
            raise RuntimeError("boom")
            yield  # noqa: unreachable - 让函数成为 async generator

        monkeypatch.setattr(
            "app.router.graph.run_router", _raising_run_router
        )
        args = _make_run_args()
        assert _cmd_run(args) == 1

    def test_run_writes_md_and_json_reports(
        self,
        suites_dir: Path,
        reports_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys,
    ):
        """``--format=md,json`` 写入两种报告文件。"""
        _write_suite(suites_dir, "test-smoke")
        monkeypatch.setattr(
            "app.router.graph.run_router",
            _make_fake_run_router([{"event": "done", "data": "{}"}]),
        )
        args = _make_run_args(format="md,json")
        assert _cmd_run(args) == 0
        captured = capsys.readouterr()
        # 应打印两种格式报告写入信息
        assert "md 报告已写入" in captured.out
        assert "json 报告已写入" in captured.out
        # reports_dir 下应有 .md 和 .json 文件
        md_files = list(reports_dir.glob("eval-test-smoke-*.md"))
        json_files = list(reports_dir.glob("eval-test-smoke-*.json"))
        assert len(md_files) == 1
        assert len(json_files) == 1

    def test_run_all_executes_all_suites(
        self,
        suites_dir: Path,
        reports_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys,
    ):
        """``--suite=all`` 执行所有 suite。"""
        _write_suite(suites_dir, "alpha", _suite_yaml(sid="alpha", name="A"))
        _write_suite(suites_dir, "beta", _suite_yaml(sid="beta", name="B"))
        monkeypatch.setattr(
            "app.router.graph.run_router",
            _make_fake_run_router([{"event": "done", "data": "{}"}]),
        )
        args = _make_run_args(suite="all")
        assert _cmd_run(args) == 0
        captured = capsys.readouterr()
        # 应包含两个 suite 的报告标题
        assert "alpha" in captured.out
        assert "beta" in captured.out

    def test_run_no_rubric_skips_l2(
        self,
        suites_dir: Path,
        reports_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """``--no-rubric`` 跳过 L2 RubricJudge，case 仍能通过（L1 passed）。"""
        _write_suite(suites_dir, "test-smoke")
        monkeypatch.setattr(
            "app.router.graph.run_router",
            _make_fake_run_router([{"event": "done", "data": "{}"}]),
        )
        args = _make_run_args(no_rubric=True)
        assert _cmd_run(args) == 0


# ============================================================
# 6. _cmd_list
# ============================================================


class TestCmdList:
    """``_cmd_list`` 测试。"""

    def test_no_suites_prints_empty(self, suites_dir: Path, capsys):
        """无 suite 时打印提示并返回 0。"""
        suites_dir.mkdir(parents=True, exist_ok=True)
        assert _cmd_list() == 0
        captured = capsys.readouterr()
        assert "无可用 suite" in captured.out

    def test_lists_suites_with_case_count(
        self, suites_dir: Path, capsys
    ):
        """列出 suite 名和 case 数。"""
        _write_suite(suites_dir, "demo", _suite_yaml())
        assert _cmd_list() == 0
        captured = capsys.readouterr()
        assert "可用 suite" in captured.out
        assert "demo" in captured.out
        assert "1 cases" in captured.out


# ============================================================
# 7. _cmd_show
# ============================================================


class TestCmdShow:
    """``_cmd_show`` 测试。"""

    def test_show_prints_suite_details(self, suites_dir: Path, capsys):
        """打印 suite 详情和 case 列表。"""
        data = _suite_yaml()
        data["cases"][0]["expect"]["tools_called"] = ["read_file"]
        data["cases"][0]["expect"]["rubric"] = "test rubric"
        _write_suite(suites_dir, "demo", data)
        args = _make_show_args("demo")
        assert _cmd_show(args) == 0
        captured = capsys.readouterr()
        assert "Suite: test-smoke" in captured.out
        assert "Test Smoke" in captured.out
        assert "Cases (1)" in captured.out
        assert "[c1]" in captured.out
        assert "mode=work" in captured.out
        assert "tools_called" in captured.out
        assert "rubric=enabled" in captured.out

    def test_show_not_exist_exits_2(self, suites_dir: Path):
        """suite 不存在时 sys.exit(2)（由 _load_suite 抛出）。"""
        suites_dir.mkdir(parents=True, exist_ok=True)
        args = _make_show_args("nonexistent")
        with pytest.raises(SystemExit) as exc_info:
            _cmd_show(args)
        assert exc_info.value.code == 2


# ============================================================
# 8. run_eval_command 分发
# ============================================================


class TestRunEvalCommand:
    """``run_eval_command`` 分发测试。"""

    def test_dispatch_run(self, monkeypatch: pytest.MonkeyPatch):
        """eval_command=run → 调用 _cmd_run。"""
        called = {"flag": False}

        def fake_cmd_run(args):
            called["flag"] = True
            return 0

        monkeypatch.setattr(eval_cli, "_cmd_run", fake_cmd_run)
        args = argparse.Namespace(eval_command="run")
        assert run_eval_command(args) == 0
        assert called["flag"] is True

    def test_dispatch_list(self, monkeypatch: pytest.MonkeyPatch):
        """eval_command=list → 调用 _cmd_list。"""
        called = {"flag": False}

        def fake_cmd_list():
            called["flag"] = True
            return 0

        monkeypatch.setattr(eval_cli, "_cmd_list", fake_cmd_list)
        args = argparse.Namespace(eval_command="list")
        assert run_eval_command(args) == 0
        assert called["flag"] is True

    def test_dispatch_show(self, monkeypatch: pytest.MonkeyPatch):
        """eval_command=show → 调用 _cmd_show。"""
        called = {"flag": False}

        def fake_cmd_show(args):
            called["flag"] = True
            return 0

        monkeypatch.setattr(eval_cli, "_cmd_show", fake_cmd_show)
        args = argparse.Namespace(eval_command="show", suite_name="x")
        assert run_eval_command(args) == 0
        assert called["flag"] is True

    def test_unknown_subcommand_returns_2(self, capsys):
        """未知 eval_command → 返回 2。"""
        args = argparse.Namespace(eval_command="unknown")
        assert run_eval_command(args) == 2
        captured = capsys.readouterr()
        assert "未知 eval 子命令" in captured.err

    def test_none_subcommand_returns_2(self, capsys):
        """eval_command=None（未指定子命令）→ 返回 2。"""
        args = argparse.Namespace(eval_command=None)
        assert run_eval_command(args) == 2
        captured = capsys.readouterr()
        assert "用法" in captured.err


# ============================================================
# 9. 集成：_build_parser + run_eval_command 端到端
# ============================================================


class TestParserAndDispatchIntegration:
    """``_build_eval_parser`` + ``run_eval_command`` 端到端集成测试。"""

    def test_full_flow_run(
        self,
        suites_dir: Path,
        reports_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """``agentx eval run`` 完整流程：解析 → 分发 → 执行 → 返回 0。"""
        _write_suite(suites_dir, "test-smoke")
        monkeypatch.setattr(
            "app.router.graph.run_router",
            _make_fake_run_router([{"event": "done", "data": "{}"}]),
        )
        parser = _build_eval_parser()
        args = parser.parse_args(["run", "--suite=test-smoke"])
        assert run_eval_command(args) == 0

    def test_full_flow_list(
        self, suites_dir: Path, capsys
    ):
        """``agentx eval list`` 完整流程：解析 → 分发 → 列出。"""
        _write_suite(suites_dir, "demo", _suite_yaml())
        parser = _build_eval_parser()
        args = parser.parse_args(["list"])
        assert run_eval_command(args) == 0
        captured = capsys.readouterr()
        assert "demo" in captured.out

    def test_full_flow_show(self, suites_dir: Path, capsys):
        """``agentx eval show demo`` 完整流程：解析 → 分发 → 显示。"""
        _write_suite(suites_dir, "demo", _suite_yaml())
        parser = _build_eval_parser()
        args = parser.parse_args(["show", "demo"])
        assert run_eval_command(args) == 0
        captured = capsys.readouterr()
        assert "Suite: test-smoke" in captured.out
