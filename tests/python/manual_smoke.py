"""端到端冒烟：跑三条路径 + /reset，逐个断言事件序列。"""

from __future__ import annotations

import json
import sys

import httpx

BASE = "http://127.0.0.1:8123"


def collect_sse(body: dict, label: str, timeout: float = 120.0) -> tuple[list[str], list[dict], str]:
    """收 SSE 事件：返回 (tokens, structured_events, done_data)。"""
    tokens: list[str] = []
    structured: list[dict] = []
    done_data = ""
    with httpx.Client(timeout=timeout) as c:
        with c.stream("POST", f"{BASE}/api/chat", json=body) as r:
            assert r.status_code == 200, f"[{label}] status={r.status_code}"
            buf = b""
            for chunk in r.iter_bytes():
                buf += chunk
                # 累计直到含 done 事件
                if b"event: done" in buf:
                    break
            text = buf.decode("utf-8", errors="replace").replace("\r\n", "\n")
            # 解析 SSE（每个事件以空行分隔）
            for block in text.split("\n\n"):
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
                if evt == "token":
                    tokens.append(data)
                elif evt == "done":
                    done_data = data
                elif evt in ("todo_update", "approval_request", "error"):
                    try:
                        structured.append({"event": evt, "data": json.loads(data)})
                    except Exception:  # noqa: BLE001
                        structured.append({"event": evt, "data": data})
    return tokens, structured, done_data


def main() -> int:
    failed: list[str] = []

    # Path A: CHAT
    try:
        tokens, _, done = collect_sse(
            {"message": "你好", "thread_id": "smoke-A"}, "A"
        )
        joined = "".join(tokens)
        assert tokens, f"A: 无 token (got {len(tokens)})"
        assert "<think>" not in joined, f"A: 仍有 <think> 块: {joined!r}"
        assert done == "{}", f"A: done 格式错: {done!r}"
        print(f"[A] OK tokens={len(tokens)} content={joined!r}")
    except AssertionError as e:
        failed.append(f"[A] {e}")

    # Path B: SINGLE_TOOL
    try:
        tokens, structured, done = collect_sse(
            {"message": "列出 data/workspace 目录的内容", "thread_id": "smoke-B"}, "B"
        )
        assert tokens or structured, f"B: 无事件 (tokens={len(tokens)} struct={len(structured)})"
        assert done == "{}", f"B: done 格式错: {done!r}"
        kinds = [s["event"] for s in structured]
        print(f"[B] OK tokens={len(tokens)} structured={kinds}")
    except AssertionError as e:
        failed.append(f"[B] {e}")

    # Path C: DEEP_TASK
    try:
        tokens, structured, done = collect_sse(
            {"message": "帮我分析这个项目结构并规划优化方案", "thread_id": "smoke-C"},
            "C",
        )
        joined = "".join(tokens)
        assert tokens or structured, f"C: 无事件"
        assert "<think>" not in joined, f"C: 仍有 <think> 块: {joined[:200]!r}"
        assert done == "{}", f"C: done 格式错: {done!r}"
        print(f"[C] OK tokens={len(tokens)} content[:80]={joined[:80]!r}")
    except AssertionError as e:
        failed.append(f"[C] {e}")

    # /reset
    try:
        tokens, _, done = collect_sse(
            {"message": "/reset", "thread_id": "smoke-reset"}, "R"
        )
        joined = "".join(tokens)
        assert joined and ("已清空" in joined), f"R: 提示缺失: {joined!r}"
        assert done == "{}", f"R: done 格式错: {done!r}"
        print(f"[R] OK content={joined!r}")
    except AssertionError as e:
        failed.append(f"[R] {e}")

    # /api/health
    try:
        r = httpx.get(f"{BASE}/api/health", timeout=10.0)
        assert r.status_code == 200
        h = r.json()
        assert h.get("status") == "ok"
        print(f"[health] OK embedding={h['embedding']['status']} milvus={h['milvus']['status']}")
    except AssertionError as e:
        failed.append(f"[health] {e}")

    if failed:
        print("\n=== FAILED ===")
        for f in failed:
            print(f)
        return 1
    print("\n=== ALL SMOKE PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())