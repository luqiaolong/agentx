"""AgentX CLI 主入口。

支持两种使用模式：
- REPL 交互模式：``agentx`` 进入持续对话
- One-shot 单次任务：``agentx "提问内容"`` 直接输出后退出

默认 agent_mode 为 ``coding``。通过 ``--work`` / ``--coding`` / ``--coding-team`` 切换。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from app.cli_render import EventRenderer
from app.cli_store import apply_config_to_env

__version__ = "0.2.0"

__all__ = ["main"]


# ============================================================
# 参数解析
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentx",
        description="AgentX CLI — 终端 AI 助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  agentx                          # 进入 REPL（默认 coding 模式）
  agentx "写一个快速排序"          # One-shot 单次任务
  agentx --work "帮我规划行程"     # 使用 work 模式
  agentx --coding-team "重构模块"  # 使用 coding_team 模式
  git diff | agentx "写 commit"   # 管道输入
  agentx "分析数据" --json         # JSON 结构化输出
""",
    )
    parser.add_argument(
        "message",
        nargs="?",
        default=None,
        help="One-shot 模式的提问内容（省略则进入 REPL）",
    )
    parser.add_argument("--thread", default=None, help="复用已有会话 ID")
    parser.add_argument("--work", action="store_true", help="使用 work 模式（Supervisor 全能 agent）")
    parser.add_argument("--coding", action="store_true", help="使用 coding 模式（默认）")
    parser.add_argument("--coding-team", action="store_true", help="使用 coding_team 模式（AgentTeam 协作）")
    parser.add_argument("--workspace", "-w", default=None, help="绑定 workspace 绝对路径")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示 reasoning 和完整 tool_result")
    parser.add_argument("--json", dest="json_mode", action="store_true", help="JSON 结构化输出")
    parser.add_argument("--version", action="version", version=f"AgentX CLI v{__version__}")
    return parser


def _resolve_agent_mode(args: argparse.Namespace) -> str:
    """从命令行参数解析 agent_mode。"""
    if args.work:
        return "work"
    if args.coding_team:
        return "coding_team"
    # coding 是默认（包括 --coding 或均未指定）
    return "coding"


def _read_stdin_if_piped() -> str | None:
    """若 stdin 为管道输入，读取内容；否则返回 None。"""
    if sys.stdin.isatty():
        return None
    try:
        return sys.stdin.read().strip()
    except Exception:
        return None


# ============================================================
# 核心事件消费
# ============================================================

async def _consume_events(
    event_generator,
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
                await _handle_approval(thread_id)
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


async def _handle_approval(thread_id: str) -> None:
    """终端审批交互：阻塞等待用户输入。"""
    from app.approval.decision import ApprovalDecision
    from app.approval.state import submit_approval

    while True:
        try:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, input, "Approve? [y=批准 / n=拒绝 / o=仅一次 / s=会话级]: "
            )
        except (EOFError, KeyboardInterrupt):
            # 用户 Ctrl+C 或 Ctrl+D → 拒绝
            await submit_approval(thread_id, ApprovalDecision(approved=False, decision="deny"))
            print("[已拒绝]")
            return

        choice = user_input.strip().lower()
        if choice in ("y", "yes"):
            await submit_approval(thread_id, ApprovalDecision(approved=True, decision="approve"))
            return
        elif choice in ("n", "no"):
            await submit_approval(thread_id, ApprovalDecision(approved=False, decision="deny"))
            print("[已拒绝]")
            return
        elif choice in ("o", "once"):
            await submit_approval(thread_id, ApprovalDecision(approved=True, decision="once"))
            return
        elif choice in ("s", "session"):
            await submit_approval(thread_id, ApprovalDecision(approved=True, decision="session"))
            return
        else:
            print("请输入 y/n/o/s")


# ============================================================
# REPL 模式
# ============================================================

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
    paused = False  # Ctrl+C 暂停状态标记

    try:
      while True:
        try:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, input, "> "
            )
        except KeyboardInterrupt:
            if paused:
                print("\n再见!")
                break
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
            cmd_result = _handle_command(user_input, current_mode, thread_id, checkpointer, workspace_path)
            if cmd_result is None:
                break  # /quit
            if isinstance(cmd_result, tuple):
                current_mode = cmd_result[0]
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
            await _consume_events(event_gen, renderer, thread_id, is_repl=True)
            paused = False
        except KeyboardInterrupt:
            from app.approval.state import set_pause
            await set_pause(thread_id)
            paused = True
            print("\n[已暂停，再次 Ctrl+C 退出]")
        except Exception as exc:
            paused = False
            if json_mode:
                renderer._json_events.append({"event": "error", "data": str(exc)})
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


