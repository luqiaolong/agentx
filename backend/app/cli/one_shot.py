"""One-shot 单次任务模式。

``agentx "提问内容"`` 直接输出结果后退出。
"""

from __future__ import annotations

import json
import sys

from app.cli.renderer import EventRenderer
from app.cli.repl import consume_events
from app.cli.store import apply_config_to_env

__all__ = ["run_one_shot"]


async def run_one_shot(
    message: str,
    *,
    agent_mode: str,
    thread_id: str,
    workspace_path: str | None,
    verbose: bool,
    json_mode: bool,
) -> int:
    """One-shot 单次任务模式。

    Returns:
        退出码：0 成功，1 错误。
    """
    from app.config import get_settings, reload_settings
    from app.memory.checkpointer import aclose_checkpointer, get_async_checkpointer
    from app.router.graph import run_router

    # 加载配置
    apply_config_to_env()
    reload_settings()
    get_settings()  # 触发 settings 缓存预热

    # 获取 checkpointer
    try:
        checkpointer = await get_async_checkpointer()
    except Exception as exc:
        if verbose:
            print(f"[警告] checkpointer 初始化失败: {exc}", file=sys.stderr)
        checkpointer = None

    renderer = EventRenderer(verbose=verbose, json_mode=json_mode)

    try:
        event_gen = run_router(
            message=message,
            thread_id=thread_id,
            checkpointer=checkpointer,
            agent_mode=agent_mode,
            workspace_path=workspace_path,
        )
        success = await consume_events(event_gen, renderer, thread_id, is_repl=False)
    except KeyboardInterrupt:
        # Ctrl+C 优雅退出
        if json_mode:
            print(json.dumps([{"event": "error", "data": "用户中断"}], ensure_ascii=False))
        else:
            print("\n[中断]")
        return 1
    except Exception as exc:
        if json_mode:
            print(json.dumps([{"event": "error", "data": str(exc)}], ensure_ascii=False))
        else:
            print(f"[错误] {exc}", file=sys.stderr)
        return 1
    finally:
        await aclose_checkpointer()

    if json_mode:
        renderer.flush_json()

    return 0 if success else 1
