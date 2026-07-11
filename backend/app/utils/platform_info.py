"""操作系统平台检测工具。

提供跨平台操作系统识别能力，供 system prompt 构建、命令适配、日志标注等场景使用。
"""

from __future__ import annotations

import platform
import sys

__all__ = ["detect_os", "get_os_hint"]


def detect_os() -> tuple[str, str]:
    """检测当前操作系统，返回 (os_name, shell_family)。

    Returns:
        - os_name: 人类可读的操作系统名称（如 ``'Windows 11'``、``'macOS'``、``'Ubuntu'``）
        - shell_family: 命令家族标识（``'windows'``、``'posix'``、``'unknown'``）

    Examples:
        >>> detect_os()  # on Windows
        ('Windows 11', 'windows')
        >>> detect_os()  # on macOS
        ('macOS', 'posix')
        >>> detect_os()  # on Linux
        ('Ubuntu 22.04', 'posix')
    """
    system = platform.system()
    release = platform.release()

    if sys.platform == "win32" or system == "Windows":
        return f"Windows {release}", "windows"
    if sys.platform == "darwin" or system == "Darwin":
        return "macOS", "posix"
    if sys.platform.startswith("linux") or system == "Linux":
        # 尝试获取 Linux 发行版名称
        try:
            import distro  # type: ignore[import-untyped]

            linux_name = distro.name(pretty=True)
        except Exception:
            linux_name = "Linux"
        return linux_name, "posix"

    return f"{system} {release}", "unknown"


def get_os_hint() -> str:
    """构建操作系统环境提示，告知 LLM 当前运行平台及对应命令规范。

    Returns:
        可直接拼入 system prompt 的 Markdown 字符串。

    Examples:
        >>> hint = get_os_hint()
        >>> "运行环境" in hint
        True
    """
    os_name, shell_family = detect_os()

    if shell_family == "windows":
        return (
            f"\n\n## 运行环境\n"
            f"当前操作系统：{os_name}。"
            "执行 CLI 命令时请使用 Windows 命令（PowerShell / CMD 语法），"
            "且避免使用管道符 |（沙箱禁止 shell 元字符）：\n"
            "- 查看总内存：powershell -Command (Get-CimInstance Win32_OperatingSystem).TotalVisibleMemorySize\n"
            "- 查看可用内存：powershell -Command (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory\n"
            "- 查看磁盘：powershell -Command (Get-CimInstance Win32_LogicalDisk).Size 与 .FreeSpace\n"
            "- 查看进程：tasklist、powershell -Command Get-Process\n"
            "- 文件操作：Get-ChildItem、Select-Object、Get-Content 等\n"
            "- 避免使用 Linux/macOS 专属命令（ls、cat、grep、ps、top、free、df 等）"
        )
    if shell_family == "posix":
        return (
            f"\n\n## 运行环境\n"
            f"当前操作系统：{os_name}（Unix）。"
            "执行 CLI 命令时请使用标准 Unix/POSIX 命令：\n"
            "- 查看进程：ps、top、htop\n"
            "- 查看内存：free、vm_stat（macOS）\n"
            "- 查看磁盘：df、du\n"
            "- 文件操作：ls、cat、grep、find、sed、awk 等\n"
            "- 避免使用 Windows 专属命令（tasklist、wmic、Get-Process 等）"
        )

    # 未知平台，给出通用提示
    return (
        f"\n\n## 运行环境\n"
        f"当前操作系统：{os_name}。"
        "执行 CLI 命令时请使用该系统原生命令。"
    )
