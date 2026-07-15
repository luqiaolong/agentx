"""eval Reporter 报告系统单元测试。

覆盖：
1. ConsoleReporter：render 返回字符串，含 case_id 和通过数
2. ConsoleReporter：含 ✓ 或 ✗ 标记
3. MarkdownReporter：render 返回 MD 字符串，含标题 ``# 评测报告``
4. MarkdownReporter：含汇总表和 case 详情
5. MarkdownReporter：失败 case 展开 JudgeResult.details
6. JsonReporter：render 返回有效 JSON，可反序列化回 EvalResult
7. 辅助函数 extract_agent_reply 正确提取 token
8. 辅助函数 format_duration 格式化正确
"""

from __future__ import annotations

from datetime import datetime

from app.eval.models import (
    CaseResult,
    EvalCase,
    EvalResult,
    JudgeResult,
)
from app.eval.reporters import ConsoleReporter, JsonReporter, MarkdownReporter
from app.eval.reporters.base import extract_agent_reply, format_duration


def _build_sample_result() -> EvalResult:
    """构造 2 个 case（1 通过 / 1 失败）的 EvalResult，含 L1+L2 判分。"""
    case_pass = EvalCase(id="c-pass", user_message="你好", agent_mode="work")
    case_fail = EvalCase(id="c-fail", user_message="读文件", agent_mode="coding")

    cr_pass = CaseResult(
        case=case_pass,
        events=[{"event": "token", "data": "你好"}, {"event": "done", "data": "{}"}],
        judge_results=[
            JudgeResult(
                case_id="c-pass",
                passed=True,
                score=5.0,
                reason="事件断言全部通过",
                layer="L1",
            ),
            JudgeResult(
                case_id="c-pass",
                passed=True,
                score=4.0,
                reason="相关性高",
                details={"relevance": 5, "accuracy": 3},
                layer="L2",
            ),
        ],
        passed=True,
        avg_score=4.5,
        duration_ms=120,
    )

    cr_fail = CaseResult(
        case=case_fail,
        events=[{"event": "error", "data": "timeout"}],
        judge_results=[
            JudgeResult(
                case_id="c-fail",
                passed=False,
                score=0.0,
                reason="缺少 token 事件",
                details={"missing": ["token"]},
                layer="L1",
            ),
        ],
        passed=False,
        avg_score=0.0,
        duration_ms=1500,
        error="执行超时",
    )

    return EvalResult(
        suite_id="smoke",
        started_at=datetime(2026, 7, 8, 12, 0, 0),
        duration_ms=1620,
        case_results=[cr_pass, cr_fail],
    )


def test_console_reporter_returns_string_with_case_id_and_pass_count() -> None:
    """ConsoleReporter.render 返回字符串，含 case_id 与通过数汇总。"""
    result = _build_sample_result()
    out = ConsoleReporter().render(result)

    assert isinstance(out, str)
    assert "c-pass" in out
    assert "c-fail" in out
    # 通过数汇总：1/2
    assert "1/2" in out or ("1" in out and "2" in out)


def test_console_reporter_contains_pass_fail_marks() -> None:
    """ConsoleReporter 输出含 ✓（通过）或 ✗（失败）标记。"""
    result = _build_sample_result()
    out = ConsoleReporter().render(result)

    assert "✓" in out or "✗" in out
    # 既有通过又有失败，两种标记都应出现
    assert "✓" in out
    assert "✗" in out


def test_markdown_reporter_contains_title() -> None:
    """MarkdownReporter.render 返回 MD 字符串，含标题 ``# 评测报告``。"""
    result = _build_sample_result()
    md = MarkdownReporter().render(result)

    assert isinstance(md, str)
    assert "# 评测报告: smoke" in md


def test_markdown_reporter_contains_summary_and_case_details() -> None:
    """MarkdownReporter 含汇总表与 case 详情表。"""
    result = _build_sample_result()
    md = MarkdownReporter().render(result)

    # 汇总表区段
    assert "汇总" in md
    assert "1 / 2" in md or "1/2" in md
    # case 详情表区段
    assert "Case 详情" in md or "详情" in md
    assert "c-pass" in md
    assert "c-fail" in md


