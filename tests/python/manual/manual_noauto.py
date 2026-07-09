"""测试：不自动 approve，看 SSE 流是否会持续等待。"""

from __future__ import annotations

import asyncio
import sys
import time

import httpx

BASE = "http://127.0.0.1:8123"


async def main():
    thread_id = f"wait-{int(time.time())}"

    # 授权
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

    # 发送请求，**不**自动 approve
    print(f"\n--- Sending chat, NOT auto-approving ---")
    print(f"--- Watching for up to 30s ---")
    async with httpx.AsyncClient(timeout=60.0) as c:
        try:
            async with c.stream(
                "POST",
                f"{BASE}/api/chat",
                json={
                    "message": "用 list_dir 列出 data/workspace 目录",
                    "thread_id": thread_id,
                },
            ) as r:
                print(f"[wait] status={r.status_code}")
                start = time.monotonic()
                events_received = []
                buf = b""
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    elapsed = time.monotonic() - start
                    print(f"[wait] [{elapsed:.2f}s] received {len(chunk)} bytes")
                    text = buf.decode("utf-8", errors="replace").replace("\r\n", "\n")
                    while "\n\n" in text:
                        block, text = text.split("\n\n", 1)
                        lines = [ln for ln in block.split("\n") if ln.strip()]
                        if not lines:
                            continue
                        evt = ""
                        data = ""
                        for ln in lines:
                            if ln.startswith("event: "):
                                evt = ln[7:].strip()
                            elif ln.startswith("data: "):
                                data = ln[6:]
                        if evt:
                            print(f"[wait] [{elapsed:.2f}s] event={evt} data={data[:150]!r}")
                            events_received.append(evt)
                            buf = text.encode("utf-8")
                            if elapsed > 30:
                                print("[wait] timeout, aborting")
                                await c.post(
                                    f"{BASE}/api/chat/abort",
                                    json={"thread_id": thread_id},
                                )
                                break
                    if elapsed > 30:
                        break
                    if b"event: done" in buf:
                        print("[wait] got done event")
                        break
        except Exception as e:
            print(f"[wait] exception: {e}")

    print(f"\n=== events received: {events_received}")


if __name__ == "__main__":
    asyncio.run(main())