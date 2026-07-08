"""trace_id 全链路验证脚本（开发模式一次性使用）。

使用方式：
    cd backend && ..\.venv\Scripts\python.exe -m scripts.verify_trace_id

预期：
    1. logger.info 输出 stderr + 写入 backend.log（含 trace=xxx）
    2. SSE 事件 data 顶层含 trace_id 字段
    3. 退出 bind_trace 后日志无 trace_id
"""
import sys
from pathlib import Path

# 让 Python 能找到 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.observability.logger import logger, _LOG_FILE
from app.observability.trace import bind_trace, current_trace_id, new_trace_id
from app.utils.sse_events import make_tool_call_event, make_approval_event


def main() -> None:
    tid = new_trace_id()
    print(f"[1] generated trace_id: {tid}")
    print(f"[2] log file path: {_LOG_FILE}")

    # ---- case A: 在 bind_trace 块内 ----
    with bind_trace(tid):
        # 验证 logger 自动注入 trace_id
        logger.info("case_a: inside bind_trace", thread_id="test-thread")
        print(f"[3] current_trace_id() = {current_trace_id()}")

        # 验证 SSE 事件 data 顶层自动注入 trace_id
        evt_call = make_tool_call_event(
            "tc-1", "cli_execute", {"command": "wmic cpu get name"}, source="work"
        )
        print(f"[4] tool_call event (no explicit trace_id): {evt_call}")
        assert f'"trace_id": "{tid}"' in evt_call["data"], (
            f"expected trace_id in data, got: {evt_call}"
        )

        evt_approval = make_approval_event(
            {"tool_name": "cli_execute", "preview": "wmic cpu get name", "kind": "dangerous_tool"}
        )
        print(f"[5] approval_request event: {evt_approval}")
        assert f'"trace_id": "{tid}"' in evt_approval["data"], (
            f"expected trace_id in data, got: {evt_approval}"
        )

    # ---- case B: 退出 bind_trace ----
    print(f"[6] current_trace_id() after exit = {current_trace_id()}")
    assert current_trace_id() is None, "trace_id should be cleared after bind_trace exits"

    # 退出后的事件不应带 trace_id
    evt_outside = make_tool_call_event("tc-2", "read_file", {"path": "/tmp"})
    print(f"[7] tool_call event (outside bind_trace): {evt_outside}")
    assert "trace_id" not in evt_outside["data"], (
        f"expected NO trace_id, got: {evt_outside}"
    )

    # ---- case C: 显式传 trace_id 优先于 ContextVar ----
    with bind_trace(new_trace_id()):
        evt_explicit = make_tool_call_event(
            "tc-3", "read_file", {"path": "/tmp"}, trace_id="explicit_overrides_ctx"
        )
        print(f"[8] tool_call event (explicit overrides): {evt_explicit}")
        assert '"trace_id": "explicit_overrides_ctx"' in evt_explicit["data"]

    print("\n✅ ALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()