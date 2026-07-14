"""REPL 命令分发与实现。

所有 async 命令正确 ``await``，禁止 fire-and-forget。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import Enum

__all__ = ["CommandAction", "CommandResult", "CommandContext", "handle_command", "print_help"]


class CommandAction(Enum):
    """命令执行后的动作。"""

    CONTINUE = "continue"
    SWITCH_MODE = "switch_mode"
    QUIT = "quit"


@dataclass
class CommandResult:
    """命令执行结果。"""

    action: CommandAction
    new_mode: str | None = None

    @classmethod
    def continue_(cls) -> "CommandResult":
        return cls(action=CommandAction.CONTINUE)

    @classmethod
    def quit(cls) -> "CommandResult":
        return cls(action=CommandAction.QUIT)

    @classmethod
    def switch_mode(cls, mode: str) -> "CommandResult":
        return cls(action=CommandAction.SWITCH_MODE, new_mode=mode)


@dataclass
class CommandContext:
    """命令执行上下文（传递给各命令实现）。"""

    current_mode: str
    thread_id: str
    checkpointer: object | None
    workspace_path: str | None


# ============================================================
# 命令分发
# ============================================================

async def handle_command(cmd: str, ctx: CommandContext) -> CommandResult:
    """处理 REPL 内建命令。

    Returns:
        CommandResult：CONTINUE / SWITCH_MODE / QUIT。
    """
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()

    if command in ("/quit", "/q"):
        return CommandResult.quit()

    if command == "/help":
        print_help()
        return CommandResult.continue_()

    if command == "/reset":
        await _cmd_reset(ctx.thread_id, ctx.checkpointer)
        return CommandResult.continue_()

    if command == "/mode":
        return _cmd_mode(parts, ctx.current_mode)

    if command == "/init":
        await _cmd_init(ctx.workspace_path, ctx.thread_id)
        return CommandResult.continue_()

    if command == "/model":
        _cmd_model()
        return CommandResult.continue_()

    if command == "/models":
        _cmd_models()
        return CommandResult.continue_()

    if command == "/compact":
        await _cmd_compact(ctx.thread_id, ctx.checkpointer)
        return CommandResult.continue_()

    if command == "/abort":
        await _cmd_abort(ctx.thread_id)
        return CommandResult.continue_()

    if command == "/pause":
        await _cmd_pause(ctx.thread_id)
        return CommandResult.continue_()

    if command == "/resume":
        await _cmd_resume(ctx.thread_id)
        return CommandResult.continue_()

    if command == "/clear":
        _cmd_clear()
        return CommandResult.continue_()

    if command == "/thread":
        print(f"当前 thread_id: {ctx.thread_id}")
        return CommandResult.continue_()

    if command == "/threads":
        await _cmd_threads()
        return CommandResult.continue_()

    if command == "/history":
        limit = 10
        if len(parts) > 1:
            try:
                limit = int(parts[1].strip())
            except ValueError:
                print("[用法] /history [N]  N 为消息条数，默认 10")
                return CommandResult.continue_()
        await _cmd_history(ctx.thread_id, ctx.checkpointer, limit)
        return CommandResult.continue_()

    # 未知命令
    print(f"未知命令: {command}（输入 /help 查看可用命令）")
    return CommandResult.continue_()


# ============================================================
# 帮助
# ============================================================

def print_help() -> None:
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
    print("  /history [N]        显示当前会话最近 N 条消息（默认 10）")
    print("  /quit (或 /q)       退出")
    print("  /help               显示此帮助")


# ============================================================
# 各命令实现
# ============================================================

async def _cmd_reset(thread_id: str, checkpointer: object | None) -> None:
    """清空会话状态。"""
    from app.sandbox import get_sandbox

    if checkpointer and hasattr(checkpointer, "adelete_thread"):
        try:
            await checkpointer.adelete_thread(thread_id)
            await get_sandbox().clear(thread_id)
            print("[会话已重置]")
        except Exception as exc:
            print(f"[重置失败] {exc}")
    else:
        print("[checkpointer 不支持重置]")


def _cmd_mode(parts: list[str], current_mode: str) -> CommandResult:
    """切换模式。"""
    if len(parts) < 2:
        print(f"当前模式: {current_mode}")
        print("用法: /mode <work|coding|coding_team>")
        return CommandResult.continue_()
    new_mode = parts[1].strip().lower()
    if new_mode in ("work", "coding", "coding_team"):
        print(f"模式已切换为 {new_mode}")
        return CommandResult.switch_mode(new_mode)
    else:
        print(f"无效模式: {new_mode}，可选: work / coding / coding_team")
        return CommandResult.continue_()


async def _cmd_init(workspace_path: str | None, thread_id: str) -> None:
    """授权当前目录为 workspace。"""
    from app.sandbox import get_sandbox

    if not workspace_path:
        print("[未指定 workspace 路径]")
        return
    try:
        await get_sandbox().authorize(thread_id, workspace_path)
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
    from app.cli.store import read_config_json

    config = read_config_json()
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


async def _cmd_compact(thread_id: str, checkpointer: object | None) -> None:
    """压缩会话历史。"""
    import uuid

    from langchain_core.messages import SystemMessage
    from app.memory import summarize_messages
    from app.memory.compact_utils import split_messages_for_compact

    if not checkpointer:
        print("[checkpointer 未初始化]")
        return

    # T1.7: fail-fast — 若 checkpointer 不支持写回，立即退出
    if not (hasattr(checkpointer, "aput") or hasattr(checkpointer, "put")):
        print("[checkpointer 不支持写回（缺少 aput/put 方法）]")
        return

    # T1.6: checkpoint_ns 是 LangGraph saver 必需字段（主线程命名空间为空字符串）
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

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

    to_compress, keep_recent = split_messages_for_compact(messages)
    try:
        summary = await summarize_messages(to_compress)
    except Exception as exc:
        print(f"[摘要失败] {exc}")
        return

    new_messages = [SystemMessage(content=summary), *keep_recent]
    new_channel_values = {**channel_values, "messages": new_messages}
    # T1.6: 生成新 checkpoint ID 并设置 parent_checkpoint_id，
    # 避免覆盖旧 checkpoint、破坏历史链
    new_checkpoint_id = str(uuid.uuid4())
    new_checkpoint = {
        **checkpoint,
        "id": new_checkpoint_id,
        "parent_checkpoint_id": checkpoint.get("id"),
        "channel_values": new_channel_values,
    }
    new_config = {
        **config,
        "configurable": {
            **config.get("configurable", {}),
            "checkpoint_id": new_checkpoint_id,
            "checkpoint_ns": "",
        },
    }
    # T1.6: new_versions 必须是 dict（channel -> version），而非 list
    new_versions = {
        **(checkpoint.get("channel_versions") or {}),
        "messages": str(uuid.uuid4()),
    }

    try:
        if hasattr(checkpointer, "aput"):
            await checkpointer.aput(new_config, new_checkpoint, {}, new_versions)
        elif hasattr(checkpointer, "put"):
            checkpointer.put(new_config, new_checkpoint, {}, new_versions)
        else:
            print("[checkpointer 不支持写回]")
            return
    except Exception as exc:
        print(f"[写回失败] {exc}")
        return

    print(f"[已压缩] {len(to_compress)} 条消息 → 摘要 ({len(summary)} 字符)")


async def _cmd_abort(thread_id: str) -> None:
    """中止当前生成。"""
    from app.security.approval import set_abort

    try:
        await set_abort(thread_id)
        print("[已发送中止信号]")
    except Exception as exc:
        print(f"[中止失败] {exc}")


async def _cmd_pause(thread_id: str) -> None:
    """暂停生成。"""
    from app.security.approval import set_pause

    try:
        await set_pause(thread_id)
        print("[已暂停]")
    except Exception as exc:
        print(f"[暂停失败] {exc}")


async def _cmd_resume(thread_id: str) -> None:
    """恢复生成。"""
    from app.security.approval import clear_pause

    try:
        await clear_pause(thread_id)
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
    from app.memory import list_threads

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


async def _cmd_history(thread_id: str, checkpointer: object | None, limit: int = 10) -> None:
    """显示当前 thread 最近 N 条消息摘要。"""
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
        print("[无历史消息]")
        return

    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    messages = list(channel_values.get("messages", []))

    if not messages:
        print("[无历史消息]")
        return

    # 取最近 limit 条
    recent = messages[-limit:]
    print(f"最近 {len(recent)} 条消息:")
    for i, msg in enumerate(recent, 1):
        # 获取角色
        role = getattr(msg, "type", getattr(msg, "role", "unknown"))
        # 获取内容摘要
        content = getattr(msg, "content", str(msg))
        content_str = str(content) if content else ""
        # 截断显示
        if len(content_str) > 100:
            content_str = content_str[:100] + "..."
        print(f"  {i}. [{role}] {content_str}")
