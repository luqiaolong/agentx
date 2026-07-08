"""将项目级配置合并到全局 ``Settings`` 之上。

合并策略（项目覆盖全局）：
- MCP servers：追加模式（全局 + 项目，按 ``name`` 去重，项目优先）
- 子代理配置：深合并（项目字段递归覆盖全局同名字代理的字段）
- 工具开关：覆盖模式（项目 key 覆盖全局 key）
- 系统提示词：前置模式（项目提示词 + "\\n\\n" + 默认提示词）
- AGENTS.md / rules：项目独占（通过 ``context_prompt`` 注入）
- 凭证：全局独占（安全红线，不合并）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.config.settings import Settings
from app.config.subagents import SubagentSettings
from app.observability.logger import logger
from app.project_config.loader import ProjectConfig

__all__ = ["MergedConfig", "merge_configs"]


def _deep_merge(base: dict, override: dict) -> dict:
    """递归深合并：``override`` 的字段覆盖 ``base``，嵌套 dict 递归合并。

    - ``base`` / ``override`` 同 key 且都为 dict → 递归合并
    - 否则 ``override`` 的值覆盖 ``base`` 的值
    - 不修改入参（返回新 dict）
    """
    result = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass
class MergedConfig:
    """合并后的运行时配置。

    暴露与 ``Settings`` 同名的 property，便于下游透明替换。
    当 ``project`` 为 ``None`` 或 ``exists=False`` 时，所有 property 退化为
    ``base`` 的原始值。
    """

    base: Settings
    """原始全局 ``Settings``。"""

    project: ProjectConfig | None = None
    """项目级配置，``None`` = 无项目配置。"""

    @property
    def has_project_config(self) -> bool:
        """是否存在有效的项目配置。"""
        return self.project is not None and self.project.exists

    @property
    def context_prompt(self) -> str:
        """项目级上下文提示词（AGENTS.md + rules）。

        无项目配置时返回空字符串。
        """
        if not self.has_project_config or self.project is None:
            return ""
        return self.project.context_prompt

    @property
    def default_system_prompt(self) -> str:
        """合并后的系统提示词。

        项目提示词前置到默认提示词之前。无项目提示词时返回默认值。
        """
        base_prompt = self.base.default_system_prompt
        if not self.has_project_config or self.project is None:
            return base_prompt
        project_prompt = self.project.system_prompt
        if not project_prompt:
            return base_prompt
        return f"{project_prompt}\n\n{base_prompt}"

    @property
    def mcp_servers_config(self) -> list[Any]:
        """合并后的 MCP servers 配置。

        追加模式：全局 + 项目，按 ``name`` 去重（项目优先）。
        """
        base_servers = list(self.base.mcp_servers_config)
        if not self.has_project_config or self.project is None:
            return base_servers

        project_servers = self.project.mcp_servers
        if not project_servers:
            return base_servers

        # 按 name 去重：项目优先（项目存在同 name 则覆盖全局）
        result: list[Any] = []
        seen_names: set[str] = set()

        # 先放项目级（优先级高）
        for server in project_servers:
            if isinstance(server, dict):
                name = server.get("name", "")
                if name:
                    seen_names.add(name)
            result.append(server)

        # 再放全局级（跳过项目已有的 name）
        for server in base_servers:
            if isinstance(server, dict):
                name = server.get("name", "")
                if name and name in seen_names:
                    continue
            result.append(server)

        return result

    @property
    def tools_enabled(self) -> dict[str, bool]:
        """合并后的工具开关。

        覆盖模式：项目 key 覆盖全局 key。
        """
        result = self.base.tools_enabled.copy()
        if self.has_project_config and self.project is not None:
            result.update(self.project.tools_config)
        return result

    @property
    def subagents(self) -> dict[str, SubagentSettings]:
        """合并后的子代理配置。

        深合并：项目字段递归覆盖全局同名字代理的字段。
        """
        base_subagents = self.base.subagents
        if not self.has_project_config or self.project is None:
            return base_subagents

        project_config = self.project.subagents_config
        if not project_config:
            return base_subagents

        result = dict(base_subagents)
        for name, raw in project_config.items():
            if not isinstance(raw, dict):
                continue
            if name in result:
                # 深合并：递归合并项目值到全局值
                base_dump = result[name].model_dump()
                # 兼容前端 camelCase → snake_case 字段名映射
                override = dict(raw)
                if "systemPrompt" in override:
                    override["system_prompt"] = override.pop("systemPrompt")
                if "triggerDescription" in override:
                    override["trigger_description"] = override.pop("triggerDescription")
                merged = _deep_merge(base_dump, override)
                try:
                    result[name] = SubagentSettings(**merged)
                except ValidationError as exc:
                    # 项目配置含非法字段时降级为保留全局配置 + warning
                    logger.warning(
                        "project_config.subagent_merge_failed",
                        name=name,
                        error=str(exc),
                    )
            # 全局没有的新子代理配置：跳过（缺少默认值无法实例化，
            # 下游 custom_subagents 配置走独立路径）

        return result

    # 透传 base 的非合并属性（凭证、模型、Milvus 等）
    @property
    def openai_api_key(self) -> str | None:
        return self.base.openai_api_key

    @property
    def openai_base_url(self) -> str | None:
        return self.base.openai_base_url

    @property
    def default_model(self) -> str:
        return self.base.default_model

    @property
    def max_output_tokens(self) -> int | None:
        return self.base.max_output_tokens

    @property
    def context_max_messages(self) -> int:
        return self.base.context_max_messages

    @property
    def context_max_tokens(self) -> int:
        return self.base.context_max_tokens


def merge_configs(settings: Settings, project: ProjectConfig | None) -> MergedConfig:
    """将项目配置合并到全局 ``Settings`` 之上。

    Args:
        settings: 全局 ``Settings`` 实例（来自 ``get_settings()``）。
        project: 项目级配置，``None`` = 无项目配置（等价于纯全局配置）。

    Returns:
        ``MergedConfig``，暴露与 ``Settings`` 同名的 property。
    """
    return MergedConfig(base=settings, project=project)
