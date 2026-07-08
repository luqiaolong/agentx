"""eval CLI 子命令：``agentx eval run|list|show``。

由 ``app.cli.main`` 在 ``args.command == "eval"`` 时调用 ``run_eval_command`` 分发。
退出码（FR-7.8）：全部通过=0，有失败=1，suite 不存在=2。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import yaml

from app.eval.judges import AssertJudge, RubricJudge
from app.eval.mocks.llm import MockChatModel
from app.eval.models import EvalResult, EvalSuite
from app.eval.reporters import ConsoleReporter, JsonReporter, MarkdownReporter
from app.eval.runner import EvalRunner

# suites 目录：worktree root 下的 tests/eval/suites/
# __file__ = backend/app/eval/cli.py → parents[3] = worktree root
_SUITES_DIR = Path(__file__).resolve().parents[3] / "tests" / "eval" / "suites"
# mock fixtures 目录：backend/app/eval/mocks/fixtures/
_FIXTURES_DIR = Path(__file__).resolve().parent / "mocks" / "fixtures"
# 报告输出目录：worktree root 下的 data/eval/reports/
_REPORTS_DIR = Path(__file__).resolve().parents[3] / "data" / "eval" / "reports"


def _load_suite(name: str) -> EvalSuite:
    """从 ``tests/eval/suites/{name}.yaml`` 加载 ``EvalSuite``。

    文件不存在时打印错误并 ``sys.exit(2)``（FR-7.8 suite 不存在=2）。
    """
    path = _SUITES_DIR / f"{name}.yaml"
    if not path.exists():
        print(f"错误：suite '{name}' 不存在（路径：{path}）", file=sys.stderr)
        sys.exit(2)
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return EvalSuite(**data)


def _list_suites() -> list[str]:
    """列出所有可用 suite 名（按字母序）；目录不存在时返回空列表。"""
    if not _SUITES_DIR.exists():
        return []
    return sorted(p.stem for p in _SUITES_DIR.glob("*.yaml"))


def _make_reporter(format_name: str):
    """按格式名构造 Reporter 实例。"""
    if format_name == "console":
        return ConsoleReporter()
    if format_name == "md":
        return MarkdownReporter()
    if format_name == "json":
        return JsonReporter()
    raise ValueError(
        f"未知 format: {format_name!r}，合法值为 console/md/json"
    )


def _ext_for(format_name: str) -> str:
    """``md`` / ``json`` → 报告文件扩展名。

    ``console`` 永远走 ``print(content)`` 不会进此函数，因此不映射。
    """
    return {"md": "md", "json": "json"}[format_name]


async def _run_suite_async(
    suite: EvalSuite, live: bool, no_rubric: bool
) -> EvalResult:
    """异步执行 suite + Judge 打分，返回回填后的 ``EvalResult``。

    - ``live=True`` 时 chat_model=None（用真实 LLM）；否则用 ``MockChatModel``
    - ``no_rubric=True`` 时只跑 L1 ``AssertJudge``，跳过 L2 ``RubricJudge``，
      且 L3 自纠分支不触发
    - 评分逻辑已合并到 ``EvalRunner.run_suite(judges=...)`` 内部，
      本函数不再二次循环 ``case_results``
    """
    chat_model = None if live else MockChatModel.from_fixtures(_FIXTURES_DIR)
    judges: list = [AssertJudge()]
    if not no_rubric:
        judges.append(RubricJudge(no_rubric=no_rubric))

    runner = EvalRunner(chat_model=chat_model, no_rubric=no_rubric)
    return await runner.run_suite(suite, judges=judges)


async def _run_all_suites_async(
    suites: list[tuple[str, EvalSuite]], live: bool, no_rubric: bool
) -> list[tuple[str, EvalResult]]:
    """在单个 event loop 中顺序执行所有 suite，避免 checkpointer 跨 event loop 问题。

    ``get_async_checkpointer()`` 返回单例，其内部 asyncio.Lock 绑定到首次调用的
    event loop。若每个 suite 用独立 ``asyncio.run()``，后续 suite 会因 lock 跨
    event loop 报 RuntimeError。本函数把所有 suite 放在同一个 ``asyncio.run()``
    中执行，确保 lock 始终在同一个 event loop 上。
    """
    results: list[tuple[str, EvalResult]] = []
    for name, suite in suites:
        eval_result = await _run_suite_async(
            suite, live=live, no_rubric=no_rubric
        )
        results.append((name, eval_result))
    return results


def _cmd_run(args: argparse.Namespace) -> int:
    """执行 ``agentx eval run``。返回退出码（0=全过，1=有失败，2=suite 不存在）。"""
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    if not formats:
        formats = ["console"]

    # 解析要跑的 suite 列表
    if args.suite == "all":
        suite_names = _list_suites()
        if not suite_names:
            print("错误：未找到任何 suite", file=sys.stderr)
            return 2
    else:
        path = _SUITES_DIR / f"{args.suite}.yaml"
        if not path.exists():
            print(f"错误：suite '{args.suite}' 不存在", file=sys.stderr)
            return 2
        suite_names = [args.suite]

    # 加载所有 suite
    suites: list[tuple[str, EvalSuite]] = []
    for name in suite_names:
        suites.append((name, _load_suite(name)))

    # 在单个 asyncio.run() 中执行所有 suite，避免 checkpointer 跨 event loop 问题
    all_results = asyncio.run(
        _run_all_suites_async(suites, live=args.live, no_rubric=args.no_rubric)
    )

    overall_passed = True
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for name, eval_result in all_results:
        # 渲染每种格式
        for fmt in formats:
            reporter = _make_reporter(fmt)
            content = reporter.render(eval_result)
            if fmt == "console":
                print(content)
            else:
                report_path = _REPORTS_DIR / f"eval-{name}-{timestamp}.{_ext_for(fmt)}"
                report_path.write_text(content, encoding="utf-8")
                print(f"[{name}] {fmt} 报告已写入：{report_path}")

        if not all(cr.passed for cr in eval_result.case_results):
            overall_passed = False

    return 0 if overall_passed else 1


def _cmd_list() -> int:
    """执行 ``agentx eval list``，列出所有可用 suite。"""
    names = _list_suites()
    if not names:
        print("（无可用 suite）")
        return 0
    print("可用 suite：")
    for n in names:
        try:
            suite = _load_suite(n)
            print(f"  - {n}: {suite.name}（{len(suite.cases)} cases）")
        except SystemExit:
            # _load_suite 在不存在时会 sys.exit(2)，这里已经被 _list_suites 列出
            # 理论上不会触发，但防御性处理
            print(f"  - {n}: 加载失败")
        except Exception as exc:  # noqa: BLE001
            print(f"  - {n}: 加载失败 - {exc}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    """执行 ``agentx eval show <name>``，打印 suite 的 case 列表。"""
    suite = _load_suite(args.suite_name)
    print(f"Suite: {suite.id} — {suite.name}")
    print(f"描述: {suite.description}")
    print(f"Cases ({len(suite.cases)}):")
    for i, c in enumerate(suite.cases, 1):
        print(f"  {i}. [{c.id}] mode={c.agent_mode} tags={c.tags}")
        preview = c.user_message[:80] + ("..." if len(c.user_message) > 80 else "")
        print(f"     user_message: {preview}")
        expect = c.expect
        parts: list[str] = []
        if expect.events:
            parts.append(f"events={len(expect.events)}")
        if expect.tools_called:
            parts.append(f"tools_called={expect.tools_called}")
        if expect.tools_not_called:
            parts.append(f"tools_not_called={expect.tools_not_called}")
        if expect.assertions:
            parts.append(f"assertions={len(expect.assertions)}")
        if expect.rubric:
            parts.append("rubric=enabled")
        if expect.self_correct:
            parts.append("self_correct=enabled")
        if parts:
            print(f"     expect: {', '.join(parts)}")
    return 0


def run_eval_command(args: argparse.Namespace) -> int:
    """eval 子命令分发入口（由 ``app.cli.main`` 调用）。

    根据 ``args.eval_command`` 分发到 ``_cmd_run`` / ``_cmd_list`` / ``_cmd_show``。
    未指定子命令时打印用法并返回 2。
    """
    if args.eval_command == "run":
        return _cmd_run(args)
    if args.eval_command == "list":
        return _cmd_list()
    if args.eval_command == "show":
        return _cmd_show(args)
    print(f"错误：未知 eval 子命令 '{args.eval_command}'", file=sys.stderr)
    print("用法：agentx eval run|list|show ...", file=sys.stderr)
    return 2