def _handle_command(
    cmd: str, current_mode: str, thread_id: str, checkpointer, workspace_path: str
) -> str | tuple[str] | None:
    """处理 REPL 内建命令。

    Returns:
        None → 退出 REPL
        str → 继续当前模式（命令已处理）
        tuple[str] → 切换到新模式
    """
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()

    if command in ("/quit", "/q"):
        return None

    if command == "/help":
        _print_help()
        return current_mode

    if command == "/reset":
        _cmd_reset(thread_id, checkpointer)
        return current_mode

    if command == "/mode":
        return _cmd_mode(parts, current_mode)

    if command == "/init":
        _cmd_init(workspace_path, thread_id)
        return current_mode

    if command == "/model":
        _cmd_model()
        return current_mode

    if command == "/models":
        _cmd_models()
        return current_mode

    if command == "/compact":
        asyncio.get_event_loop().create_task(_cmd_compact(thread_id, checkpointer))
        return current_mode

    if command == "/abort":
        _cmd_abort(thread_id)
        return current_mode

    if command == "/pause":
        _cmd_pause(thread_id)
        return current_mode

    if command == "/resume":
        _cmd_resume(thread_id)
        return current_mode

    if command == "/clear":
        _cmd_clear()
        return current_mode

    if command == "/thread":
        print(f"当前 thread_id: {thread_id}")
        return current_mode

    if command == "/threads":
        asyncio.get_event_loop().create_task(_cmd_threads())
        return current_mode

    # 未知命令
    print(f"未知命令: {command}（输入 /help 查看可用命令）")
    return current_mode


def _print_help() -> None:
    """打印帮助信息。"""
    print("可用命令:")
    print("  /reset              清空当前会话历史")
    print("  /mode <mode>        切换模式: work / coding / coding_team")
    print("  /init               授权当前目录为 workspace")
    print("  /model              显示当前模型")
    print("  /models             列出所有可用模型")
    print("  /compact            压缩会话历史（保留最近2条）")
    print("  /abort              中止当前生成")
    print("  /pause              暂停生成")
    print("  /resume             恢复生成")
    print("  /clear              清空终端屏幕")
    print("  /thread             显示当前 thread_id")
    print("  /threads            列出所有历史会话")
    print("  /quit (或 /q)       退出")
    print("  /help               显示此帮助")


def _cmd_reset(thread_id: str, checkpointer) -> None:
    """清空会话状态。"""
    from app.utils.security import get_sandbox

    if checkpointer and hasattr(checkpointer, "adelete_thread"):
        try:
            asyncio.get_event_loop().create_task(checkpointer.adelete_thread(thread_id))
            get_sandbox().clear(thread_id)
            print("[会话已重置]")
        except Exception as exc:
            print(f"[重置失败] {exc}")
    else:
        print("[checkpointer 不支持重置]")


def _cmd_mode(parts: list[str], current_mode: str) -> str | tuple[str]:
    """切换模式。"""
    if len(parts) < 2:
        print(f"当前模式: {current_mode}")
        print("用法: /mode <work|coding|coding_team>")
        return current_mode
    new_mode = parts[1].strip().lower()
    if new_mode in ("work", "coding", "coding_team"):
        print(f"模式已切换为 {new_mode}")
        return (new_mode,)
    else:
        print(f"无效模式: {new_mode}，可选: work / coding / coding_team")
        return current_mode


def _cmd_init(workspace_path: str, thread_id: str) -> None:
    """授权当前目录为 workspace。"""
    from app.utils.security import get_sandbox

    try:
        get_sandbox().authorize(workspace_path, thread_id)
        print(f"[已授权] {workspace_path} → thread {thread_id}")
    except Exception as exc:
        print(f"[授权失败] {exc}")


def _cmd_model() -> None:
    """显示当前模型。"""
    from app.config import get_settings

    settings = get_settings()
    print(f"当前模型: {settings.default_model}")
    if settings.openai_base_url:
        print(f"  base_url: {settings.openai_base_url}")
    if settings.max_output_tokens:
        print(f"  max_tokens: {settings.max_output_tokens}")


def _cmd_models() -> None:
    """列出所有可用模型（从 Tauri store 读取）。"""
    from app.cli_store import _read_config_json

    config = _read_config_json()
    if not config:
        print("[无法读取模型配置]")
        return

    models = config.get("models", {})
    entries = models.get("entries", [])
    active_id = models.get("activeId")

    if not entries:
        print("未配置模型")
        return

    print("可用模型:")
    for entry in entries:
        model_id = entry.get("id", "?")
        model_name = entry.get("model", "?")
        label = entry.get("label", "")
        provider = entry.get("providerId", "custom")
        marker = " *" if model_id == active_id else ""
        display = f"  [{provider}] {model_name}"
        if label:
            display += f" ({label})"
        print(f"{display}{marker}")
    print("\n* 表示当前激活模型")


