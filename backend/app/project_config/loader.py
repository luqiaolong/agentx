"""从 ``.agentx/`` 目录加载项目级配置。

容错策略：
- ``.agentx/`` 不存在 → 返回 ``exists=False`` 的空 ``ProjectConfig``
- JSON 文件解析失败 → 降级为空配置 + ``logger.warning``
- ``rules/*.md`` 限制最多 10 个文件，每个最大 4KB
- ``AGENTS.md`` / ``system_prompt.md`` 最大 32KB
- 拒绝符号链接（防止 ``rules/`` 下放置指向 ``~/.ssh/id_rsa`` 等敏感文件的 symlink
  泄漏到 LLM 上下文）
- ``rules/README.md`` 不被加载为 rule（它是说明文档而非规则）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.observability.logger import logger

__all__ = ["RuleFile", "ProjectConfig", "load_project_config"]

# rules/ 目录限制
_MAX_RULE_FILES = 10
_MAX_RULE_FILE_SIZE = 4096  # 4KB

# AGENTS.md / system_prompt.md 大小上限（防止 DoS / 上下文膨胀）
_MAX_AGENTS_MD_SIZE = 32 * 1024  # 32KB
_MAX_SYSTEM_PROMPT_SIZE = 32 * 1024  # 32KB

# rules/ 目录下不被加载为 rule 的文件名（说明文档而非规则）
_RULES_SKIP_NAMES: frozenset[str] = frozenset({"README.md"})


@dataclass
class RuleFile:
    """``.agentx/rules/`` 下的单个规则文件。"""

    name: str
    """文件名（不含 ``.md`` 扩展名），用作上下文标题。"""

    content: str
    """文件内容（Markdown）。"""


@dataclass
class ProjectConfig:
    """从 ``.agentx/`` 加载的项目级配置。

    所有字段 nullable / 空集合默认值，缺失文件不报错（降级为全局配置）。
    """

    workspace_path: Path
    agents_md: str | None = None
    """``.agentx/AGENTS.md`` 内容，``None`` = 文件不存在。"""

    rules: list[RuleFile] = field(default_factory=list)
    """``.agentx/rules/*.md`` 列表（按文件名排序，最多 10 个）。"""

    mcp_servers: list[dict] = field(default_factory=list)
    """``.agentx/mcp.json``，空列表 = 无项目级 MCP。"""

    subagents_config: dict = field(default_factory=dict)
    """``.agentx/subagents.json``，空 dict = 无覆盖。"""

    tools_config: dict[str, bool] = field(default_factory=dict)
    """``.agentx/tools.json``，空 dict = 无覆盖。"""

    system_prompt: str | None = None
    """``.agentx/system_prompt.md``，``None`` = 无覆盖。"""

    exists: bool = False
    """``.agentx/`` 目录是否存在。"""

    @property
    def context_prompt(self) -> str:
        """拼接 AGENTS.md + rules 为上下文提示词。

        无 AGENTS.md 且无 rules 时返回空字符串。
        """
        parts: list[str] = []
        if self.agents_md:
            parts.append(self.agents_md)
        for rule in self.rules:
            parts.append(f"## {rule.name}\n\n{rule.content}")
        return "\n\n".join(parts)


def _read_text_safe(path: Path, max_size: int | None = None) -> str | None:
    """安全读取文本文件，失败返回 ``None``。

    Args:
        path: 文件路径。
        max_size: 可选最大字节数，超出则截断并追加 ``[truncated]`` 标记。
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("project_config.read_failed", file=str(path), error=str(exc))
        return None
    if max_size is not None and len(content) > max_size:
        content = content[:max_size] + "\n[truncated]"
    return content


def _parse_json_safe(path: Path, default: dict | list) -> dict | list:
    """安全解析 JSON 文件，失败返回 ``default`` + warning。"""
    raw = _read_text_safe(path)
    if raw is None:
        return default
    try:
        parsed = json.loads(raw)
        # 类型校验：dict 字段期望 dict，list 字段期望 list
        if isinstance(default, dict) and not isinstance(parsed, dict):
            logger.warning("project_config.json_type_mismatch", file=str(path), expected="dict")
            return default
        if isinstance(default, list) and not isinstance(parsed, list):
            logger.warning("project_config.json_type_mismatch", file=str(path), expected="list")
            return default
        return parsed
    except json.JSONDecodeError as exc:
        logger.warning("project_config.json_parse_failed", file=str(path), error=str(exc))
        return default


def _load_rules(rules_dir: Path) -> list[RuleFile]:
    """加载 ``rules/`` 目录下的 ``*.md`` 文件。

    - 按文件名排序
    - 最多 ``_MAX_RULE_FILES`` 个
    - 每个文件最大 ``_MAX_RULE_FILE_SIZE`` 字节，超出截断
    - 跳过 ``README.md`` 等说明文档（非规则）
    - 拒绝符号链接（防止指向 ``~/.ssh/id_rsa`` 等敏感文件泄漏到 LLM 上下文）
    """
    if not rules_dir.is_dir():
        return []

    md_files = sorted(rules_dir.glob("*.md"))
    rules: list[RuleFile] = []

    for md_file in md_files[:_MAX_RULE_FILES]:
        # 跳过说明文档（README.md 等）
        if md_file.name in _RULES_SKIP_NAMES:
            continue
        # 拒绝符号链接（安全：防止 rules/ 下放置指向敏感文件的 symlink）
        if md_file.is_symlink():
            logger.warning(
                "project_config.rule_symlink_skipped",
                file=str(md_file),
            )
            continue
        raw = _read_text_safe(md_file, max_size=_MAX_RULE_FILE_SIZE)
        if raw is None:
            continue
        # 文件名（不含 .md）作为标题
        name = md_file.stem
        rules.append(RuleFile(name=name, content=raw))

    return rules


def _read_agentx_text(path: Path, max_size: int) -> str | None:
    """读取 ``.agentx/`` 下的文本文件，含 symlink 防护 + 大小截断。

    - 拒绝符号链接（防止 ``.agentx/AGENTS.md`` 被替换为指向敏感文件的 symlink）
    - 超过 ``max_size`` 截断并追加 ``[truncated]``
    """
    if not path.exists():
        return None
    if path.is_symlink():
        logger.warning(
            "project_config.symlink_skipped",
            file=str(path),
        )
        return None
    return _read_text_safe(path, max_size=max_size)


def load_project_config(workspace_path: Path) -> ProjectConfig:
    """从 ``workspace_path/.agentx/`` 加载项目级配置。

    Args:
        workspace_path: 工作区根目录的绝对路径。

    Returns:
        ``ProjectConfig``。``.agentx/`` 不存在时返回 ``exists=False`` 的空配置。
    """
    agentx_dir = workspace_path / ".agentx"
    if not agentx_dir.is_dir():
        return ProjectConfig(workspace_path=workspace_path, exists=False)

    # AGENTS.md（含 symlink 防护 + 32KB 截断）
    agents_md = _read_agentx_text(agentx_dir / "AGENTS.md", _MAX_AGENTS_MD_SIZE)

    # mcp.json
    mcp_servers = _parse_json_safe(agentx_dir / "mcp.json", default=[])

    # subagents.json
    subagents_config = _parse_json_safe(agentx_dir / "subagents.json", default={})

    # tools.json
    tools_config = _parse_json_safe(agentx_dir / "tools.json", default={})

    # system_prompt.md（含 symlink 防护 + 32KB 截断）
    system_prompt = _read_agentx_text(
        agentx_dir / "system_prompt.md", _MAX_SYSTEM_PROMPT_SIZE
    )

    # rules/*.md（含 symlink 防护 + 4KB 截断 + README.md 排除）
    rules = _load_rules(agentx_dir / "rules")

    logger.info(
        "project_config.loaded",
        workspace=str(workspace_path),
        has_agents_md=agents_md is not None,
        rules_count=len(rules),
        mcp_count=len(mcp_servers) if isinstance(mcp_servers, list) else 0,
    )

    return ProjectConfig(
        workspace_path=workspace_path,
        agents_md=agents_md,
        rules=rules,
        mcp_servers=mcp_servers if isinstance(mcp_servers, list) else [],
        subagents_config=subagents_config if isinstance(subagents_config, dict) else {},
        tools_config=tools_config if isinstance(tools_config, dict) else {},
        system_prompt=system_prompt,
        exists=True,
    )
