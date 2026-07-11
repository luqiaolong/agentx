"""本地 Claude CLI 封装：把复盘/优化任务委托给 ``claude`` 命令行工具。

核心能力：
- ``claude -p --output-format stream-json`` 调用 Claude CLI，stdin 传 prompt
- 解析 stream-json 事件流，转换为 agentx SSE 事件（reasoning / token / done / error）
- 支持 ``plan``（只读分析）和 ``acceptEdits``（可修改代码）两种权限模式
- CLI 不可用时直接抛 ``ClaudeCliError``，不降级

设计约束（AGENTS.md §1.1）：
- 不自研 agent 循环 / think 解析 — Claude CLI 内置完整 agent 能力
- 不自研 read_file 工具 — Claude CLI 内置 Read/Grep/Glob/Edit/Bash
- 事件契约与 ``deepagent/streaming.py`` + ``useChatStream`` 对齐
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal

from app.observability.logger import logger
from app.sse.events import make_error_event

__all__ = ["ClaudeCliRunner", "ClaudeCliError", "claude_cli_available"]


class ClaudeCliError(RuntimeError):
    """Claude CLI 调用异常（未安装 / 认证失败 / 超时 / 返回错误）。"""


def claude_cli_available() -> bool:
    """检测本地是否安装 ``claude`` CLI（PATH 中可找到）。"""
    return shutil.which("claude") is not None


@dataclass
class ClaudeCliResult:
    """Claude CLI 调用的最终汇总结果（来自 stream-json 的 result 事件）。"""

    result_text: str = ""
    is_error: bool = False
    session_id: str | None = None
    total_cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    usage: dict[str, Any] | None = None


class ClaudeCliRunner:
    """封装本地 ``claude`` CLI，流式输出 agentx SSE 事件。

    Args:
        model: 模型名（如 ``sonnet`` / ``opus``），传给 ``--model``。
        max_turns: agent 循环最大轮数，传给 ``--max-turns``。
        max_budget_usd: 单次调用成本上限，传给 ``--max-budget-usd``。
        timeout: 超时秒数（Claude CLI 无内置超时，靠外部 asyncio 控制）。
        working_dir: Claude CLI 的工作目录（默认项目根）。
        permission_mode: 权限模式 — ``plan``（只读）/ ``acceptEdits``（可改代码）。
    """

    def __init__(
        self,
        *,
        model: str = "sonnet",
        max_turns: int = 20,
        max_budget_usd: float = 2.0,
        timeout: float = 600.0,
        working_dir: str | None = None,
        permission_mode: Literal["plan", "acceptEdits"] = "plan",
    ) -> None:
        if not claude_cli_available():
            raise ClaudeCliError(
                "claude CLI 未安装或不在 PATH 中。"
                "请安装 Claude Code CLI：npm install -g @anthropic-ai/claude-code"
            )
        self.model = model
        self.max_turns = max_turns
        self.max_budget_usd = max_budget_usd
        self.timeout = timeout
        self.working_dir = working_dir
        self.permission_mode = permission_mode

    def _build_command(self) -> list[str]:
        """构造 ``claude -p`` 命令行参数。

        Windows 上 npm 全局安装的 ``claude`` 可能是 ``claude.cmd`` 批处理文件或
        ``claude.ps1`` PowerShell 脚本。``asyncio.create_subprocess_exec`` 底层调
        ``CreateProcess`` 不会搜索 PATHEXT，因此需要：
        - ``.cmd/.bat``：用 ``cmd /c`` 前缀让 cmd.exe 解析
        - ``.ps1``：用 ``powershell/pwsh -File`` 执行
        - 其他/无扩展名：直接作为可执行文件路径传递
        """
        args = [
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--model", self.model,
            "--max-turns", str(self.max_turns),
            "--max-budget-usd", str(self.max_budget_usd),
            "--bare",  # 跳过 hooks/LSP/CLAUDE.md 自动发现，启动更快
            "--no-session-persistence",  # 一次性调用，不写盘
            "--permission-mode", self.permission_mode,
        ]
        exe = shutil.which("claude")
        if not exe:
            raise ClaudeCliError(
                "claude CLI 未安装或不在 PATH 中。"
                "请安装 Claude Code CLI：npm install -g @anthropic-ai/claude-code"
            )

        if sys.platform == "win32":
            ext = os.path.splitext(exe)[1].lower()
            if ext == ".ps1":
                ps = shutil.which("pwsh") or shutil.which("powershell")
                if not ps:
                    raise ClaudeCliError(
                        "claude CLI 是 PowerShell 脚本，但系统未找到 powershell/pwsh。"
                    )
                return [ps, "-ExecutionPolicy", "Bypass", "-File", exe, *args]
            if ext in (".cmd", ".bat"):
                return ["cmd", "/c", exe, *args]
            return [exe, *args]

        return [exe, *args]

    async def run_stream(
        self,
        prompt: str,
        trace_id: str,
        source: str = "review",
    ) -> AsyncIterator[dict[str, str]]:
        """流式执行，yield agentx SSE 事件。

        把 ``claude --output-format stream-json`` 的事件转换为：
        - ``assistant.message.content[].type == "thinking"`` → ``reasoning_delta``
        - ``assistant.message.content[].type == "text"`` → ``token``
        - ``result`` → ``done``（携带 cost / duration 元数据）
        - 异常 → ``error``

        Args:
            prompt: 完整 prompt 文本（通过 stdin 传入，规避命令行长度限制）。
            trace_id: 关联的 trace_id（写入 SSE 事件 metadata）。
            source: 事件来源标识（``review`` / ``apply-optimization``）。
        """
        cmd = self._build_command()
        logger.info(
            "claude cli stream started",
            trace_id=trace_id,
            source=source,
            model=self.model,
            permission_mode=self.permission_mode,
            max_turns=self.max_turns,
        )

        # 先发一个 reasoning 事件让前端建 ReasoningBlock
        yield {
            "event": "reasoning",
            "data": json.dumps(
                {
                    "content": "Claude CLI 正在分析…",
                    "source": source,
                    "trace_id": trace_id,
                },
                ensure_ascii=False,
            ),
        }

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir,
            )
        except OSError as exc:
            err_msg = f"启动 claude CLI 失败：{exc}（请确认已安装 claude CLI 并完成认证）"
            logger.error("claude cli spawn failed", error=str(exc), trace_id=trace_id)
            yield make_error_event(err_msg, trace_id=trace_id)
            yield {"event": "done", "data": "{}"}
            return

        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None

        # 通过 stdin 传 prompt（规避命令行长度限制）
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        final_result: ClaudeCliResult | None = None

        async def _read_stream(
            stream: asyncio.StreamReader,
        ) -> AsyncIterator[dict[str, str]]:
            """逐行读取 stream-json，转换为 SSE 事件。"""
            nonlocal final_result
            while True:
                line = await stream.readline()
                if not line:
                    return
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue
                try:
                    event = json.loads(line_str)
                except json.JSONDecodeError:
                    logger.warning(
                        "claude cli: non-json line",
                        line=line_str[:200],
                        trace_id=trace_id,
                    )
                    continue
                async for sse in self._convert_event(event, trace_id, source):
                    yield sse

        try:
            async with asyncio.timeout(self.timeout):
                async for sse in _read_stream(proc.stdout):
                    if sse.get("event") == "done":
                        final_result = _extract_result_from_done(sse)
                    yield sse
                    if sse.get("event") == "done":
                        return
        except TimeoutError:
            proc.kill()
            await proc.wait()
            err_msg = f"Claude CLI 超时（{self.timeout}s）"
            logger.error("claude cli timeout", trace_id=trace_id, timeout=self.timeout)
            yield make_error_event(err_msg, trace_id=trace_id)
            yield {"event": "done", "data": "{}"}
            return

        # 读 stderr（若进程异常退出）
        stderr_data = await proc.stderr.read()
        await proc.wait()
        if proc.returncode != 0 and final_result is None:
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
            err_msg = f"Claude CLI 退出码 {proc.returncode}"
            if stderr_text:
                err_msg += f"：{stderr_text[:500]}"
            logger.error(
                "claude cli failed",
                returncode=proc.returncode,
                stderr=stderr_text[:500],
                trace_id=trace_id,
            )
            yield make_error_event(err_msg, trace_id=trace_id)
            yield {"event": "done", "data": "{}"}

    async def _convert_event(
        self,
        event: dict[str, Any],
        trace_id: str,
        source: str,
    ) -> AsyncIterator[dict[str, str]]:
        """把单个 Claude stream-json 事件转为 agentx SSE 事件。"""
        etype = event.get("type", "")

        if etype == "assistant":
            message = event.get("message", {})
            content_blocks = message.get("content", [])
            for block in content_blocks:
                block_type = block.get("type", "")
                if block_type == "thinking":
                    thinking_text = block.get("thinking", "")
                    if thinking_text:
                        yield {
                            "event": "reasoning_delta",
                            "data": json.dumps(
                                {
                                    "delta": thinking_text,
                                    "source": source,
                                    "trace_id": trace_id,
                                },
                                ensure_ascii=False,
                            ),
                        }
                elif block_type == "text":
                    text = block.get("text", "")
                    if text:
                        yield {"event": "token", "data": text}

        elif etype == "result":
            is_error = event.get("is_error", False)
            result_text = event.get("result", "")
            if is_error:
                yield make_error_event(
                    result_text or "Claude CLI 返回错误",
                    trace_id=trace_id,
                )
            # done 事件携带元数据（cost / duration / session_id）
            done_meta = {
                "cost_usd": event.get("total_cost_usd", 0),
                "duration_ms": event.get("duration_ms", 0),
                "num_turns": event.get("num_turns", 0),
                "session_id": event.get("session_id"),
                "usage": event.get("usage"),
                "result_text": result_text,
            }
            yield {"event": "done", "data": json.dumps(done_meta, ensure_ascii=False)}


def _extract_result_from_done(done_sse: dict[str, str]) -> ClaudeCliResult | None:
    """从 done SSE 事件中提取 ClaudeCliResult（供持久化用）。"""
    try:
        meta = json.loads(done_sse.get("data", "{}"))
        return ClaudeCliResult(
            result_text=meta.get("result_text", ""),
            is_error=False,
            session_id=meta.get("session_id"),
            total_cost_usd=meta.get("cost_usd", 0),
            duration_ms=meta.get("duration_ms", 0),
            num_turns=meta.get("num_turns", 0),
            usage=meta.get("usage"),
        )
    except (json.JSONDecodeError, TypeError):
        return None
