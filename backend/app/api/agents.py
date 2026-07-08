"""场景化智能体 API 路由。

提供 ``GET /api/agents/mentionable`` 端点，返回当前可用的 @mention agent 列表，
供前端输入框 @ 自动补全使用。

列表来源：
- Expert（``agents.experts`` 配置，当前仅 coding）
- 内置子代理（``subagents`` 配置，rag/web）
- 自定义子代理（``custom_subagents`` 配置）
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.config import BUILTIN_EXPERT_KEYS, BUILTIN_SUBAGENT_KEYS, get_settings

__all__ = ["register_agents_routes"]


def _build_mentionable_list() -> list[dict[str, Any]]:
    """构建可用 @mention agent 列表。

    仅返回 enabled=True 的条目。Expert 排在前面，子代理排在后面。
    """
    settings = get_settings()
    items: list[dict[str, Any]] = []

    # Expert（按 BUILTIN_EXPERT_KEYS 顺序 + 配置中的额外 Expert）
    experts = settings.agents.experts
    expert_keys = sorted(BUILTIN_EXPERT_KEYS | set(experts.keys()))
    for name in expert_keys:
        cfg = experts.get(name)
        if not cfg or not cfg.enabled:
            continue
        items.append(
            {
                "key": name,
                "type": "expert",
                "display_name": name,
                "trigger_description": cfg.scenario or name,
            }
        )

    # 内置子代理（按 BUILTIN_SUBAGENT_KEYS 顺序）
    subagents = settings.subagents
    for name in sorted(BUILTIN_SUBAGENT_KEYS):
        cfg = subagents.get(name)
        if not cfg or not cfg.enabled:
            continue
        items.append(
            {
                "key": name,
                "type": "subagent",
                "display_name": name,
                "trigger_description": cfg.trigger_description or name,
            }
        )

    # 自定义子代理（按 key 排序）
    custom_subagents = settings.custom_subagents
    for key in sorted(custom_subagents.keys()):
        cfg = custom_subagents[key]
        if not cfg.enabled:
            continue
        items.append(
            {
                "key": key,
                "type": "subagent",
                "display_name": cfg.name or key,
                "trigger_description": cfg.trigger_description or key,
            }
        )

    return items


def register_agents_routes(app: FastAPI) -> None:
    """注册场景化智能体相关路由。"""

    @app.get("/api/agents/mentionable")
    async def list_mentionable_agents() -> dict[str, Any]:
        """返回当前可用的 @mention agent 列表。

        前端在 work 模式下输入 ``@`` 时调用此端点获取自动补全候选。
        coding/coding_team 模式下 @mention 不生效，前端不调用本端点。

        Response:
        ```
        {
          "items": [
            {"key": "coding", "type": "expert", "display_name": "coding", "trigger_description": "coding"},
            {"key": "rag", "type": "subagent", "display_name": "rag", "trigger_description": "..."},
            {"key": "web", "type": "subagent", "display_name": "web", "trigger_description": "..."}
          ]
        }
        ```
        """
        return {"items": _build_mentionable_list()}

    @app.get("/api/agents/config")
    async def get_agents_config() -> dict[str, Any]:
        """返回场景化智能体配置摘要（供前端模式选择器决定可用模式）。

        Response:
        ```
        {
          "coding_team_enabled": true
        }
        ```
        """
        settings = get_settings()
        return {
            "coding_team_enabled": settings.agents.coding_team_enabled,
        }
