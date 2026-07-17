"""完全模拟 run_deep_path，调用 _stream_agent_events。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any, AsyncIterator

sys.path.insert(0, "D:/java/agentprojects/agentx/backend")
os.environ.setdefault("AGENTX_OPENAI_API_KEY", "sk-cp-GWq28SFWokQwN0215z823vuYde3sEkFOID817ucPGgAu8w1yZlNffPnGJxAtGDluv4fid40J3FA1i0sZUSuwNdi3uEj2h5sQVCTLSSlBgIhBOe2m4fpbQhA")
os.environ.setdefault("AGENTX_OPENAI_BASE_URL", "https://api.minimax.chat/v1")
os.environ.setdefault("AGENTX_DEFAULT_MODEL", "minimax-m3")


# 复制 deep_path.py 中的关键函数（避免循环 import）
DANGEROUS_TOOLS: set[str] = {"edit_file", "write_file", "shell_exec"}
_DEEP_SYSTEM_PROMPT = (
    "你是一个强大的个人助理。你可以读写文件、搜索知识库、搜索网页。"
    "执行危险操作（写文件、执行命令）前需要用户审批。"
    "请根据用户任务规划步骤，调用合适的工具完成。"
)


def build_deep_agent(thread_id: str) -> Any:
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.prebuilt import create_react_agent
    from app.llm import get_chat_model
    from app.deepagent.subagents.code_agent import _make_fs_tools
    from app.deepagent.subagents.rag_agent import _make_rag_tools
    from app.deepagent.subagents.web_agent import _make_web_tools

    model = get_chat_model(temperature=0.3, streaming=True)
    tools = [
        *_make_fs_tools(thread_id),
        *_make_rag_tools(thread_id),
        *_make_web_tools(thread_id),
    ]
    checkpointer = MemorySaver()
    return create_react_agent(
        model,
        tools,
        name="deep_agent",
        prompt=_DEEP_SYSTEM_PROMPT,
        interrupt_before=["tools"],
        checkpointer=checkpointer,
    )


def _is_interrupted(agent, config):
    state = agent.get_state(config)
    if not state or not state.next:
        return False
    return "tools" in state.next


def _get_pending_tool_calls(agent, config):
    state = agent.get_state(config)
    if not state or not state.values:
        return []
    msgs = state.values.get("messages", [])
    if not msgs:
        return []
    last = msgs[-1]
    tcs = getattr(last, "tool_calls", None) or []
    return list(tcs)


def _make_approval_event(tool_call, thread_id):
    name = tool_call.get("name", "unknown")
    args = tool_call.get("args", {})
    preview = f"将调用 {name}"
    return {
        "event": "approval_request",
        "data": json.dumps({
            "thread_id": thread_id,
            "tool_name": name,
            "args": args,
            "preview": preview,
        }, ensure_ascii=False),
    }


async def _stream_agent_events(agent, inputs, config):
    from langchain_core.messages import AIMessage, ToolMessage
    from app.utils.text import strip_think

    async for state in agent.astream(inputs, config=config, stream_mode="values"):
        msgs = state.get("messages", []) if hasattr(state, "get") else []
        if not msgs:
            continue
        last = msgs[-1]
        if isinstance(last, ToolMessage):
            yield {"event": "todo_update", "data": json.dumps({"todos": [{"text": f"工具 {last.name} 完成", "done": True}]}, ensure_ascii=False)}
        elif isinstance(last, AIMessage):
            if getattr(last, "tool_calls", None):
                for tc in last.tool_calls:
                    tc_name = tc.get("name", tc.get("tool", "unknown")) if isinstance(tc, dict) else "unknown"
                    yield {"event": "todo_update", "data": json.dumps({"todos": [{"text": f"调用工具: {tc_name}", "done": False}]}, ensure_ascii=False)}
            elif getattr(last, "content", ""):
                content = last.content
                if isinstance(content, list):
                    content = "".join(b if isinstance(b, str) else b.get("text", "") if isinstance(b, dict) else "" for b in content)
                text = strip_think(content if isinstance(content, str) else str(content))
                if text:
                    yield {"event": "token", "data": text}


async def run_deep_path_sim(state, message):
    thread_id = state.get("thread_id", "")
    config = {"configurable": {"thread_id": thread_id or "deep-default"}}
    inputs = {"messages": [{"role": "user", "content": message}]}

    agent = build_deep_agent(thread_id)

    print("[sim] initial astream...")
    async for sse in _stream_agent_events(agent, inputs, config):
        yield sse
        # print state right after yield
        snap = agent.get_state(config)
        print(f"[sim]   after yield: state.next={snap.next}")

    print(f"[sim] after initial astream: state.next={agent.get_state(config).next}")

    iteration = 0
    while iteration < 10:
        iteration += 1
        if not _is_interrupted(agent, config):
            print(f"[sim] iter {iteration}: NOT interrupted, break")
            break
        pending = _get_pending_tool_calls(agent, config)
        print(f"[sim] iter {iteration}: interrupted, pending={[tc.get('name') for tc in pending]}")
        dangerous = [tc for tc in pending if tc.get("name") in DANGEROUS_TOOLS]
        if dangerous:
            yield _make_approval_event(dangerous[0], thread_id)
            print(f"[sim]   → yielded approval_request, waiting for user (sim returns)")
            return  # 模拟 _await_approval 阻塞
        break

    print("[sim] resuming with None")
    async for sse in _stream_agent_events(agent, None, config):
        yield sse


async def main():
    from app.utils.security import get_sandbox

    thread_id = f"sim-{int(time.time())}"
    sandbox = get_sandbox()
    sandbox.authorize(thread_id, "D:/java/agentprojects/agentx/data/workspace", writable=True)
    print(f"[setup] authorized thread={thread_id}")

    state = {
        "thread_id": thread_id,
        "messages": [{"role": "user", "content": "在 data/workspace/ 目录下创建一个文件 sim_test.txt，内容写 'sim-test'"}],
        "classification": "DEEP_TASK",
    }

    print("\n=== run_deep_path simulation ===\n")
    n = 0
    async for evt in run_deep_path_sim(state, state["messages"][0]["content"]):
        n += 1
        print(f"  [evt #{n}] {evt['event']} {evt['data'][:120]!r}")


if __name__ == "__main__":
    asyncio.run(main())