async def _cmd_compact(thread_id: str, checkpointer) -> None:
    """压缩会话历史。"""
    from langchain_core.messages import SystemMessage
    from app.memory import summarize_messages
    from app.observability.logger import logger

    if not checkpointer:
        print("[checkpointer 未初始化]")
        return

    config = {"configurable": {"thread_id": thread_id}}

    try:
        if hasattr(checkpointer, "aget"):
            checkpoint = await checkpointer.aget(config)
        else:
            checkpoint = checkpointer.get(config)
    except Exception as exc:
        print(f"[加载失败] {exc}")
        return

    if not checkpoint:
        print("[无 checkpoint 可压缩]")
        return

    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    messages = list(channel_values.get("messages", []))
    if len(messages) < 4:
        print("[消息不足，无需压缩]")
        return

    to_compress = messages[:-2]
    keep_recent = messages[-2:]
    try:
        summary = await summarize_messages(to_compress)
    except Exception as exc:
        print(f"[摘要失败] {exc}")
        return

    new_messages = [SystemMessage(content=summary), *keep_recent]
    new_channel_values = {**channel_values, "messages": new_messages}
    new_checkpoint = {**checkpoint, "channel_values": new_channel_values}

    try:
        if hasattr(checkpointer, "aput"):
            await checkpointer.aput(config, new_checkpoint, {"messages": "any"}, [])
        elif hasattr(checkpointer, "put"):
            checkpointer.put(config, new_checkpoint, {"messages": "any"}, [])
        else:
            print("[checkpointer 不支持写回]")
            return
    except Exception as exc:
        print(f"[写回失败] {exc}")
        return

    print(f"[已压缩] {len(to_compress)} 条消息 → 摘要 ({len(summary)} 字符)")


def _cmd_abort(thread_id: str) -> None:
    """中止当前生成。"""
    from app.approval.state import set_abort

    try:
        asyncio.get_event_loop().create_task(set_abort(thread_id))
        print("[已发送中止信号]")
    except Exception as exc:
        print(f"[中止失败] {exc}")


def _cmd_pause(thread_id: str) -> None:
    """暂停生成。"""
    from app.approval.state import set_pause

    try:
        asyncio.get_event_loop().create_task(set_pause(thread_id))
        print("[已暂停]")
    except Exception as exc:
        print(f"[暂停失败] {exc}")


def _cmd_resume(thread_id: str) -> None:
    """恢复生成。"""
    from app.approval.state import clear_pause

    try:
        asyncio.get_event_loop().create_task(clear_pause(thread_id))
        print("[已恢复]")
    except Exception as exc:
        print(f"[恢复失败] {exc}")


def _cmd_clear() -> None:
    """清空终端屏幕。"""
    if sys.platform == "win32":
        os.system("cls")
    else:
        os.system("clear")


async def _cmd_threads() -> None:
    """列出所有历史会话。"""
    from app.memory.checkpointer import list_threads

    try:
        threads = await list_threads()
    except Exception as exc:
        print(f"[加载失败] {exc}")
        return

    if not threads:
        print("无历史会话")
        return

    print("历史会话:")
    for t in threads:
        tid = t.get("thread_id", "?")
        count = t.get("message_count", "?")
        updated = t.get("updated_at", "?")
        print(f"  {tid} | {count} 条消息 | {updated}")


# ============================================================
# One-shot 模式
# ============================================================

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
    settings = get_settings()

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
        success = await _consume_events(event_gen, renderer, thread_id, is_repl=False)
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


# ============================================================
# 主入口
# ============================================================

def main() -> None:
    """CLI 主入口。"""
    parser = _build_parser()
    args = parser.parse_args()

    # 解析 agent_mode
    agent_mode = _resolve_agent_mode(args)

    # 生成或复用 thread_id
    thread_id = args.thread or uuid.uuid4().hex[:12]

    # workspace 路径
    workspace_path = args.workspace
    if workspace_path is None:
        workspace_path = str(Path.cwd())

    # 读取管道输入
    stdin_content = _read_stdin_if_piped()

    # coding_team 模式下增加子任务超时到 600s（CLI 交互场景需要更长）
    if agent_mode == "coding_team":
        os.environ.setdefault("AGENTX_AGENT_TEAM_SUBTASK_TIMEOUT", "600")

    # 判断模式
    if args.message is not None:
        # One-shot: 有位置参数
        message = args.message
        if stdin_content:
            message = f"{message}\n\n---\n{stdin_content}"
        exit_code = asyncio.run(run_one_shot(
            message,
            agent_mode=agent_mode,
            thread_id=thread_id,
            workspace_path=workspace_path,
            verbose=args.verbose,
            json_mode=args.json_mode,
        ))
        sys.exit(exit_code)
    elif stdin_content:
        # One-shot: 无位置参数但有管道输入
        exit_code = asyncio.run(run_one_shot(
            stdin_content,
            agent_mode=agent_mode,
            thread_id=thread_id,
            workspace_path=workspace_path,
            verbose=args.verbose,
            json_mode=args.json_mode,
        ))
        sys.exit(exit_code)
    else:
        # REPL 模式
        asyncio.run(run_repl(
            agent_mode=agent_mode,
            thread_id=thread_id,
            workspace_path=workspace_path,
            verbose=args.verbose,
            json_mode=args.json_mode,
        ))


if __name__ == "__main__":
    main()
