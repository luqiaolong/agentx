"""幂等生成 ``.agentx/`` 配置目录。

``generate_agentx_dir`` 仅创建缺失文件，已存在的文件跳过（保护用户编辑）。
``mkdir(exist_ok=True)`` + 文件级 ``exists()`` 检查 = 完全幂等，多次调用安全。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.observability.logger import logger
from app.workspace.config.templates import EMPTY_DIRS, TEMPLATES

__all__ = ["GenerationResult", "generate_agentx_dir"]


@dataclass
class GenerationResult:
    """``generate_agentx_dir`` 的返回值。"""

    path: str
    """``.agentx/`` 目录的绝对路径。"""

    created: list[str] = field(default_factory=list)
    """本次新建的文件列表（相对 ``.agentx/`` 的路径）。"""

    skipped: list[str] = field(default_factory=list)
    """已存在被跳过的文件列表（相对 ``.agentx/`` 的路径）。"""


def generate_agentx_dir(workspace_path: Path) -> GenerationResult:
    """在 ``workspace_path`` 下幂等生成 ``.agentx/`` 配置目录。

    Args:
        workspace_path: 工作区根目录的绝对路径。

    Returns:
        ``GenerationResult``，含 ``path`` / ``created`` / ``skipped`` 字段。

    Raises:
        FileNotFoundError: ``workspace_path`` 不存在。
        NotADirectoryError: ``workspace_path`` 不是目录。
    """
    if not workspace_path.exists():
        raise FileNotFoundError(f"workspace 不存在: {workspace_path}")
    if not workspace_path.is_dir():
        raise NotADirectoryError(f"workspace 不是目录: {workspace_path}")

    agentx_dir = workspace_path / ".agentx"
    agentx_dir.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    skipped: list[str] = []

    for rel_path, content in TEMPLATES.items():
        file_path = agentx_dir / rel_path
        # 确保父目录存在
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if file_path.exists():
            skipped.append(rel_path)
        else:
            file_path.write_text(content, encoding="utf-8")
            created.append(rel_path)

    # 创建空目录（rules / skills / memory）
    for dir_name in EMPTY_DIRS:
        dir_path = agentx_dir / dir_name
        dir_path.mkdir(parents=True, exist_ok=True)
        if dir_path.exists():
            created.append(f"{dir_name}/")
        else:
            skipped.append(f"{dir_name}/")

    logger.info(
        "workspace.config.generated",
        workspace=str(workspace_path),
        created_count=len(created),
        skipped_count=len(skipped),
    )

    return GenerationResult(
        path=str(agentx_dir),
        created=created,
        skipped=skipped,
    )
