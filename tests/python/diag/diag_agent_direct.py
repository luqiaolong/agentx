"""诊断脚本：手动构建 DeepAgent 跑一整个 write_file 流，观察是否中断。"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback
from typing import Any

sys.path.insert(0, "D:/java/agentprojects/agentx/backend")
os.environ.setdefault("AGENTX_OPENAI_API_KEY", "sk-cp-GWq28SFWokQwN0215z823vuYde3sEkFOID817ucPGgAu8w1yZlNffPnGJxAtGDluv4fid40J3FA1i0sZUSuwNdi3uEj2h5sQVCTLSSlBgIhBOe2m4fpbQhA")
os.environ.setdefault("AGENTX_OPENAI_BASE_URL", "https://api.minimax.chat/v1")
os.environ.setdefault("AGENTX_DEFAULT_MODEL", "minimax-m3")


async def main():
    from langgraph.prebuilt import create_react_agent
    from langgraph.checkpoint.memory import MemorySaver
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

    from app.llm import get_chat_model
    from app.subagents.code_agent import _make_fs_tools
    from app.subagents.rag_agent import _make_rag_tools
    from app.subagents.web_agent import _make_web_tools

    thread_id = f"diag-{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}

    print(f"[setup] thread_id={thread_id}")

    llm = get_chat_model(temperature=0.3, streaming=True)
    tools = [
        *_make_fs_tools(thread_id),
        *_make_rag_tools(thread_id),
        *_make_web_tools(thread_id),
    ]
    print(f"[setup] {len(tools)} tools loaded")

    agent = create_react_agent(
        llm,
        tools,
        name="diag_agent",
        interrupt_before=["tools"],
        checkpointer=MemorySaver(),
    )

    inputs = {"messages": [HumanMessage(content="在 data/workspace/ 目录下创建一个文件 diag_test.txt，内容写 'diag-test'")]}

    print("\n=== 第 1 次 astream（initial） ===\n")
    state_count = 0
    interrupted_raised = False
    try:
        async for state in agent.astream(inputs, config=config, stream_mode="values"):
            state_count += 1
            msgs = state.get("messages", [])
            last = msgs[-1] if msgs else None
            print(f"  [state #{state_count}] type={type(last).__name__}", end="")
            if isinstance(last, AIMessage):
                tc = getattr(last, "tool_calls", None)
                print(f" tool_calls={[t.get('name') for t in tc] if tc else None}")
            elif isinstance(last, ToolMessage):
                print(f" tool={last.name} content={str(last.content)[:80]!r}")
            else:
                print(f" content={str(getattr(last, 'content', ''))[:80]!r}")
    except Exception as e:
        interrupted_raised = True
        print(f"  [exception] {type(e).__name__}: {e}")

    print(f"\n[after initial astream] states={state_count} interrupted={interrupted_raised}")

    # 检查 state.next
    state_snapshot = agent.get_state(config)
    print(f"  state.next: {state_snapshot.next}")
    print(f"  state.values.messages count: {len(state_snapshot.values.get('messages', []))}")
    last_msg = state_snapshot.values.get("messages", [{}])[-1]
    if hasattr(last_msg, "tool_calls"):
        print(f"  last_msg tool_calls: {last_msg.tool_calls}")


if __name__ == "__main__":
    asyncio.run(main())