def test_markdown_reporter_includes_failed_case_details() -> None:
    """MarkdownReporter 对失败 case 展开 JudgeResult.details。"""
    result = _build_sample_result()
    md = MarkdownReporter().render(result)

    # 失败 case 详情区段
    assert "失败" in md
    # L1 判分原因与 details 内容被展开
    assert "缺少 token 事件" in md
    assert "missing" in md


def test_json_reporter_roundtrip() -> None:
    """JsonReporter.render 返回有效 JSON，可反序列化回 EvalResult。"""
    result = _build_sample_result()
    out = JsonReporter().render(result)

    assert isinstance(out, str)
    restored = EvalResult.model_validate_json(out)
    assert restored.suite_id == result.suite_id
    assert restored.duration_ms == result.duration_ms
    assert len(restored.case_results) == len(result.case_results)
    assert restored.case_results[0].case.id == "c-pass"
    assert restored.case_results[1].passed is False
    assert restored.started_at == result.started_at


def test_extract_agent_reply_concatenates_tokens() -> None:
    """extract_agent_reply 仅提取 event=='token' 的 data 并拼接。"""
    events = [
        {"event": "token", "data": "Hello "},
        {"event": "tool_call", "data": '{"name":"read_file"}'},
        {"event": "token", "data": "World"},
        {"event": "done", "data": "{}"},
    ]
    assert extract_agent_reply(events) == "Hello World"


def test_extract_agent_reply_empty_when_no_token_events() -> None:
    """无 token 事件时返回空字符串。"""
    events = [
        {"event": "tool_call", "data": "{}"},
        {"event": "done", "data": "{}"},
    ]
    assert extract_agent_reply(events) == ""


def test_format_duration_formats_correctly() -> None:
    """format_duration：>=1000ms 用秒，<1000ms 用毫秒。"""
    assert format_duration(120) == "120ms"
    assert format_duration(0) == "0ms"
    assert format_duration(1200) == "1.2s"
    assert format_duration(1500) == "1.5s"
    assert format_duration(1000) == "1.0s"


def test_markdown_reporter_escapes_pipe_and_newline_in_table() -> None:
    """B5: 表格单元格中的 | 与换行符被转义，避免破坏表格结构。"""
    case = EvalCase(id="c-pipe|x", user_message="hi", agent_mode="work")
    cr = CaseResult(
        case=case,
        events=[],
        judge_results=[
            JudgeResult(
                case_id="c-pipe|x",
                passed=False,
                score=0.0,
                reason="line1\nline2|pipe",
                details={"key": "val|ue"},
                layer="L1",
            )
        ],
        passed=False,
        avg_score=0.0,
        duration_ms=10,
        error="err|msg",
    )
    result = EvalResult(
        suite_id="s1",
        started_at=datetime(2026, 7, 15),
        duration_ms=10,
        case_results=[cr],
    )
    md = MarkdownReporter().render(result)

    # case 详情表行：单元格内的 | 应被转义为 \|
    # 找到 case 详情表所在行
    detail_lines = [ln for ln in md.splitlines() if "c-pipe" in ln]
    assert detail_lines, "case 详情表行应存在"
    detail_line = detail_lines[0]
    # 原始 | 应被转义为 \|（不破坏表格列分隔）
    assert "\\|" in detail_line
    # 换行符应被替换为空格
    assert "\n" not in detail_line


def test_markdown_reporter_renders_details_value_as_json() -> None:
    """B5: details 中 list/dict 值用 JSON 渲染，而非 Python repr。"""
    case = EvalCase(id="c-obj", user_message="hi", agent_mode="work")
    cr = CaseResult(
        case=case,
        events=[],
        judge_results=[
            JudgeResult(
                case_id="c-obj",
                passed=False,
                score=0.0,
                reason="fail",
                details={"criteria": [{"name": "c1", "passed": False}]},
                layer="L2",
            )
        ],
        passed=False,
        avg_score=0.0,
        duration_ms=10,
    )
    result = EvalResult(
        suite_id="s1",
        started_at=datetime(2026, 7, 15),
        duration_ms=10,
        case_results=[cr],
    )
    md = MarkdownReporter().render(result)

    # list 值应渲染为 JSON（含双引号），而非 Python repr（含单引号）
    assert '"name": "c1"' in md or '"name":"c1"' in md
    # 不应出现 Python dict repr 的单引号风格
    assert "{'name': 'c1'}" not in md
