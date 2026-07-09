"""eval pytest 插件单元测试。

覆盖（spec.md FR-8 + design.md §9）：
1. ``pytest_collect_file`` 路由：.py / 非 suites YAML / suites YAML
2. ``EvalSuiteFile.collect`` 生成 ``EvalItem`` 数量正确
3. ``--suite`` 过滤：白名单外 suite 不 yield item
4. ``EvalItem.runtest`` 通过 / 失败 / error case
5. ``EvalItem.reportinfo`` 格式
6. mock 模式用 ``MockChatModel``、live 模式用 ``chat_model=None``
7. pytester 集成：YAML suite 自动收集 + ``--suite`` 过滤

``run_router`` 通过 ``monkeypatch`` 替换为 fake 异步生成器，不连真实 LLM / 沙箱。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.eval.mocks.llm import MockChatModel
from app.eval.models import CaseExpect, EvalCase, EvalSuite, EventAssertion
from app.eval.pytest_plugin import (
    EvalItem,
    EvalSuiteFile,
    pytest_collect_file,
)


# ============================================================
# 辅助函数
# ============================================================


def _suite(
    *,
    sid: str = "s1",
    cases: list[EvalCase] | None = None,
) -> EvalSuite:
    """构造测试用 EvalSuite。"""
    if cases is None:
        cases = [
            EvalCase(id="c1", user_message="hi", agent_mode="work", expect=CaseExpect()),
        ]
    return EvalSuite(id=sid, name="test suite", description="test", cases=cases)


def _write_suite_yaml(tmp_path: Path, suite: EvalSuite, filename: str = "test.yaml") -> Path:
    """把 EvalSuite 序列化为 YAML 写到 ``tmp_path/suites/<filename>``。"""
    suites_dir = tmp_path / "suites"
    suites_dir.mkdir(exist_ok=True)
    yaml_file = suites_dir / filename
    yaml_file.write_text(
        yaml.dump(suite.model_dump(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return yaml_file


def _collect_items(request: pytest.FixtureRequest, yaml_file: Path) -> list[EvalItem]:
    """调 ``pytest_collect_file`` + ``collect`` 返回所有 EvalItem。"""
    suite_file = pytest_collect_file(request.session, yaml_file)
    assert suite_file is not None, "pytest_collect_file 应返回 EvalSuiteFile"
    return list(suite_file.collect())


def _make_fake_run_router(
    events: list[dict[str, str]] | None = None,
    captured: dict | None = None,
    raise_exc: Exception | None = None,
):
    """构造 fake ``run_router``：yield 指定 events，可记录入参，可抛异常。"""

    async def _fake_run_router(**kwargs: Any):
        if captured is not None:
            captured.update(kwargs)
        if raise_exc is not None:
            raise raise_exc
        for ev in (events or []):
            yield ev

    return _fake_run_router


# ============================================================
# 1. pytest_collect_file 路由
# ============================================================


def test_collect_file_returns_none_for_py_file() -> None:
    """.py 文件不被收集。"""
    assert pytest_collect_file(None, Path("tests/test_foo.py")) is None


def test_collect_file_returns_none_for_non_yaml() -> None:
    """非 .yaml 文件（即使在 suites 目录）不被收集。"""
    assert pytest_collect_file(None, Path("suites/foo.txt")) is None


def test_collect_file_returns_none_for_non_suites_yaml() -> None:
    """不在 suites 目录的 YAML 不被收集。"""
    assert pytest_collect_file(None, Path("data/config.yaml")) is None


def test_collect_file_returns_suite_file_for_suites_yaml(
    request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    """suites 目录下的 YAML 返回 EvalSuiteFile。"""
    yaml_file = _write_suite_yaml(tmp_path, _suite())
    result = pytest_collect_file(request.session, yaml_file)
    assert isinstance(result, EvalSuiteFile)


# ============================================================
# 2. EvalSuiteFile.collect 生成 EvalItem
# ============================================================


def test_suite_file_collect_yields_correct_items(
    request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    """collect 生成与 cases 数量一致的 EvalItem。"""
    suite = _suite(
        cases=[
            EvalCase(id="c1", user_message="a", agent_mode="work", expect=CaseExpect()),
            EvalCase(id="c2", user_message="b", agent_mode="work", expect=CaseExpect()),
        ]
    )
    yaml_file = _write_suite_yaml(tmp_path, suite)
    items = _collect_items(request, yaml_file)

    assert len(items) == 2
    assert all(isinstance(i, EvalItem) for i in items)
    assert [i.case.id for i in items] == ["c1", "c2"]
    assert all(i.suite_id == "s1" for i in items)


def test_suite_file_collect_no_filter_yields_all(
    request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    """无 --suite 过滤时 yield 所有 case。"""
    yaml_file = _write_suite_yaml(tmp_path, _suite())
    items = _collect_items(request, yaml_file)
    assert len(items) == 1


# ============================================================
# 3. --suite 过滤
# ============================================================


def test_suite_file_collect_filters_by_suite_option(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--suite 指定其他 suite id 时，当前 suite 不 yield 任何 item。"""
    yaml_file = _write_suite_yaml(tmp_path, _suite(sid="s1"))
    # 模拟 --suite=other
    monkeypatch.setattr(request.config.option, "suite", "other")

    suite_file = pytest_collect_file(request.session, yaml_file)
    items = list(suite_file.collect())
    assert items == []


