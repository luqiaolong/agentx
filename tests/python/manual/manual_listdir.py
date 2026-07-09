"""测试：触发 list_dir (safe tool)，看是否能在 SSE 流中看到 todo_update 然后 done。"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8123"


def collect_sse(body: dict, label: str, timeout: float = 60.0) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    with httpx.Client(timeout=timeout) as c:
        with c.stream("POST", f"{BASE}/api/chat", json=body) as r:
            print(f"[{label}] status={r.status_code}")
            buf = b""
            for chunk in r.iter_bytes():
                buf += chunk
                text = buf.decode("utf-8", errors="replace").replace("\r\n", "\n")
                while "\n\n" in text:
                    block, text = text.split("\n\n", 1)
                    evt, data = _parse_sse_block(block)
                    if evt:
                        elapsed = time.monotonic()
                        print(f"[{label}] [{elapsed:.2f}s] {evt}: {data[:150]!r}")
                        events.append((evt, data))
                        buf = text.encode("utf-8")
                if b"event: done" in buf:
                    break
    return events


def _parse_sse_block(block: str) -> tuple[str, str]:
    evt = ""
    data = ""
    for ln in block.split("\n"):
        ln = ln.strip()
        if ln.startswith("event: "):
            evt = ln[7:].strip()
        elif ln.startswith("data: "):
            data = ln[6:]
    return evt, data


def main() -> int:
    thread_id = f"listdir-{int(time.time())}"

    # 授权 workspace
    r = httpx.post(
        f"{BASE}/api/sandbox/authorize",
        json={
            "thread_id": thread_id,
            "path": "D:/java/agentprojects/agentx/data/workspace",
            "writable": True,
        },
        timeout=10.0,
    )
    print(f"[setup] authorize: {r.status_code} {r.json()}")

    # 触发 list_dir (safe tool)
    print("\n--- Test 1: list_dir (safe) ---")
    events = collect_sse(
        {
            "message": "用 list_dir 工具列出 data/workspace 目录的内容",
            "thread_id": thread_id,
        },
        "listdir",
    )

    print(f"\n=== 共 {len(events)} 事件 ===")
    for evt, data in events:
        print(f"  {evt}: {data[:120]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())