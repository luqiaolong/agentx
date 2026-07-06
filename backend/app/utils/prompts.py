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


__all__ = ["resolve_system_prompt"]
