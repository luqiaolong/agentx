"""通用 prompt 工具。

合并多层 system prompt，按优先级排序。
"""

from __future__ import annotations

from app.utils.platform_info import get_os_hint


def resolve_system_prompt(
    default: str,
    scene_prompt: str | None,
    skill_extra: str | None,
) -> str:
    """合并三层 system prompt，优先级：skill_extra > scene_prompt > default。

    - ``scene_prompt`` 非空时覆盖 ``default``（场景切换器注入）。
    - ``skill_extra`` 非空时拼在最前（画像 / @skill content，遵循 spec R9 画像优先约定）。
    - ``scene_prompt`` 为空字符串视为未设置（防御 pydantic 边界）。

    Examples:
        >>> resolve_system_prompt("d", None, None)
        'd'
        >>> resolve_system_prompt("d", "scene", None)
        'scene'
        >>> resolve_system_prompt("d", "scene", "skill")
        'skill\nscene'
    """
    base = scene_prompt if scene_prompt else default
    return f"{skill_extra}\n{base}" if skill_extra else base


def build_workspace_prompt_suffix(workspace_path: str | None) -> str:
    """根据工作区路径生成 system prompt 后缀。

    集中实现，供 Supervisor / DeepAgent / Coding Expert 等场景复用，
    避免各 agent 中重复定义导致文案漂移。

    重要：不向 LLM 泄露 workspace 绝对路径。deepagents FilesystemBackend
    在 ``virtual_mode=True`` 下将 workspace 根目录映射为虚拟路径 ``/``，
    LLM 应使用虚拟路径（如 ``/backend/app/...``）调用 fs 工具（ls/read_file/
    glob/grep），而非 Windows 绝对路径（如 ``D:\\...``），否则会被
    ``validate_path`` 拒绝并触发工具调用死循环。
    """
    parts = [get_os_hint()]
    if workspace_path:
        parts.append(
            "\n\n## 工作区与文件路径\n"
            "当前已授权一个工作区目录。文件操作工具（ls/read_file/glob/grep/write_file/edit_file）"
            "使用**虚拟路径**，以 ``/`` 代表工作区根目录：\n"
            "- 列出根目录：``ls(\"/\")``\n"
            "- 读取文件：``read_file(\"/backend/app/main.py\")``\n"
            "- 搜索文件：``glob(\"**/*.py\", path=\"/backend\")``\n"
            "- 搜索内容：``grep(\"pattern\", path=\"/backend/app\")``\n"
            "\n"
            "**禁止**在文件操作工具中使用 Windows 绝对路径（如 ``D:\\\\...``）或带盘符的路径，"
            "这些路径会被拒绝。请始终使用以 ``/`` 开头的虚拟路径。\n"
            "\n"
            "执行 shell 命令（execute 工具）时也不要在命令中包含工作区绝对路径，"
            "使用相对路径即可（工作目录已设为工作区根目录）。"
        )
    return "".join(parts)


__all__ = ["resolve_system_prompt", "build_workspace_prompt_suffix"]
