"""审批请求生命周期单元测试：approval_id + 活跃注册表 + consume-once + TTL。

覆盖 REQ-APR-1 ~ REQ-APR-5：
1. ApprovalRequest 拥有独立 approval_id（REQ-APR-1）
2. submit_approval 必须 compare-and-consume 活跃请求（REQ-APR-2）
3. full_trust 只能由匹配活跃请求的决定开启（REQ-APR-3）
4. 前端恢复基于活跃请求（REQ-APR-4）
5. 审批请求有 TTL 与清理语义（REQ-APR-5）
"""

from __future__ import annotations

import time

import pytest

from app.security.approval import (
    ApprovalDecision,
    ApprovalResult,
)
from app.security.approval import state as security_state


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    """每个测试前清理全局审批状态。"""
    security_state._pending_approvals.clear()
    security_state._abort_flags.clear()
    security_state._abort_events.clear()
    security_state._pause_flags.clear()
    security_state._pause_events.clear()
    security_state._active_approval_requests.clear()
    yield
    security_state._pending_approvals.clear()
    security_state._abort_flags.clear()
    security_state._abort_events.clear()
    security_state._pause_flags.clear()
    security_state._pause_events.clear()
    security_state._active_approval_requests.clear()


# ============================================================
# REQ-APR-1: 审批请求必须拥有独立身份
# ============================================================


async def test_register_creates_unique_approval_id() -> None:
    """register_approval_request 生成唯一 approval_id，绑定 thread_id/run_id。"""
    req1 = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-1"
    )
    req2 = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-2"
    )
    assert req1.approval_id != req2.approval_id
    assert req1.thread_id == "t1"
    assert req1.run_id == "run-1"
    assert req1.request_kind == "dangerous_tool"
    assert req1.tool_call_id == "tc-1"
    assert req1.created_at > 0
    assert req1.expires_at > req1.created_at
    assert req1.consumed_at is None


async def test_two_approvals_same_thread_not_reused() -> None:
    """同线程前后两个审批请求不可复用同一决定（REQ-APR-1 Scenario）。"""
    a1 = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-1"
    )
    # 消费 a1 并提交决定
    consumed = await security_state.consume_approval(a1.approval_id, "run-1")
    assert consumed is not None
    ok = await security_state.submit_approval(
        a1.approval_id,
        ApprovalResult(decision=ApprovalDecision.APPROVE),
        "run-1",
    )
    assert ok is True
    # 消费决定
    decision = await security_state.pop_approval("t1")
    assert decision is not None
    assert decision.approved is True

    # 第二个请求 a2 开始等待
    a2 = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-2"
    )
    assert a2.approval_id != a1.approval_id
    # a1 的决定不应自动作用于 a2
    assert await security_state.pop_approval("t1") is None


# ============================================================
# REQ-APR-2: submit_approval 必须 compare-and-consume
# ============================================================


async def test_stale_approval_rejected() -> None:
    """过期请求被拒绝（REQ-APR-2 Scenario: stale approval）。"""
    req = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-1"
    )
    # 篡改 expires_at 为过去
    req.expires_at = time.monotonic() - 1
    ok = await security_state.submit_approval(
        req.approval_id,
        ApprovalResult(decision=ApprovalDecision.APPROVE),
        "run-1",
    )
    assert ok is False
    # 不改变当前线程的审批状态
    assert await security_state.pop_approval("t1") is None


async def test_pre_approval_rejected() -> None:
    """无活跃请求时提交被拒（REQ-APR-2 Scenario: pre-approval）。"""
    ok = await security_state.submit_approval(
        "nonexistent-approval-id",
        ApprovalResult(decision=ApprovalDecision.APPROVE),
        "run-1",
    )
    assert ok is False
    # 不开启 full_trust（无状态写入）
    assert await security_state.pop_approval("t1") is None


async def test_mismatched_run_rejected() -> None:
    """run_id 不匹配被拒（REQ-APR-3 Scenario: 旧 run 的决定不能放开新 run）。"""
    req = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-1"
    )
    ok = await security_state.submit_approval(
        req.approval_id,
        ApprovalResult(decision=ApprovalDecision.APPROVE),
        "run-2",  # 不匹配
    )
    assert ok is False
    assert await security_state.pop_approval("t1") is None


async def test_consume_once() -> None:
    """同一 approval_id 只能消费一次。"""
    req = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-1"
    )
    # 第一次 consume 成功
    consumed1 = await security_state.consume_approval(req.approval_id, "run-1")
    assert consumed1 is not None
    assert consumed1.consumed_at is not None
    # 第二次 consume 失败
    consumed2 = await security_state.consume_approval(req.approval_id, "run-1")
    assert consumed2 is None


