"""pytest 插件：将 tests/eval/suites/*.yaml 自动收集为测试用例。

通过 pyproject.toml 的 ``[project.entry-points.pytest11]`` 注册，pytest 自动加载。

收集流程：
1. ``pytest_collect_file`` 对路径含 ``suites`` 的 ``*.yaml`` 生成 ``EvalSuiteFile``
2. ``EvalSuiteFile.collect`` 解析 YAML 为 ``EvalSuite``，按 ``--suite`` 过滤后逐 case
   生成 ``EvalItem``
3. ``EvalItem.runtest`` 调 ``EvalRunner.run_case`` + ``JudgeChain``，失败时
   ``raise AssertionError`` 并打印 JudgeResult 详情

模式切换：
- 默认 mock 模式：``MockChatModel`` + ``RubricJudge(no_rubric=True)``（跳过 L2/L3）
- ``-m live``：``chat_model=None``（真实 LLM）+ ``RubricJudge()``（真实 L2，无 key 仍降级）
- ``--no-rubric``：与 mock 模式一致，L3 自纠分支不触发（避免无 API key 时构造
  ``RubricMiddleware`` 失败）
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.eval.judges import AssertJudge, JudgeChain, RubricJudge
from app.eval.mocks.llm import MockChatModel
from app.eval.models import EvalCase, EvalSuite
from app.eval.runner import EvalRunner

__all__ = [
    "EvalSuiteFile",
    "EvalItem",
    "pytest_addoption",
    "pytest_collect_file",
    "pytest_configure",
    "pytest_runtest_call",
]


def pytest_addoption(parser: pytest.Parser) -> None:
    """添加 ``--suite`` 命令行选项。"""
    group = parser.getgroup("agentx-eval")
    group.addoption(
        "--suite",
        action="store",
        default=None,
        help="只运行指定 suite（id，逗号分隔多个）",
    )


def pytest_configure(config: pytest.Config) -> None:
    """注册 ``live`` marker，避免 unknown marker 警告。"""
    config.addinivalue_line(
        "markers",
        "live: eval case 使用真实 LLM（live 模式），默认为 mock 模式",
    )


def pytest_collect_file(parent: pytest.Collector, file_path: Path):
    """收集路径含 ``suites`` 的 ``*.yaml`` 为 ``EvalSuiteFile``。"""
    if file_path.suffix != ".yaml":
        return None
    if "suites" not in str(file_path):
        return None
    return EvalSuiteFile.from_parent(parent, path=file_path)


class EvalSuiteFile(pytest.File):
    """单个 YAML suite 文件，解析为 ``EvalSuite`` 并按 case 生成 ``EvalItem``。"""

    def collect(self):
        with self.path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        suite = EvalSuite(**data)

        # --suite 过滤：未在白名单中的 suite 不 yield 任何 item
        suite_filter = self.config.getoption("--suite", default=None)
        if suite_filter:
            allowed = {s.strip() for s in suite_filter.split(",")}
            if suite.id not in allowed:
                return

        for case in suite.cases:
            yield EvalItem.from_parent(
                self, name=case.id, case=case, suite_id=suite.id
            )


class EvalItem(pytest.Item):
    """单个 case 作为 pytest 测试项，``runtest`` 执行 case 并评分。"""

    def __init__(self, *, case: EvalCase, suite_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.case = case
        self.suite_id = suite_id

    async def runtest(self) -> None:
        """执行 case + 评分。失败时 ``raise AssertionError`` 并打印详情。

        注意：本方法是 async，但 pytest 不会自动 await 自定义 ``pytest.Item``
        子类的 ``runtest``（``asyncio_mode=auto`` 仅处理 ``test_*`` 函数）。
        ``pytest_runtest_call`` hook 负责驱动本协程。
        """
        mark_expr = self.config.getoption("-m", default="") or ""
        is_live = "live" in mark_expr

        if is_live:
            # live 模式：用真实 LLM，RubricJudge 正常执行（无 key 时自动降级 skipped）
            chat_model = None
            judges = [AssertJudge(), RubricJudge()]
            runner = EvalRunner(chat_model=chat_model)
        else:
            # mock 模式：MockChatModel + 跳过 L2/L3（no_rubric=True 避免 RubricMiddleware 构造失败）
            fixtures_dir = Path(__file__).resolve().parent / "mocks" / "fixtures"
            chat_model = MockChatModel.from_fixtures(fixtures_dir)
            judges = [AssertJudge(), RubricJudge(no_rubric=True)]
            runner = EvalRunner(chat_model=chat_model, no_rubric=True)
        composite = JudgeChain(judges)

        async def _run() -> tuple:
            case_result = await runner.run_case(self.case)
            judge_results = await composite.evaluate(case_result.events, self.case)
            return case_result, judge_results

        case_result, judge_results = await _run()
        case_result = EvalRunner.apply_judge_results(case_result, judge_results)

        if not case_result.passed:
            details = []
            if case_result.error:
                details.append(f"error: {case_result.error}")
            for jr in case_result.judge_results:
                details.append(
                    f"[{jr.layer}] passed={jr.passed} score={jr.score:.2f} "
                    f"reason={jr.reason}"
                )
            raise AssertionError(
                f"Case '{self.case.id}' failed:\n" + "\n".join(details)
            )

    def reportinfo(self):
        return self.path, 0, f"[{self.suite_id}] {self.case.id}"


def pytest_runtest_call(item: pytest.Item) -> None:
    """驱动 ``EvalItem.runtest`` async 协程。

    pytest ``asyncio_mode=auto`` 仅自动 await ``test_*`` 函数，不处理自定义
    ``pytest.Item`` 子类的 ``runtest``。本 hook 检测 item 是否为 ``EvalItem``
    且 ``runtest`` 返回 coroutine，若是则用事件循环驱动至完成。
    """
    if not isinstance(item, EvalItem):
        return
    import asyncio

    result = item.runtest()
    if asyncio.iscoroutine(result):
        asyncio.run(result)