def test_suite_file_collect_suite_option_matches(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--suite 指定当前 suite id 时，正常 yield item。"""
    yaml_file = _write_suite_yaml(tmp_path, _suite(sid="s1"))
    monkeypatch.setattr(request.config.option, "suite", "s1")

    suite_file = pytest_collect_file(request.session, yaml_file)
    items = list(suite_file.collect())
    assert len(items) == 1


def test_suite_file_collect_suite_option_multiple(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--suite 支持逗号分隔多个 id。"""
    yaml_file = _write_suite_yaml(tmp_path, _suite(sid="s1"))
    monkeypatch.setattr(request.config.option, "suite", "s2,s1,s3")

    suite_file = pytest_collect_file(request.session, yaml_file)
    items = list(suite_file.collect())
    assert len(items) == 1


# ============================================================
# 4. EvalItem.runtest 通过 / 失败 / error
# ============================================================


async def test_runtest_pass_does_not_raise(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case 通过时 runtest 不抛异常（mock 模式 + 空 expect）。"""
    monkeypatch.setattr(
        "app.router.graph.run_router",
        _make_fake_run_router([{"event": "done", "data": "{}"}]),
    )
    yaml_file = _write_suite_yaml(
        tmp_path,
        _suite(
            cases=[
                EvalCase(id="c-pass", user_message="hi", agent_mode="work", expect=CaseExpect()),
            ]
        ),
    )
    items = _collect_items(request, yaml_file)
    # 不抛异常即通过
    await items[0].runtest()


async def test_runtest_fail_raises_assertion_error(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L1 断言失败时 runtest 抛 AssertionError。"""
    monkeypatch.setattr(
        "app.router.graph.run_router",
        _make_fake_run_router([{"event": "done", "data": "{}"}]),
    )
    # expect 要求 token 事件，但 fake 只 yield done
    expect = CaseExpect(events=[EventAssertion(type="token", count_min=1)])
    yaml_file = _write_suite_yaml(
        tmp_path,
        _suite(
            cases=[
                EvalCase(id="c-fail", user_message="hi", agent_mode="work", expect=expect),
            ]
        ),
    )
    items = _collect_items(request, yaml_file)

    with pytest.raises(AssertionError, match="c-fail"):
        await items[0].runtest()


async def test_runtest_error_case_raises_assertion_error(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_router 抛异常 → CaseResult.error → runtest 抛 AssertionError。"""
    monkeypatch.setattr(
        "app.router.graph.run_router",
        _make_fake_run_router(raise_exc=RuntimeError("boom")),
    )
    yaml_file = _write_suite_yaml(tmp_path, _suite())
    items = _collect_items(request, yaml_file)

    with pytest.raises(AssertionError, match="boom"):
        await items[0].runtest()


# ============================================================
# 5. EvalItem.reportinfo
# ============================================================


def test_reportinfo_returns_correct_format(
    request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    """reportinfo 返回 (path, 0, '[suite_id] case_id')。"""
    yaml_file = _write_suite_yaml(
        tmp_path,
        _suite(
            cases=[
                EvalCase(id="c1", user_message="hi", agent_mode="work", expect=CaseExpect()),
            ]
        ),
    )
    items = _collect_items(request, yaml_file)

    path, lineno, msg = items[0].reportinfo()
    assert path == yaml_file
    assert lineno == 0
    assert msg == "[s1] c1"


# ============================================================
# 6. mock / live 模式 chat_model 切换
# ============================================================


async def test_mock_mode_uses_mock_chat_model(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认 mock 模式：chat_model 为 MockChatModel 实例。"""
    captured: dict = {}
    monkeypatch.setattr(
        "app.router.graph.run_router",
        _make_fake_run_router([{"event": "done", "data": "{}"}], captured=captured),
    )
    yaml_file = _write_suite_yaml(tmp_path, _suite())
    items = _collect_items(request, yaml_file)
    await items[0].runtest()

    assert isinstance(captured.get("chat_model"), MockChatModel)


async def test_live_mode_uses_no_chat_model(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """-m live 模式：chat_model 为 None（真实 LLM）。"""
    captured: dict = {}
    monkeypatch.setattr(
        "app.router.graph.run_router",
        _make_fake_run_router([{"event": "done", "data": "{}"}], captured=captured),
    )
    # 模拟 -m live（pytest -m 的 dest 为 markexpr）
    monkeypatch.setattr(request.config.option, "markexpr", "live")

    yaml_file = _write_suite_yaml(tmp_path, _suite())
    items = _collect_items(request, yaml_file)
    await items[0].runtest()

    assert captured.get("chat_model") is None


# ============================================================
# 7. pytester 集成测试
# ============================================================


def test_pytester_collects_yaml_suite(pytester: pytest.Pytester) -> None:
    """pytester 集成：suites/*.yaml 自动收集为 EvalItem。"""
    suites_dir = pytester.path / "suites"
    suites_dir.mkdir()
    (suites_dir / "test.yaml").write_text(
        "id: s1\n"
        "name: integration\n"
        "cases:\n"
        "  - id: c1\n"
        "    user_message: hi\n"
        "    agent_mode: work\n"
        "  - id: c2\n"
        "    user_message: hello\n"
        "    agent_mode: work\n",
        encoding="utf-8",
    )

    result = pytester.runpytest("suites", "--collect-only", "-q")
    result.assert_outcomes()
    output = result.stdout.str()
    assert "c1" in output
    assert "c2" in output


def test_pytester_suite_filter(pytester: pytest.Pytester) -> None:
    """pytester 集成：--suite 过滤只收集匹配的 suite。"""
    suites_dir = pytester.path / "suites"
    suites_dir.mkdir()
    (suites_dir / "foo.yaml").write_text(
        "id: foo\nname: f\ncases:\n  - id: fc1\n    user_message: hi\n    agent_mode: work\n",
        encoding="utf-8",
    )
    (suites_dir / "bar.yaml").write_text(
        "id: bar\nname: b\ncases:\n  - id: bc1\n    user_message: hi\n    agent_mode: work\n",
        encoding="utf-8",
    )

    result = pytester.runpytest("suites", "--suite=foo", "--collect-only", "-q")
    result.assert_outcomes()
    output = result.stdout.str()
    assert "fc1" in output
    assert "bc1" not in output
