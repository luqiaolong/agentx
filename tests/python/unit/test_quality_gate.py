"""_quality_gate 质量门测试（R5: identical-findings 检查）。

验证：
- 所有 findings 完全相同（非空、非截断标记）→ 拒绝，reason="all_findings_identical"
- 存在差异 → 放行
- 全部为截断标记 → 用截断 reason 拒绝（而非 identical reason）

Phase 2 清理：``Blackboard`` dataclass 已删除，``_quality_gate`` 现接受 dict（Mapping）。
"""

from __future__ import annotations

from app.team.aggregator import _quality_gate


def test_all_identical_findings_rejected() -> None:
    """4 个完全相同的非截断 findings → 拒绝，reason 为 all_findings_identical。"""
    bb = {
        "findings": {
            "agent1": "same result",
            "agent2": "same result",
            "agent3": "same result",
            "agent4": "same result",
        },
        "errors": {},
    }
    ok, reason = _quality_gate(bb)
    assert ok is False
    assert reason == "all_findings_identical"


def test_identical_findings_with_whitespace_stripped() -> None:
    """strip 后相同的 findings 也应被识别为 identical。"""
    bb = {
        "findings": {
            "agent1": "  same result  ",
            "agent2": "same result",
            "agent3": " same result ",
        },
        "errors": {},
    }
    ok, reason = _quality_gate(bb)
    assert ok is False
    assert reason == "all_findings_identical"


def test_one_different_finding_passes() -> None:
    """4 个 findings 中有 1 个不同 → 放行。"""
    bb = {
        "findings": {
            "agent1": "same result",
            "agent2": "same result",
            "agent3": "same result",
            "agent4": "different result",
        },
        "errors": {},
    }
    ok, reason = _quality_gate(bb)
    assert ok is True
    assert reason == ""


def test_all_truncation_rejected_with_truncation_reason() -> None:
    """全部为 [结果已截断] → 用截断 reason 拒绝，不是 identical reason。"""
    bb = {
        "findings": {
            "agent1": "[结果已截断]",
            "agent2": "[结果已截断]",
            "agent3": "[结果已截断]",
            "agent4": "[结果已截断]",
        },
        "errors": {},
    }
    ok, reason = _quality_gate(bb)
    assert ok is False
    assert reason != "all_findings_identical"
    assert "截断" in reason


def test_empty_findings_rejected() -> None:
    """无任何 findings → 拒绝。"""
    bb = {"findings": {}, "errors": {}}
    ok, reason = _quality_gate(bb)
    assert ok is False


def test_single_finding_passes() -> None:
    """单个 finding 不触发 identical 检查（至少 2 个才比较）。"""
    bb = {"findings": {"agent1": "only one result"}, "errors": {}}
    ok, reason = _quality_gate(bb)
    assert ok is True
    assert reason == ""


def test_identical_empty_findings_pass() -> None:
    """全空 findings 不触发 identical 拒绝（非空才拒绝）。"""
    bb = {
        "findings": {
            "agent1": "",
            "agent2": "",
        },
        "errors": {},
    }
    ok, _ = _quality_gate(bb)
    assert ok is True
