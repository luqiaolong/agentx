"""验证 approval_runner._is_interrupted 在 LangGraph 1.x + deepagents 0.6.x 下能正确检测 interrupt。

核心 bug：旧判定 ``"tools" in state.next`` 对 HumanInTheLoopMiddleware.after_model 触发
的 interrupt 永远 False。修复后增加 ``state.interrupts`` 主判定。

用法：python backend/scripts/verify_is_interrupted.py
"""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

# 添加 backend 路径以便 import app.* 模块
sys.path.insert(0, "d:/java/agentprojects/agentx/backend")

from langgraph.types import Interrupt  # noqa: E402

from app.deepagent.approval_runner import _is_interrupted  # noqa: E402


async def case_1_human_in_the_loop_interrupt() -> None:
    """deepagents 0.6.x HITL：state.next 含 'HumanInTheLoopMiddleware.after_model'，
    state.interrupts 有 Interrupt 对象。修复前 _is_interrupted 返回 False，
    修复后必须返回 True（修复目标）。"""
    state = SimpleNamespace(
        next=("HumanInTheLoopMiddleware.after_model",),
        interrupts=(Interrupt(value={"decisions": [{"type": "approve"}]}, id="x"),),
        values={},
    )
    agent = SimpleNamespace(aget_state=AsyncMock(return_value=state))
    result = await _is_interrupted(agent, {"configurable": {"thread_id": "t"}})
    assert result is True, f"修复失败：HITL interrupt 应被检测，实际 {result}"
    print("[case 1] HITL interrupt (修复目标) ............. PASS")


async def case_2_no_interrupt() -> None:
    """正常运行状态：state.next 为空，state.interrupts 为空 → False。"""
    state = SimpleNamespace(next=(), interrupts=(), values={})
    agent = SimpleNamespace(aget_state=AsyncMock(return_value=state))
    result = await _is_interrupted(agent, {"configurable": {"thread_id": "t"}})
    assert result is False
    print("[case 2] no interrupt (正常流式状态) ............ PASS")


async def case_3_legacy_interrupt_before_tools() -> None:
    """旧 LangGraph 机制 interrupt_before=['tools']：state.next 含 'tools' → True。
    修复后保留此分支作为向后兼容。"""
    state = SimpleNamespace(next=("tools",), interrupts=(), values={})
    agent = SimpleNamespace(aget_state=AsyncMock(return_value=state))
    result = await _is_interrupted(agent, {"configurable": {"thread_id": "t"}})
    assert result is True
    print("[case 3] legacy interrupt_before=['tools'] 兼容 .. PASS")


async def case_4_empty_interrupts_next_other_node() -> None:
    """非 interrupt 的正常推进：state.next 含 'model' 等其他节点 → False。"""
    state = SimpleNamespace(next=("model",), interrupts=(), values={})
    agent = SimpleNamespace(aget_state=AsyncMock(return_value=state))
    result = await _is_interrupted(agent, {"configurable": {"thread_id": "t"}})
    assert result is False
    print("[case 4] non-interrupt next node ............... PASS")


async def case_5_state_none() -> None:
    """aget_state 异常或返回 None → False（不崩溃）。"""
    agent = SimpleNamespace(aget_state=AsyncMock(return_value=None))
    result = await _is_interrupted(agent, {"configurable": {"thread_id": "t"}})
    assert result is False
    print("[case 5] state None (边界) ..................... PASS")


async def case_6_state_without_interrupts_attr() -> None:
    """老版本 LangGraph 可能没有 interrupts 属性 → 走 getattr fallback。"""
    state = SimpleNamespace(next=(), values={})  # 无 interrupts 属性
    agent = SimpleNamespace(aget_state=AsyncMock(return_value=state))
    result = await _is_interrupted(agent, {"configurable": {"thread_id": "t"}})
    assert result is False
    print("[case 6] state has no interrupts attr (兼容) ... PASS")


async def main() -> int:
    cases = [
        case_1_human_in_the_loop_interrupt,
        case_2_no_interrupt,
        case_3_legacy_interrupt_before_tools,
        case_4_empty_interrupts_next_other_node,
        case_5_state_none,
        case_6_state_without_interrupts_attr,
    ]
    failures = 0
    for c in cases:
        try:
            await c()
        except AssertionError as e:
            print(f"[FAIL] {c.__name__}: {e}")
            failures += 1
        except Exception as e:
            print(f"[ERROR] {c.__name__}: {type(e).__name__}: {e}")
            failures += 1
    print()
    if failures == 0:
        print("[OK] ALL 6 CASES PASSED - _is_interrupted 修复已生效")
        return 0
    print(f"[FAIL] {failures} case(s) failed")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))