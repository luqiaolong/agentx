"""ApprovalDecision Enum + ApprovalResult dataclass 单元测试。

覆盖：
1. ApprovalDecision Enum 值（approve/once/session/deny）
2. ApprovalResult.approved property 语义
3. 矛盾消除：无法构造 approved=True + decision=deny
4. str Enum 特性（与字符串直接比较）
"""

from __future__ import annotations

import pytest

from app.security.approval import ApprovalDecision, ApprovalResult


# ============================================================
# 1. Enum 值
# ============================================================


def test_approval_decision_enum_values() -> None:
    """四种互斥决策值。"""
    assert ApprovalDecision.APPROVE.value == "approve"
    assert ApprovalDecision.ONCE.value == "once"
    assert ApprovalDecision.SESSION.value == "session"
    assert ApprovalDecision.DENY.value == "deny"


def test_approval_decision_is_str_enum() -> None:
    """继承 str，可与字符串直接比较。"""
    assert ApprovalDecision.APPROVE == "approve"
    assert ApprovalDecision.DENY == "deny"


def test_approval_decision_from_string() -> None:
    """可通过字符串构造 Enum。"""
    assert ApprovalDecision("approve") is ApprovalDecision.APPROVE
    assert ApprovalDecision("once") is ApprovalDecision.ONCE
    assert ApprovalDecision("session") is ApprovalDecision.SESSION
    assert ApprovalDecision("deny") is ApprovalDecision.DENY


def test_approval_decision_invalid_string_raises() -> None:
    """非法字符串构造 Enum 应抛 ValueError。"""
    with pytest.raises(ValueError):
        ApprovalDecision("unknown")


# ============================================================
# 2. ApprovalResult.approved property
# ============================================================


def test_approval_result_approved_true_for_approve() -> None:
    result = ApprovalResult(decision=ApprovalDecision.APPROVE)
    assert result.approved is True


def test_approval_result_approved_true_for_once() -> None:
    result = ApprovalResult(decision=ApprovalDecision.ONCE)
    assert result.approved is True


def test_approval_result_approved_true_for_session() -> None:
    result = ApprovalResult(decision=ApprovalDecision.SESSION)
    assert result.approved is True


def test_approval_result_approved_false_for_deny() -> None:
    result = ApprovalResult(decision=ApprovalDecision.DENY)
    assert result.approved is False


# ============================================================
# 3. 矛盾消除
# ============================================================


def test_no_contradiction_approved_and_decision() -> None:
    """新设计中 approved 由 decision 派生，无法构造 approved=True + decision=deny。

    旧 ``ApprovalDecision(approved=True, decision="deny")`` 可矛盾，
    新 ``ApprovalResult(decision=DENY).approved`` 恒为 False。
    """
    result = ApprovalResult(decision=ApprovalDecision.DENY)
    # approved 恒为 False，无法人为设为 True
    assert result.approved is False
    # decision 字段是 Enum，不是任意字符串
    assert result.decision is ApprovalDecision.DENY


def test_approval_result_path_and_writable_defaults() -> None:
    """path/writable 默认值。"""
    result = ApprovalResult(decision=ApprovalDecision.APPROVE)
    assert result.path is None
    assert result.writable is False


def test_approval_result_with_path_and_writable() -> None:
    """directory_extension 场景：携带 path + writable。"""
    result = ApprovalResult(
        decision=ApprovalDecision.SESSION,
        path="/tmp/data",
        writable=True,
    )
    assert result.decision == ApprovalDecision.SESSION
    assert result.path == "/tmp/data"
    assert result.writable is True
    assert result.approved is True


# ============================================================
# 4. 旧 ApprovalDecision 字段兼容性
# ============================================================


def test_approval_result_replaces_old_decision_approved() -> None:
    """ApprovalResult 可替代旧 ApprovalDecision 的使用场景。

    旧: decision.approved (字段), decision.decision (str)
    新: result.approved (property), result.decision (Enum)
    """
    # 旧风格的 approve
    result_approve = ApprovalResult(decision=ApprovalDecision.APPROVE)
    assert result_approve.approved is True
    assert result_approve.decision == "approve"  # str Enum 比较

    # 旧风格的 deny
    result_deny = ApprovalResult(decision=ApprovalDecision.DENY)
    assert result_deny.approved is False
    assert result_deny.decision == "deny"
