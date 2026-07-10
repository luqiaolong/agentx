"""沙箱执行失败分析与权限升级请求。

当 execute 工具因沙箱限制失败时，分析失败原因并生成建议动作，
供调用方决定是否触发权限升级流程（kind="sandbox_escalation"）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "SandboxFailureAnalysis",
    "analyze_sandbox_failure",
    "extract_path_from_command",
]


@dataclass
class SandboxFailureAnalysis:
    """沙箱失败分析结果。

    Attributes:
        is_sandbox_limit: 是否确认为沙箱限制导致的失败。
        reason: 人类可读的原因描述。
        suggested_action: 建议的升级动作。
            - "retry_with_auth": 临时授权路径后，在沙箱内重试。
            - "execute_unsandboxed": 绕过沙箱直接执行。
        suggested_path: 建议授权的路径（若适用）。
    """

    is_sandbox_limit: bool
    reason: str
    suggested_action: str
    suggested_path: str | None


# 已知沙箱限制错误模式（跨平台：Windows / macOS / Linux）。
# 每个条目为 (正则模式, 简短原因描述)。
_SANDBOX_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"PathNotAuthorized", re.IGNORECASE), "路径未授权"),
    (re.compile(r"access is denied", re.IGNORECASE), "访问被拒绝"),
    (re.compile(r"Permission denied", re.IGNORECASE), "权限不足"),
    (re.compile(r"0xFFFD0000", re.IGNORECASE), "沙箱拦截进程创建"),
    (re.compile(r"CreateProcess", re.IGNORECASE), "进程创建被沙箱阻止"),
    (re.compile(r"EPERM", re.IGNORECASE), "操作不允许（沙箱限制）"),
    (re.compile(r"EACCES", re.IGNORECASE), "权限不足（沙箱限制）"),
    (re.compile(r"sandbox", re.IGNORECASE), "沙箱拦截"),
    (re.compile(r"未授权", re.IGNORECASE), "路径未授权"),
    (re.compile(r"authorization", re.IGNORECASE), "授权失败"),
]

# 启发式：这些退出码通常与权限/沙箱相关。
# 包含 Windows 沙箱错误码 0xFFFF0000 (4294901760) 和 0xFFFD0000 (4294770688)
_SANDBOX_EXIT_CODES: frozenset[int] = frozenset({126, 127, 0xFFFF0000, 0xFFFD0000})

# 路径提取正则：匹配 Windows 绝对路径（C:\...）和 Unix 绝对路径（/...）。
_PATH_RE = re.compile(r'[A-Za-z]:\\[^\s"]+|/[^\s"]+')


def extract_path_from_command(command: str) -> str | None:
    """从命令字符串中提取可能的文件/目录路径。

    优先返回最长的匹配项（通常是最具体的路径）。

    Args:
        command: 完整命令字符串。

    Returns:
        提取到的路径，或 None。
    """
    matches = _PATH_RE.findall(command)
    if not matches:
        return None
    # 优先返回最长路径（通常最具体）
    return max(matches, key=len)


def analyze_sandbox_failure(
    command: str,
    exit_code: int,
    stderr: str,
) -> SandboxFailureAnalysis:
    """分析命令执行失败是否为沙箱限制导致。

    匹配已知错误模式 + 启发式判断（特殊 exit_code）。

    Args:
        command: 执行的完整命令字符串。
        exit_code: 进程退出码。
        stderr: 标准错误输出。

    Returns:
        SandboxFailureAnalysis：分析结果及建议动作。
    """
    combined = f"{stderr} {command}"

    for pattern, reason in _SANDBOX_PATTERNS:
        if pattern.search(combined):
            suggested_path = extract_path_from_command(command)
            if suggested_path:
                return SandboxFailureAnalysis(
                    is_sandbox_limit=True,
                    reason=f"沙箱限制：{reason}。命令尝试访问未授权路径。",
                    suggested_action="retry_with_auth",
                    suggested_path=suggested_path,
                )
            return SandboxFailureAnalysis(
                is_sandbox_limit=True,
                reason=f"沙箱限制：{reason}。命令需要系统级权限。",
                suggested_action="execute_unsandboxed",
                suggested_path=None,
            )

    # 启发式：exit_code 在特殊集合中，视为沙箱拦截
    if exit_code in _SANDBOX_EXIT_CODES:
        suggested_path = extract_path_from_command(command)
        if suggested_path:
            return SandboxFailureAnalysis(
                is_sandbox_limit=True,
                reason="命令以特殊退出码终止，疑似沙箱拦截。",
                suggested_action="retry_with_auth",
                suggested_path=suggested_path,
            )
        return SandboxFailureAnalysis(
            is_sandbox_limit=True,
            reason="命令以特殊退出码终止，疑似沙箱拦截。",
            suggested_action="execute_unsandboxed",
            suggested_path=None,
        )

    return SandboxFailureAnalysis(
        is_sandbox_limit=False,
        reason="命令执行失败，非沙箱限制。",
        suggested_action="",
        suggested_path=None,
    )
