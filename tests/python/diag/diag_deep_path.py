"""诊断脚本：直接调用 run_deep_path，看每一步的事件和 state.next。"""

from __future__ import annotations

import asyncio
import sys
import time
import traceback

# 把 backend 加进 path
sys.path.insert(0, "D:/java/agentprojects/agentx/backend")

# 显式注入环境（credential 必须有，LLM 才能跑）
import os
os.environ.setdefault("AGENTX_OPENAI_API_KEY", "sk-cp-GWq28SFWokQwN0215z823vuYde3sEkFOID817ucPGgAu8w1yZlNffPnGJxAtGDluv4fid40J3FA1i0sZUSuwNdi3uEj2h5sQVCTLSSlBgIhBOe2m4fpbQhA")
os.environ.setdefault("AGENTX_OPENAI_BASE_URL", "https://api.minimax.chat/v1")
os.environ.setdefault("AGENTX_DEFAULT_MODEL", "minimax-m3")


async def main():
    from app.paths.deep_path import (
        run_deep_path,
        build_deep_agent,
        DANGEROUS_TOOLS,
        _is_interrupted,
    )
    from app.utils.security import get_sandbox
    from app.router.state import RouterState

    thread_id = f"diag-{int(time.time())}"

    # 授权 workspace
    sandbox = get_sandbox()
    sandbox.authorize(thread_id, "D:/java/agentprojects/agentx/data/workspace", writable=True)
    print(f"[setup] sandbox authorized thread={thread_id}")

    state: RouterState = {
        "thread_id": thread_id,
        "messages": [{"role": "user", "content": "在 data/workspace/ 目录下创建一个文件 diag_test.txt，内容写 'diag-test'"}],
        "classification": "DEEP_TASK",
    }

    print("\n=== 开始 run_deep_path ===\n")
    event_count = 0
    try:
        async for event in run_deep_path(state, state["messages"][0]["content"]):
            event_count += 1
            kind = event.get("event", "?")
            data = event.get("data", "")[:200]
            print(f"[event #{event_count}] kind={kind} data={data!r}")
    except Exception as e:
        print(f"[exception] {type(e).__name__}: {e}")
        traceback.print_exc()

    print(f"\n=== 共收到 {event_count} 个事件 ===")


if __name__ == "__main__":
    asyncio.run(main())