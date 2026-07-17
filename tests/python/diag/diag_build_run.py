"""诊断：使用 build_deep_agent + _stream_agent_events 看实际行为。"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

sys.path.insert(0, "D:/java/agentprojects/agentx/backend")
os.environ.setdefault("AGENTX_OPENAI_API_KEY", "sk-cp-GWq28SFWokQwN0215z823vuYde3sEkFOID817ucPGgAu8w1yZlNffPnGJxAtGDluv4fid40J3FA1i0sZUSuwNdi3uEj2h5sQVCTLSSlBgIhBOe2m4fpbQhA")
os.environ.setdefault("AGENTX_OPENAI_BASE_URL", "https://api.minimax.chat/v1")
os.environ.setdefault("AGENTX_DEFAULT_MODEL", "minimax-m3")


async def main():
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.prebuilt import create_react_agent
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

    from app.llm import get_chat_model
    from app.deepagent.subagents.code_agent import _make_fs_tools
    from app.deepagent.subagents.rag_agent import _make_rag_tools
    from app.deepagent.subagents.web_agent import _make_web_tools
    from app.utils.text import ThinkFilter, extract_chunk_text

    thread_id = f"diag-build-{int(time.time())}"

    print(f"[setup] thread_id={thread_id}")

    model = get_chat_model(temperature=0.3, streaming=True)
    tools = [
        *_make_fs_tools(thread_id),
        *_make_rag_tools(thread_id),
        *_make_web_tools(thread_id),
    ]
    print(f"[setup] {len(tools)} tools: {[t.name for t in tools]}")

    agent = create_react_agent(
        model,
        tools,
        name="deep_agent",
        prompt="你是一个强大的个人助理。你可以读写文件、搜索知识库、搜索网页。",
        interrupt_before=["tools"],
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": thread_id}}
    inputs = {"messages": [HumanMessage(content="在 data/workspace/ 目录下创建一个文件 diag_test2.txt，内容写 'diag-test'")]}

    print("\n=== 第 1 次 _stream_agent_events ===\n")
    event_count = 0

    async def stream_agent(agent, inputs, config):
        async for state in agent.astream(inputs, config=config, stream_mode="values"):
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
                    yield {"event": "token", "data": str(last.content)[:50]}

    try:
        async for evt in stream_agent(agent, inputs, config):
            event_count += 1
            print(f"  [event #{event_count}] {evt['event']} data={evt['data']!r}")
            if event_count > 20:
                print("  ... stopping after 20 events")
                break
    except Exception as e:
        print(f"  [exception] {type(e).__name__}: {e}")

    state = agent.get_state(config)
    print(f"\n[after initial] events={event_count}")
    print(f"  state.next: {state.next}")


if __name__ == "__main__":
    asyncio.run(main())