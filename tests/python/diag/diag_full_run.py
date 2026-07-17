"""完整重现 run_deep_path 流程：构建 agent → initial astream → 检查 interrupt → yield approval → 等审批 → resume。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "D:/java/agentprojects/agentx/backend")
os.environ.setdefault("AGENTX_OPENAI_API_KEY", "sk-cp-GWq28SFWokQwN0215z823vuYde3sEkFOID817ucPGgAu8w1yZlNffPnGJxAtGDluv4fid40J3FA1i0sZUSuwNdi3uEj2h5sQVCTLSSlBgIhBOe2m4fpbQhA")
os.environ.setdefault("AGENTX_OPENAI_BASE_URL", "https://api.minimax.chat/v1")
os.environ.setdefault("AGENTX_DEFAULT_MODEL", "minimax-m3")


# 模拟 _pending_approvals
_pending_approvals: dict[str, bool] = {}


async def main():
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.prebuilt import create_react_agent
    from langchain_core.messages import AIMessage, ToolMessage

    from app.llm import get_chat_model
    from app.deepagent.subagents.code_agent import _make_fs_tools
    from app.deepagent.subagents.rag_agent import _make_rag_tools
    from app.deepagent.subagents.web_agent import _make_web_tools
    from app.utils.text import strip_think
    from app.utils.security import get_sandbox

    thread_id = f"full-{int(time.time())}"
    sandbox = get_sandbox()
    sandbox.authorize(thread_id, "D:/java/agentprojects/agentx/data/workspace", writable=True)

    model = get_chat_model(temperature=0.3, streaming=True)
    tools = [
        *_make_fs_tools(thread_id),
        *_make_rag_tools(thread_id),
        *_make_web_tools(thread_id),
    ]
    agent = create_react_agent(
        model,
        tools,
        name="deep_agent",
        prompt="你是个人助理。可以读写文件。",
        interrupt_before=["tools"],
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": thread_id}}
    inputs = {"messages": [{"role": "user", "content": "在 data/workspace/ 目录下创建一个文件 full_diag.txt，内容写 'full-diag'"}]}

    DANGEROUS = {"edit_file", "write_file", "shell_exec"}

    async def stream_events(inp):
        async for state in agent.astream(inp, config=config, stream_mode="values"):
            msgs = state.get("messages", []) if hasattr(state, "get") else []
            if not msgs:
                continue
            last = msgs[-1]
            if isinstance(last, ToolMessage):
                yield {"event": "todo_update", "data": f"工具 {last.name} 完成"}
            elif isinstance(last, AIMessage):
                if getattr(last, "tool_calls", None):
                    for tc in last.tool_calls:
                        yield {"event": "todo_update", "data": f"调用工具: {tc.get('name', '?')}"}
                elif getattr(last, "content", ""):
                    text = strip_think(str(last.content))
                    if text:
                        yield {"event": "token", "data": text[:60]}

    def is_interrupted():
        s = agent.get_state(config)
        if not s or not s.next:
            return False
        return "tools" in s.next

    def pending_tool_calls():
        s = agent.get_state(config)
        if not s or not s.values:
            return []
        msgs = s.values.get("messages", [])
        if not msgs:
            return []
        last = msgs[-1]
        tcs = getattr(last, "tool_calls", None) or []
        return list(tcs)

    print("\n=== Step 1: initial astream ===\n")
    n = 0
    async for evt in stream_events(inputs):
        n += 1
        print(f"  [evt #{n}] {evt['event']} {evt['data'][:80]!r}")

    print(f"\n[after step 1] events={n}")
    print(f"  state.next = {agent.get_state(config).next}")
    print(f"  is_interrupted() = {is_interrupted()}")

    if is_interrupted():
        pending = pending_tool_calls()
        print(f"  pending tool_calls: {[(tc.get('name'), tc.get('args')) for tc in pending]}")
        dangerous = [tc for tc in pending if tc.get("name") in DANGEROUS]
        if dangerous:
            tc = dangerous[0]
            print(f"\n=== Step 2: yield approval_request for {tc.get('name')} ===")
            # 模拟 yield approval_request 事件
            approval_event = {
                "event": "approval_request",
                "data": json.dumps({
                    "thread_id": thread_id,
                    "tool_name": tc.get("name"),
                    "args": tc.get("args"),
                    "preview": f"将调用 {tc.get('name')}",
                }, ensure_ascii=False),
            }
            print(f"  {approval_event}")

            print("\n=== Step 3: simulate user approval (True) ===")
            _pending_approvals[thread_id] = True

            print("\n=== Step 4: resume agent with None ===")
            async for evt in stream_events(None):
                n += 1
                print(f"  [evt #{n}] {evt['event']} {evt['data'][:80]!r}")

            print(f"\n[after step 4] state.next = {agent.get_state(config).next}")
        else:
            print("  no dangerous tool_calls")
    else:
        print("\n  ⚠ graph NOT interrupted - agent completed full flow")


if __name__ == "__main__":
    asyncio.run(main())