"""通用 prompt 工具。

合并多层 system prompt，按优先级排序。
"""

from __future__ import annotations


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
    """
    if not workspace_path:
        return ""
    return (
        f"\n\n当前 workspace: {workspace_path}\n"
        "对该路径下的文件操作需已被用户授权；"
        "若涉及越界读写，会触发审批请求。"
    )


__all__ = ["resolve_system_prompt", "build_workspace_prompt_suffix"]
