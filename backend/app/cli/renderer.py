"""终端事件渲染器。

将 SSE 事件流渲染为终端彩色输出。
"""

from __future__ import annotations

import json
import sys
from typing import Any

try:
    from colorama import Fore, Style, init as _colorama_init
    _colorama_init()
    _HAS_COLORAMA = True
except ImportError:
    _HAS_COLORAMA = False

    class _Dummy:
        def __getattr__(self, _name: str) -> str:
            return ""

    Fore = _Dummy()
    Style = _Dummy()

__all__ = ["EventRenderer"]


class EventRenderer:
    """SSE 事件终端渲染器。

    Args:
        verbose: 显示 reasoning 和完整 tool_result。
        json_mode: JSON 结构化输出（收集所有事件，最后一次性输出）。
    """

    def __init__(self, *, verbose: bool = False, json_mode: bool = False) -> None:
        self.verbose = verbose
        self.json_mode = json_mode
        self._json_events: list[dict[str, str]] = []
        self._first_token = True

    def render(self, event: dict[str, str]) -> None:
        """渲染单个 SSE 事件。

        Args:
            event: ``{"event": "token", "data": "..."}``
        """
        if self.json_mode:
            self._json_events.append(event)
            return

        event_type = event.get("event", "")
        data = event.get("data", "")

        handler = getattr(self, f"_render_{event_type}", None)
        if handler:
            handler(data)
        else:
            # 未知事件类型：verbose 时打印
            if self.verbose:
                print(f"{Fore.CYAN}[{event_type}]{Style.RESET_ALL} {data}")

    def flush_json(self) -> None:
        """JSON 模式下输出收集的事件列表。"""
        if self.json_mode:
            print(json.dumps(self._json_events, ensure_ascii=False))
            self._json_events.clear()

    # ============================================================
    # 各事件类型渲染
    # ============================================================

    def _render_token(self, data: str) -> None:
        """token 事件：增量输出。"""
        if self._first_token:
            self._first_token = False
        print(data, end="", flush=True)

    def _render_reasoning(self, data: str) -> None:
        """reasoning 事件：默认隐藏，verbose 时灰色显示。"""
        if not self.verbose:
            return
        try:
            parsed = json.loads(data)
            text = parsed.get("content", parsed.get("text", data)) if isinstance(parsed, dict) else str(parsed)
        except (json.JSONDecodeError, TypeError):
            text = data
        print(f"{Fore.LIGHTBLACK_EX}{text}{Style.RESET_ALL}", end="", flush=True)

    def _render_tool_call(self, data: str) -> None:
        """tool_call 事件：显示工具名。"""
        try:
            parsed = json.loads(data)
            name = parsed.get("name", "unknown")
            args = parsed.get("args", {})
            source = parsed.get("source", "")
        except (json.JSONDecodeError, TypeError):
            name = "unknown"
            args = {}
            source = ""

        source_tag = f" ({source})" if source else ""
        print(f"\n{Fore.CYAN}[调用: {name}{source_tag}]{Style.RESET_ALL}", flush=True)
        if self.verbose and args:
            args_str = json.dumps(args, ensure_ascii=False, indent=2)
            print(f"{Fore.LIGHTBLACK_EX}{args_str}{Style.RESET_ALL}", flush=True)

    def _render_tool_result(self, data: str) -> None:
        """tool_result 事件：截断显示。"""
        try:
            parsed = json.loads(data)
            name = parsed.get("name", "unknown")
            result = parsed.get("result", "")
            source = parsed.get("source", "")
        except (json.JSONDecodeError, TypeError):
            name = "unknown"
            result = data
            source = ""

        max_chars = 10000 if self.verbose else 2000
        result_str = str(result)
        if len(result_str) > max_chars:
            result_str = result_str[:max_chars] + f"\n... (截断，共 {len(result_str)} 字符)"

        source_tag = f" ({source})" if source else ""
        print(f"{Fore.GREEN}[结果: {name}{source_tag}]{Style.RESET_ALL} {result_str}", flush=True)

    def _render_approval_request(self, data: str) -> None:
        """approval_request 事件：高亮显示，等待用户输入。"""
        try:
            parsed = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            parsed = {"raw": data}

        tool_name = parsed.get("tool_name", parsed.get("name", "unknown"))
        args = parsed.get("args", parsed.get("args_preview", {}))
        path = parsed.get("requestedPath", parsed.get("path", ""))
        preview = parsed.get("preview", "")

        print(f"\n{Fore.YELLOW}{'='*50}{Style.RESET_ALL}", flush=True)
        print(f"{Fore.YELLOW}⚠️  审批请求{Style.RESET_ALL}", flush=True)
        print(f"{Fore.YELLOW}工具: {tool_name}{Style.RESET_ALL}", flush=True)
        if path:
            print(f"{Fore.YELLOW}路径: {path}{Style.RESET_ALL}", flush=True)
        if args:
            args_str = json.dumps(args, ensure_ascii=False, indent=2)
            # 截断参数预览
            if len(args_str) > 500:
                args_str = args_str[:500] + "..."
            print(f"{Fore.YELLOW}参数: {args_str}{Style.RESET_ALL}", flush=True)
        if preview:
            preview_str = preview[:300] + "..." if len(preview) > 300 else preview
            print(f"{Fore.LIGHTYELLOW_EX}预览:\n{preview_str}{Style.RESET_ALL}", flush=True)
        print(f"{Fore.YELLOW}{'='*50}{Style.RESET_ALL}", flush=True)

    def _render_todo_update(self, data: str) -> None:
        """todo_update 事件：显示待办列表。"""
        try:
            parsed = json.loads(data)
            todos = parsed.get("todos", [])
        except (json.JSONDecodeError, TypeError):
            todos = []

        for todo in todos:
            text = todo.get("text", "")
            done = todo.get("done", False)
            marker = f"{Fore.GREEN}✓{Style.RESET_ALL}" if done else f"{Fore.YELLOW}○{Style.RESET_ALL}"
            print(f"  {marker} {text}", flush=True)

    def _render_delegation(self, data: str) -> None:
        """delegation 事件：显示委派信息。"""
        try:
            parsed = json.loads(data)
            target = parsed.get("target", parsed.get("agent", "unknown"))
            task = parsed.get("message", parsed.get("task", ""))
        except (json.JSONDecodeError, TypeError):
            target = "unknown"
            task = data

        print(f"\n{Fore.MAGENTA}[委派: {target}]{Style.RESET_ALL} {task}", flush=True)

    def _render_team_plan(self, data: str) -> None:
        """team_plan 事件：显示团队计划。"""
        try:
            parsed = json.loads(data)
            tasks = parsed.get("plan", parsed.get("tasks", parsed.get("subtasks", [])))
            count = len(tasks) if isinstance(tasks, list) else "?"
        except (json.JSONDecodeError, TypeError):
            tasks = []
            count = "?"

        print(f"\n{Fore.MAGENTA}[团队计划: {count} 个子任务]{Style.RESET_ALL}", flush=True)
        if isinstance(tasks, list):
            for i, task in enumerate(tasks, 1):
                if isinstance(task, dict):
                    agent = task.get("agent", "?")
                    purpose = task.get("purpose", task.get("input", ""))
                    if len(purpose) > 80:
                        purpose = purpose[:80] + "..."
                    print(f"  {i}. [{agent}] {purpose}", flush=True)
        if self.verbose:
            print(f"{Fore.LIGHTBLACK_EX}{data}{Style.RESET_ALL}", flush=True)

    def _render_team_progress(self, data: str) -> None:
        """team_progress 事件：显示团队进度。"""
        try:
            parsed = json.loads(data)
            agent = parsed.get("agent", parsed.get("name", ""))
            status = parsed.get("status", "")
            message = parsed.get("message", "")
        except (json.JSONDecodeError, TypeError):
            agent = ""
            status = data
            message = ""

        msg_part = f" — {message}" if message else ""
        print(f"{Fore.MAGENTA}[{agent}] {status}{msg_part}{Style.RESET_ALL}", flush=True)

    def _render_team_result(self, data: str) -> None:
        """team_result 事件：显示团队结果。"""
        try:
            parsed = json.loads(data)
            agent = parsed.get("agent", parsed.get("name", ""))
            result = parsed.get("summary", parsed.get("result", ""))
        except (json.JSONDecodeError, TypeError):
            agent = ""
            result = data

        result_str = str(result)
        if len(result_str) > 2000:
            result_str = result_str[:2000] + "..."
        print(f"{Fore.MAGENTA}[结果: {agent}] {result_str}{Style.RESET_ALL}", flush=True)

    def _render_team_done(self, data: str) -> None:
        """team_done 事件：显示团队完成。"""
        print(f"\n{Fore.MAGENTA}[团队任务完成]{Style.RESET_ALL}", flush=True)

    def _render_classification(self, data: str) -> None:
        """classification 事件：verbose 时显示。"""
        if self.verbose:
            print(f"{Fore.CYAN}[分类: {data}]{Style.RESET_ALL}", flush=True)

    def _render_plan(self, data: str) -> None:
        """plan 事件：verbose 时显示。"""
        if self.verbose:
            print(f"{Fore.CYAN}[计划: {data}]{Style.RESET_ALL}", flush=True)

    def _render_plan_update(self, data: str) -> None:
        """plan_update 事件：verbose 时显示。"""
        if self.verbose:
            print(f"{Fore.CYAN}[计划更新: {data}]{Style.RESET_ALL}", flush=True)

    def _render_error(self, data: str) -> None:
        """error 事件：红色输出。"""
        print(f"\n{Fore.RED}[错误] {data}{Style.RESET_ALL}", flush=True)

    def _render_done(self, data: str) -> None:
        """done 事件：换行。"""
        print(flush=True)
        self._first_token = True

    # ============================================================
    # 辅助
    # ============================================================

    def add_error(self, message: str) -> None:
        """添加 error 事件到 JSON 输出缓冲（供外部异常处理使用）。"""
        self._json_events.append({"event": "error", "data": message})

    def reset(self) -> None:
        """重置渲染器状态（新的一轮对话）。"""
        self._first_token = True
        if self.json_mode:
            self._json_events.clear()
