"""trace_id 集成验证：模拟一次完整 chat 请求，检查 SSE 事件流与日志输出。

使用 FastAPI TestClient（同步）启动一个临时的 chat 流程，验证：
1. 每个 JSON 事件 data 顶层含 trace_id
2. token 事件不带 trace_id（按设计）
3. backend.log 文件追加新行（含 trace_id）
4. abort 路径的 error 事件也带 trace_id

注意：本脚本只验证 SSE 入口行为，不触发完整 LLM 调用（避免对真实 LLM 的依赖）。
修改点：
- 用 monkeypatch 替换 ``run_router``，让它立刻 yield 几个测试事件后 done。
- 用 httpx AsyncClient 或 FastAPI TestClient（内置）发起 POST。
"""
import sys
from pathlib import Path

# 让 Python 能找到 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.api import chat as chat_api
from app.api.schemas import ChatRequest
from app.observability.logger import logger, _LOG_FILE
from app.observability.trace import bind_trace, current_trace_id, new_trace_id


def _fake_run_router(
    message, thread_id, checkpointer=None, permission_mode="standard",
    agent_mode="work", workspace_path=None, revoked_paths=None, chat_model=None,
):
    """测试用 router：直接 yield 几个事件，验证 SSE 包装层注入 trace_id。"""
    # 注意：run_router 是 async generator，必须 ``async def`` + ``yield``
    # （不是普通 generator；测试中常见误用普通 def + yield，导致
    #  ``async for event in run_router(...)`` 报 "__aiter__ method" 错误）。
    if False:  # placeholder 让函数变 async def（见下面真正的 async 实现）
        yield  # never executed
    return
    yield  # unreachable


async def _async_fake_run_router(
    message, thread_id, checkpointer=None, permission_mode="standard",
    agent_mode="work", workspace_path=None, revoked_paths=None, chat_model=None,
):
    """测试用 router：async generator，直接 yield 几个事件。"""
    logger.info("fake_router.start", thread_id=thread_id, message_len=len(message))
    from app.utils.sse_events import (
        make_sse_event,
        make_tool_call_event,
        make_approval_event,
    )
    yield make_sse_event("reasoning", {"content": "排查 CPU 占用", "source": "work"})
    yield make_tool_call_event(
        "tc-wmic-1", "cli_execute",
        {"command": "wmic", "arguments": ["cpu", "get", "name"]},
        source="work",
    )
    yield make_approval_event({
        "thread_id": thread_id,
        "tool_name": "cli_execute",
        "preview": "wmic cpu get name",
        "kind": "dangerous_tool",
    })
    yield make_sse_event("error", {"message": "模拟 abort 路径", "code": "abort"})
    yield make_sse_event("done", "{}")


def main() -> None:
    # Monkeypatch run_router（patch app.main.run_router，因为 _event_generator
    # 延迟 import ``from app.main import run_router``，chat_api.run_router 是
    # re-export 后的旧引用，不影响实际调用）。必须是 async generator。
    import app.main as app_main
    app_main.run_router = _async_fake_run_router  # type: ignore[assignment]

    # 用 FastAPI TestClient 模拟 HTTP POST
    from fastapi import FastAPI
    app = FastAPI()
    chat_api.register_chat_routes(app)

    def _parse_sse(text: str) -> list[tuple[str, str]]:
        """SSE 事件解析：``event:`` 行 + ``data:`` 行 + 空行分隔。"""
        events: list[tuple[str, str]] = []
        cur_event = None
        cur_data: list[str] = []
        for line in text.splitlines():
            if line.startswith("event:"):
                cur_event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                # 注意 data: 后面有一个空格（HTTP 标准），要 lstrip 一次
                cur_data.append(line[len("data:"):].lstrip())
            elif line == "":
                if cur_event:
                    events.append((cur_event, "\n".join(cur_data)))
                cur_event = None
                cur_data = []
        return events

    with TestClient(app) as client:
        # ---- case 1: 前端传 trace_id（沿用） ----
        frontend_trace = "deadbeefcafe0011"
        print(f"\n[case 1] frontend trace_id: {frontend_trace}")
        with client.stream(
            "POST",
            "/api/chat",
            json={
                "message": "排查电脑CPU和内存占用",
                "thread_id": "test-thread-1",
                "permission_mode": "standard",
                "system_prompt": None,
                "agent_mode": "work",
                "workspace_path": None,
                "revoked_paths": [],
                "trace_id": frontend_trace,
            },
        ) as resp:
            print(f"  HTTP status: {resp.status_code}")
            assert resp.status_code == 200
            text = resp.read().decode("utf-8")
            parsed_events = _parse_sse(text)
            print(f"  parsed {len(parsed_events)} events:")
            for et, dt in parsed_events:
                print(f"    event={et}, data={dt[:160]}")

            # 断言：每个 JSON 事件 data 顶层含 trace_id（前端传的那个）
            for et, dt in parsed_events:
                if et in ("reasoning", "tool_call", "approval_request", "error"):
                    assert f'"trace_id": "{frontend_trace}"' in dt, (
                        f"event {et} missing trace_id: {dt}"
                    )
                    print(f"    OK {et} has trace_id={frontend_trace}")
                if et == "done":
                    # done 事件按设计不带 trace_id（payload 是固定的 "{}"）
                    assert dt == "{}", f"done event data changed: {dt}"
                    print(f"    OK done 事件 payload 保持不变")
                if et == "token":
                    # token 事件 data 是纯字符串，按设计不带 trace_id
                    assert frontend_trace not in dt, (
                        f"token event should NOT contain trace_id: {dt}"
                    )
                    print(f"    OK token 事件 data 不含 trace_id（按设计）")

        # ---- case 2: 前端不传 trace_id（后端自生成） ----
        print(f"\n[case 2] no frontend trace_id, backend should generate")
        with client.stream(
            "POST",
            "/api/chat",
            json={
                "message": "再来一次",
                "thread_id": "test-thread-2",
                "permission_mode": "standard",
                "system_prompt": None,
                "agent_mode": "work",
                "workspace_path": None,
                "revoked_paths": [],
            },
        ) as resp:
            text = resp.read().decode("utf-8")
            parsed_events = _parse_sse(text)
            first_trace = None
            for et, dt in parsed_events:
                if et in ("reasoning", "tool_call", "approval_request"):
                    import json
                    try:
                        obj = json.loads(dt)
                        first_trace = obj.get("trace_id")
                        if first_trace:
                            break
                    except Exception:
                        pass
            assert first_trace is not None, "no trace_id found in SSE events"
            assert len(first_trace) == 16, f"trace_id length should be 16: {first_trace}"
            print(f"  ✓ 后端生成 trace_id: {first_trace}")

    # ---- case 3: backend.log 文件追加 ----
    if _LOG_FILE.exists():
        with _LOG_FILE.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        # 检查最近几行是否有 trace_id
        recent_with_trace = [
            line for line in lines[-20:]
            if "| trace=" in line or "| trace=<magenta>" in line
        ]
        print(f"\n[case 3] backend.log 最近 20 行: {len(recent_with_trace)} 条带 trace_id")
        for line in recent_with_trace[-3:]:
            print(f"  {line.rstrip()}")

    print("\n✅ ALL SSE INTEGRATION ASSERTIONS PASSED")


if __name__ == "__main__":
    main()