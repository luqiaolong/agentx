"""诊断：完整重现 SSE 流程 - 模拟 run_router → run_deep_path 的事件流。

策略：避开 router 的循环 import，直接调用 _stream_agent_events + run_deep_path 的循环逻辑。
"""

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

    thread_id = f"diag-full-{int(time.time())}"
    sandbox = get_sandbox()
    sandbox.authorize(thread_id, "D:/java/agentprojects/agentx/data/workspace", writable=True)
    print(f"[setup] authorized thread={thread_id}")

    model = get_chat_model(temperature=0.3, streaming=True)
    tools = [
        *_make_fs_tools(thread_id),
        *_make_rag_tools(thread_id),
        *_make_web_tools(thread_id),
    ]
    print(f"[setup] {len(tools)} tools")

    agent = create_react_agent(
        model,
        tools,
        name="deep_agent",
        prompt="你是一个强大的个人助理。你可以读写文件、搜索知识库、搜索网页。",
        interrupt_before=["tools"],
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": thread_id}}
    inputs = {"messages": [{"role": "user", "content": "在 data/workspace/ 目录下创建一个文件 diag_full.txt，内容写 'diag-full'"}]}

    print("\n=== run_deep_path simulation ===\n")

    DANGEROUS_TOOLS = {"edit_file", "write_file", "shell_exec"}
    event_count = 0

    def _is_interrupted():
        state = agent.get_state(config)
        if not state or not state.next:
            return False
        return "tools" in state.next

    def _get_pending_tool_calls():
        state = agent.get_state(config)
        if not state or not state.values:
            return []
        msgs = state.values.get("messages", [])
        if not msgs:
            return []
        last_msg = msgs[-1]
        tool_calls = getattr(last_msg, "tool_calls", None) or []
        return list(tool_calls)

    async def _stream_agent_events(inp):
        async for state in agent.astream(inp, config=config, stream_mode="values"):
            messages = state.get("messages", []) if hasattr(state, "get") else []
            if not messages:
                continue
            last = messages[-1]
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

    # Step 1: initial stream
    print("--- step 1: initial astream ---")
    try:
        async for evt in _stream_agent_events(inputs):
            event_count += 1
            print(f"  [evt #{event_count}] {evt['event']} {evt['data'][:80]!r}")
    except Exception as e:
        print(f"  [exception in step 1] {type(e).__name__}: {e}")

    state = agent.get_state(config)
    print(f"\n[after step 1] state.next={state.next}")

    # Step 2: loop check interrupt
    print("\n--- step 2: interrupt check ---")
    if _is_interrupted():
        print("  ✓ _is_interrupted = True (graph paused)")
        pending = _get_pending_tool_calls()
        print(f"  pending tool_calls: {[tc.get('name') for tc in pending]}")
        dangerous = [tc for tc in pending if tc.get("name") in DANGEROUS_TOOLS]
        print(f"  dangerous tool_calls: {[tc.get('name') for tc in dangerous]}")
        if dangerous:
            tc = dangerous[0]
            print(f"  → would yield approval_request for {tc.get('name')}")
    else:
        print("  ✗ _is_interrupted = False (graph NOT paused!)")


if __name__ == "__main__":
    asyncio.run(main())