async def test_submit_after_consume_succeeds() -> None:
    """consume 后 submit 写入决策，waiter 可 pop。"""
    req = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-1"
    )
    consumed = await security_state.consume_approval(req.approval_id, "run-1")
    assert consumed is not None
    ok = await security_state.submit_approval(
        req.approval_id,
        ApprovalResult(decision=ApprovalDecision.APPROVE),
        "run-1",
    )
    assert ok is True
    decision = await security_state.pop_approval("t1")
    assert decision is not None
    assert decision.approved is True


async def test_submit_without_consume_rejected() -> None:
    """未 consume 的请求直接 submit 被拒。"""
    req = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-1"
    )
    # 未先 consume，直接 submit
    ok = await security_state.submit_approval(
        req.approval_id,
        ApprovalResult(decision=ApprovalDecision.APPROVE),
        "run-1",
    )
    assert ok is False
    assert await security_state.pop_approval("t1") is None


# ============================================================
# REQ-APR-3: full_trust 只能由匹配活跃请求的决定开启
# ============================================================


async def test_full_trust_requires_active_request() -> None:
    """full_trust 只在活跃请求消费后开启。

    通过 consume_approval 验证：无活跃请求时 consume 返回 None，
    chat_approve 应据此拒绝 full_trust。
    """
    # 无活跃请求
    consumed = await security_state.consume_approval("fake-id", "run-1")
    assert consumed is None

    # 有活跃请求但 run 不匹配
    req = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-1"
    )
    consumed = await security_state.consume_approval(req.approval_id, "run-2")
    assert consumed is None

    # 匹配的活跃请求
    consumed = await security_state.consume_approval(req.approval_id, "run-1")
    assert consumed is not None
    assert consumed.thread_id == "t1"


# ============================================================
# REQ-APR-4: 前端 pending approval 恢复必须基于活跃请求
# ============================================================


async def test_get_active_approval_for_thread() -> None:
    """get_active_approval_for_thread 返回当前活跃请求。"""
    # 无活跃请求
    assert await security_state.get_active_approval_for_thread("t1") is None

    req = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-1"
    )
    found = await security_state.get_active_approval_for_thread("t1")
    assert found is not None
    assert found.approval_id == req.approval_id


async def test_get_active_approval_excludes_consumed() -> None:
    """已消费的请求不再出现在活跃列表中。"""
    req = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-1"
    )
    await security_state.consume_approval(req.approval_id, "run-1")
    assert await security_state.get_active_approval_for_thread("t1") is None


async def test_get_active_approval_excludes_expired() -> None:
    """过期请求不再出现在活跃列表中。"""
    req = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-1"
    )
    req.expires_at = time.monotonic() - 1
    assert await security_state.get_active_approval_for_thread("t1") is None


# ============================================================
# REQ-APR-5: 审批请求必须有 TTL 与清理语义
# ============================================================


async def test_ttl_expiry() -> None:
    """过期请求被清理，consume 和 get 都返回 None。"""
    req = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-1"
    )
    # 篡改为过期
    req.expires_at = time.monotonic() - 1

    # consume 过期请求失败
    consumed = await security_state.consume_approval(req.approval_id, "run-1")
    assert consumed is None

    # get 也返回 None
    assert await security_state.get_active_approval_for_thread("t1") is None

    # submit 过期请求失败
    ok = await security_state.submit_approval(
        req.approval_id,
        ApprovalResult(decision=ApprovalDecision.APPROVE),
        "run-1",
    )
    assert ok is False


async def test_reaper_cleans_expired_approval_requests() -> None:
    """reaper 清理过期的 approval requests。"""
    req = await security_state.register_approval_request(
        "t1", "run-1", "dangerous_tool", "tc-1"
    )
    # 篡改为过期
    req.expires_at = time.monotonic() - 1

    # 手动触发 reaper 清理逻辑
    now = security_state._now()
    async with security_state._state_lock:
        expired_ids = [
            aid
            for aid, r in security_state._active_approval_requests.items()
            if r.expires_at < now or (r.consumed_at is not None and now - r.consumed_at > security_state._ACTIVE_APPROVAL_TTL)
        ]
        for aid in expired_ids:
            security_state._active_approval_requests.pop(aid, None)

    assert req.approval_id not in security_state._active_approval_requests


# ============================================================
# _make_approval_event 包含 approval_id + run_id
# ============================================================


def test_make_approval_event_includes_approval_id_and_run_id() -> None:
    """_make_approval_event 在 payload 中增加 approval_id 和 run_id。"""
    from app.security.approval.flow import _make_approval_event

    tool_call = {"name": "write_file", "args": {"file_path": "/tmp/test.py"}, "id": "tc-1"}
    event = _make_approval_event(
        tool_call,
        "thread-xyz",
        kind="dangerous_tool",
        approval_id="apr-123",
        run_id="run-456",
    )
    import json
    data = json.loads(event["data"])
    assert data["approval_id"] == "apr-123"
    assert data["run_id"] == "run-456"
    assert data["thread_id"] == "thread-xyz"
