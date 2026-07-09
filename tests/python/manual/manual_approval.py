"""详细追踪审批流：每个事件都打印 + 异步触发 approve。

关键观察：
- 客户端能否收到 todo_update？
- 客户端能否收到 approval_request？
- approval_request 是否带 thread_id？
- 提交 approve 后是否能恢复？
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8123"


async def consume_sse(client: httpx.AsyncClient, body: dict, label: str) -> list[tuple[str, str]]:
    """完整收 SSE 事件，每收到一条立刻打印 + 入库。"""
    events: list[tuple[str, str]] = []
    print(f"\n[{label}] POST /api/chat thread={body.get('thread_id')} msg={body.get('message')!r}")
    async with client.stream("POST", f"{BASE}/api/chat", json=body) as r:
        print(f"[{label}] status={r.status_code}")
        if r.status_code != 200:
            text = await r.aread()
            print(f"[{label}] non-200 body={text[:300]!r}")
            return events

        buf = b""
        async for chunk in r.aiter_bytes():
            buf += chunk
            text = buf.decode("utf-8", errors="replace").replace("\r\n", "\n")
            # 处理所有完整事件
            while "\n\n" in text:
                block, text = text.split("\n\n", 1)
                evt, data = _parse_sse_block(block)
                if evt:
                    elapsed = time.monotonic()
                    print(f"[{label}] [{elapsed:.2f}s] event={evt} data={data[:200]!r}")
                    events.append((evt, data))
                    buf = text.encode("utf-8")
                    # 收到 approval_request 后自动触发 approve
                    if evt == "approval_request" and "thread_id" in data:
                        try:
                            payload = json.loads(data)
                            tid = payload["thread_id"]
                            print(f"[{label}]   → 自动提交 approve=True thread_id={tid}")
                            await asyncio.sleep(0.5)
                            r2 = await client.post(
                                f"{BASE}/api/chat/approve",
                                json={"thread_id": tid, "approval": True},
                            )
                            print(f"[{label}]   → approve response: {r2.status_code}")
                        except Exception as e:
                            print(f"[{label}]   → approve error: {e}")
            if b"event: done" in buf:
                # 处理剩余内容
                rest = buf.decode("utf-8", errors="replace").replace("\r\n", "\n")
                if "\n\n" in rest:
                    leftover = rest.split("\n\n")[-1]
                    if leftover.strip():
                        evt, data = _parse_sse_block(leftover)
                        if evt:
                            print(f"[{label}] event={evt} data={data[:200]!r}")
                            events.append((evt, data))
                break
        else:
            print(f"[{label}] 流未正常结束（连接断？）")
    return events


def _parse_sse_block(block: str) -> tuple[str, str]:
    """解析单个 SSE block，返回 (event, data)。"""
    evt = ""
    data = ""
    for ln in block.split("\n"):
        ln = ln.strip()
        if ln.startswith("event: "):
            evt = ln[7:].strip()
        elif ln.startswith("data: "):
            data = ln[6:]
    return evt, data


async def main() -> int:
    thread_id = f"approval-test-{int(time.time())}"

    # 0. 授权 workspace 目录（写权限）
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(
            f"{BASE}/api/sandbox/authorize",
            json={
                "thread_id": thread_id,
                "path": "D:/java/agentprojects/agentx/data/workspace",
                "writable": True,
            },
        )
        print(f"[setup] authorize: {r.status_code} {r.json()}")

    # 1. 发送 deep task 触发 write_file
    async with httpx.AsyncClient(timeout=300.0) as c:
        events = await consume_sse(
            c,
            {
                "message": f"在 data/workspace/ 目录下创建一个文件 approval_test_{thread_id[-6:]}.txt，内容写 'approval-flow-test'",
                "thread_id": thread_id,
            },
            "DEEP",
        )

    # 总结
    print(f"\n=== 收到 {len(events)} 条事件 ===")
    for evt, data in events:
        print(f"  {evt}: {data[:120]!r}")

    # 检查关键事件
    event_kinds = [e for e, _ in events]
    if "approval_request" in event_kinds:
        print("[OK] 收到 approval_request")
    else:
        print("[FAIL] 未收到 approval_request")

    if "todo_update" in event_kinds:
        print("[OK] 收到 todo_update")
    else:
        print("[WARN] 未收到 todo_update")

    if "done" in event_kinds:
        print("[OK] 收到 done")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))