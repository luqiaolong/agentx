"""Unit tests for :func:`app.security.reporter.aggregate`."""

from __future__ import annotations

from app.security.risk import RiskAssessment, RiskLevel
from app.security.reporter import aggregate


def _a(
    level: RiskLevel,
    policy: str,
    rule: str,
    msg: str = "m",
    matched: tuple[str, ...] = (),
    suggestion: str = "",
) -> RiskAssessment:
    return RiskAssessment(
        level=level,
        policy_name=policy,
        rule_id=rule,
        message=msg,
        matched_chars=matched,
        suggestion=suggestion,
    )


def test_aggregate_empty_returns_empty_string() -> None:
    assert aggregate([], "echo hi", None) == ""  # type: ignore[arg-type]


def test_aggregate_single_assessment_format() -> None:
    out = aggregate([_a(RiskLevel.HIGH, "blocklist", "forbidden_cmd:rm", "rm blocked")], "rm foo", None)  # type: ignore[arg-type]
    assert "1 项命中" in out
    assert "[1]" in out
    assert "blocklist.forbidden_cmd:rm" in out
    assert "HIGH" in out
    assert "rm blocked" in out
    assert "rm foo" in out


def test_aggregate_multiple_assessments_numbered() -> None:
    a1 = _a(RiskLevel.HIGH, "blocklist", "forbidden_cmd:rm", "rm blocked")
    a2 = _a(RiskLevel.MEDIUM, "metachar", "posix_pipe", "pipe", matched=("|",))
    out = aggregate([a1, a2], "rm foo | tee", None)  # type: ignore[arg-type]
    assert "[1]" in out
    assert "[2]" in out
    assert "2 项命中" in out


def test_aggregate_truncates_long_command() -> None:
    long_cmd = "echo " + "a" * 500
    out = aggregate(
        [_a(RiskLevel.HIGH, "blocklist", "x", "x")],
        long_cmd,
        None,  # type: ignore[arg-type]
    )
    assert "..." in out
    assert len(out) < 800


def test_aggregate_includes_suggestion_when_present() -> None:
    a = _a(RiskLevel.MEDIUM, "metachar", "x", "x", suggestion="use python instead")
    out = aggregate([a], "cmd", None)  # type: ignore[arg-type]
    assert "use python instead" in out
    assert "建议" in out


def test_aggregate_includes_matched_chars_when_present() -> None:
    a = _a(RiskLevel.MEDIUM, "metachar", "x", "x", matched=("|", ";"))
    out = aggregate([a], "cmd", None)  # type: ignore[arg-type]
    assert "'|'" in out
    assert "';'" in out
    assert "命中字符" in out
