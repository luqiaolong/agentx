"""AgentX CLI 主入口。

支持两种使用模式：
- REPL 交互模式：``agentx`` 进入持续对话
- One-shot 单次任务：``agentx "提问内容"`` 直接输出后退出

默认 agent_mode 为 ``coding``。通过 ``--work`` / ``--coding`` / ``--coding-team`` 切换。

子命令：
- ``agentx config show`` — 显示当前配置
- ``agentx config get <key>`` — 获取单个配置项
- ``agentx config set <key> <value>`` — 设置配置项
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

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
  agentx eval run --suite=smoke   # 运行评测套件
  agentx eval list                # 列出所有评测套件
  agentx eval show smoke          # 显示套件详情
  agentx config show               # 显示当前配置
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
# config 子命令
# ============================================================

def _handle_config_subcommand(argv: list[str]) -> int:
    """处理 ``agentx config ...`` 子命令。

    Returns:
        退出码。
    """
    if len(argv) < 2:
        print("用法: agentx config <show|get|set> [args]")
        return 1

    sub = argv[1]

    if sub == "show":
        return _config_show()
    if sub == "get":
        if len(argv) < 3:
            print("用法: agentx config get <key>")
            return 1
        return _config_get(argv[2])
    if sub == "set":
        if len(argv) < 4:
            print("用法: agentx config set <key> <value>")
            return 1
        return _config_set(argv[2], argv[3])

    print(f"未知子命令: {sub}（可选: show / get / set）")
    return 1


def _config_show() -> int:
    """显示当前配置（从 Tauri store 读取）。"""
    from app.cli.store import read_config_json

    config = read_config_json()
    if not config:
        print("[无法读取配置（Tauri store 不存在）]")
        return 1

    import json
    print(json.dumps(config, ensure_ascii=False, indent=2))
    return 0


def _config_get(key: str) -> int:
    """获取单个配置项。"""
    from app.cli.store import read_config_json

    config = read_config_json()
    if not config:
        print(f"[无法读取配置]")
        return 1

    # 支持点号分隔的嵌套 key，如 "models.activeId"
    parts = key.split(".")
    value = config
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            print(f"[未找到配置项: {key}]")
            return 1

    if isinstance(value, (dict, list)):
        import json
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(value)
    return 0


def _config_set(key: str, value: str) -> int:
    """设置配置项（写入 Tauri store config.json）。"""
    from app.cli.store import _candidate_config_paths

    paths = _candidate_config_paths()
    config_path = None
    for p in paths:
        if p.exists():
            config_path = p
            break

    if config_path is None:
        # 使用第一个候选路径
        config_path = paths[0] if paths else None
        if config_path is None:
            print("[无法确定配置文件路径]")
            return 1
        config_path.parent.mkdir(parents=True, exist_ok=True)

    import json

    # 读取现有配置
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    else:
        config = {}

    # 支持点号分隔的嵌套 key
    parts = key.split(".")
    target = config
    for part in parts[:-1]:
        if part not in target or not isinstance(target[part], dict):
            target[part] = {}
        target = target[part]

    # 尝试解析 value 为 bool/int/float，否则保持字符串
    parsed_value: object = value
    if value.lower() in ("true", "false"):
        parsed_value = value.lower() == "true"
    else:
        try:
            parsed_value = int(value)
        except ValueError:
            try:
                parsed_value = float(value)
            except ValueError:
                pass

    # 凭证字段自动加 plain: 前缀（与 Tauri store 凭证格式对齐）
    _CREDENTIAL_PREFIXES = ("apikey.", "milvus.user", "milvus.password")
    if any(key.startswith(p) for p in _CREDENTIAL_PREFIXES) and isinstance(parsed_value, str):
        if not parsed_value.startswith(("plain:", "enc:")):
            parsed_value = f"plain:{parsed_value}"

    target[parts[-1]] = parsed_value

    try:
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        # 凭证字段隐藏显示
        display_value = "***" if any(key.startswith(p) for p in _CREDENTIAL_PREFIXES) else parsed_value
        print(f"[已设置] {key} = {display_value}")
        print(f"[写入] {config_path}")
        return 0
    except Exception as exc:
        print(f"[写入失败] {exc}")
        return 1


# ============================================================
# 主入口
# ============================================================

def _build_eval_parser() -> argparse.ArgumentParser:
    """构建 ``agentx eval`` 子命令 parser。

    注意：不与 ``_build_parser`` 合并以保持向后兼容。argparse 的 subparsers 会
    把第一个位置参数当作子命令名验证 choices，导致 ``agentx "你好"`` 报
    ``invalid choice`` 错误。因此 eval 子命令用独立 parser，在 ``main`` 中
    通过早期检测 ``sys.argv[1] == "eval"`` 分发。
    """
    parser = argparse.ArgumentParser(
        prog="agentx eval",
        description="评测框架：运行/列出/查看评测套件",
    )
    sub = parser.add_subparsers(dest="eval_command")
    # eval run
    run_parser = sub.add_parser("run", help="运行评测")
    run_parser.add_argument(
        "--suite", default="smoke", help="suite 名称（默认 smoke，all 跑所有）"
    )
    run_parser.add_argument(
        "--live", action="store_true", help="使用真实 LLM（默认用 Mock）"
    )
    run_parser.add_argument(
        "--format",
        default="console",
        help="输出格式：console|md|json，逗号分隔多格式",
    )
    run_parser.add_argument(
        "--no-rubric",
        dest="no_rubric",
        action="store_true",
        help="跳过 L2 RubricJudge 和 L3 自纠分支",
    )
    # eval list
    sub.add_parser("list", help="列出所有 suite")
    # eval show
    show_parser = sub.add_parser("show", help="显示 suite 详情")
    show_parser.add_argument("suite_name", help="suite 名称")
    return parser


def main() -> None:
    """CLI 主入口。"""
    # eval 子命令早期分发：避免 argparse subparsers 与 positional message 冲突
    if len(sys.argv) > 1 and sys.argv[1] == "eval":
        eval_parser = _build_eval_parser()
        eval_args = eval_parser.parse_args(sys.argv[2:])  # 跳过 "eval"
        from app.eval.cli import run_eval_command

        sys.exit(run_eval_command(eval_args))

    # config 子命令：在 argparse 之前拦截
    if len(sys.argv) > 1 and sys.argv[1] == "config":
        exit_code = _handle_config_subcommand(sys.argv[1:])
        sys.exit(exit_code)

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
        exit_code = asyncio.run(_run_one_shot_wrapper(
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
        exit_code = asyncio.run(_run_one_shot_wrapper(
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
        asyncio.run(_run_repl_wrapper(
            agent_mode=agent_mode,
            thread_id=thread_id,
            workspace_path=workspace_path,
            verbose=args.verbose,
            json_mode=args.json_mode,
        ))


async def _run_one_shot_wrapper(message: str, **kwargs) -> int:
    """One-shot 包装器，延迟 import 避免循环依赖。"""
    from app.cli.one_shot import run_one_shot
    return await run_one_shot(message, **kwargs)


async def _run_repl_wrapper(**kwargs) -> None:
    """REPL 包装器，延迟 import 避免循环依赖。"""
    from app.cli.repl import run_repl
    await run_repl(**kwargs)


if __name__ == "__main__":
    main()
