"""REPL 交互模式。

持续读取用户输入，``/`` 开头走内建命令，其余发送给 ``run_router``。
"""

from __future__ import annotations

import asyncio
import sys
from typing import AsyncIterator

from app.cli.approval import handle_approval
from app.cli.commands import CommandAction, CommandContext, handle_command
from app.cli.renderer import EventRenderer
from app.cli.store import apply_config_to_env

__all__ = ["run_repl", "consume_events"]

__version__ = "0.2.0"


async def consume_events(
    event_generator: AsyncIterator[dict],
    renderer: EventRenderer,
    thread_id: str,
    *,
    is_repl: bool = False,
) -> bool:
    """消费 SSE 事件流，处理审批交互。

    Returns:
        True 表示正常完成，False 表示出错。

    Note:
        REPL 模式下不捕获 KeyboardInterrupt，交由外层 ``run_repl`` 处理
        （第一次 Ctrl+C 暂停，第二次退出）。
    """
    try:
        async for event in event_generator:
            event_type = event.get("event", "")

            # 审批请求：暂停并等待用户输入
            if event_type == "approval_request":
                renderer.render(event)
                await handle_approval(thread_id)
                continue

            renderer.render(event)

            if event_type == "error":
                return False

    except KeyboardInterrupt:
        if is_repl:
            raise  # 交由 run_repl 统一处理
        print("\n[中断]")
        return False
    except Exception as exc:
        print(f"\n[错误] {exc}", file=sys.stderr)
        return False

    return True


async def run_repl(
    *,
    agent_mode: str,
    thread_id: str,
    workspace_path: str | None,
    verbose: bool,
    json_mode: bool,
) -> None:
    """REPL 交互模式。"""
    from app.config import get_settings, reload_settings
    from app.memory.checkpointer import aclose_checkpointer, get_async_checkpointer
    from app.router.graph import run_router

    # 加载配置
    apply_config_to_env()
    reload_settings()
    settings = get_settings()

    # 打印 banner
    if not json_mode:
        _print_banner(agent_mode, thread_id, settings.default_model)

    # 获取 checkpointer
    try:
        checkpointer = await get_async_checkpointer()
    except Exception as exc:
        print(f"[警告] checkpointer 初始化失败: {exc}", file=sys.stderr)
        checkpointer = None

    renderer = EventRenderer(verbose=verbose, json_mode=json_mode)

    current_mode = agent_mode

    try:
        while True:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, input, "> "
                )
            except KeyboardInterrupt:
                print("\n再见!")
                break
            except EOFError:
                print("\n再见!")
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            # 内建命令
            if user_input.startswith("/"):
                ctx = CommandContext(
                    current_mode=current_mode,
                    thread_id=thread_id,
                    checkpointer=checkpointer,
                    workspace_path=workspace_path,
                )
                result = await handle_command(user_input, ctx)
                if result.action == CommandAction.QUIT:
                    break
                if result.action == CommandAction.SWITCH_MODE and result.new_mode:
                    current_mode = result.new_mode
                continue

            # 发送给 LLM
            renderer.reset()
            try:
                event_gen = run_router(
                    message=user_input,
                    thread_id=thread_id,
                    checkpointer=checkpointer,
                    agent_mode=current_mode,
                    workspace_path=workspace_path,
                )
                await consume_events(event_gen, renderer, thread_id, is_repl=True)
            except KeyboardInterrupt:
                from app.approval.state import set_pause
                await set_pause(thread_id)
                print("\n[已暂停，再次 Ctrl+C 退出]")
            except Exception as exc:
                if json_mode:
                    renderer.add_error(str(exc))
                else:
                    print(f"\n[错误] {exc}", file=sys.stderr)

            if json_mode:
                renderer.flush_json()
    finally:
        await aclose_checkpointer()


def _print_banner(agent_mode: str, thread_id: str, model: str) -> None:
    """打印 REPL banner。"""
    print(f"AgentX CLI v{__version__} | model: {model} | mode: {agent_mode} | thread: {thread_id}")
    print("输入 /help 查看可用命令，Ctrl+C 退出\n")
