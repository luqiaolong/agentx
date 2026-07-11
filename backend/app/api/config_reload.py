"""配置热更新路由。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI

from app.api.schemas import ConfigReloadRequest
from app.config import reload_settings
from app.observability.logger import logger


def register_config_reload_routes(app: FastAPI) -> None:
    """注册配置热更新路由（``POST /api/config/reload``）。"""

    @app.post("/api/config/reload")
    async def config_reload(req: ConfigReloadRequest) -> dict[str, Any]:
        """热更新后端配置（无需重启进程）。

        将请求字段映射到 ``AGENTX_*`` env var，然后清除 ``get_settings`` 的
        ``lru_cache``。``get_chat_model`` / 子代理 / 工具 / 用户画像等运行时
        均通过 ``get_settings()`` 读取配置，因此热更新后立即生效。

        MCP 配置变更时额外触发 ``manager.refresh()`` 重连所有 server。
        """
        env_overrides: dict[str, str] = {}
        if req.default_model is not None:
            env_overrides["AGENTX_DEFAULT_MODEL"] = req.default_model
        if req.openai_api_key is not None:
            env_overrides["AGENTX_OPENAI_API_KEY"] = req.openai_api_key
        if req.openai_base_url is not None:
            env_overrides["AGENTX_OPENAI_BASE_URL"] = req.openai_base_url
        if req.deepseek_api_key is not None:
            env_overrides["AGENTX_DEEPSEEK_API_KEY"] = req.deepseek_api_key
        if req.kimi_api_key is not None:
            env_overrides["AGENTX_KIMI_API_KEY"] = req.kimi_api_key
        if req.glm_api_key is not None:
            env_overrides["AGENTX_GLM_API_KEY"] = req.glm_api_key
        if req.tavily_api_key is not None:
            env_overrides["AGENTX_TAVILY_API_KEY"] = req.tavily_api_key
        if req.max_output_tokens is not None:
            env_overrides["AGENTX_MAX_OUTPUT_TOKENS"] = str(req.max_output_tokens)
        if req.approval_max_wait is not None:
            env_overrides["AGENTX_APPROVAL_MAX_WAIT"] = str(req.approval_max_wait)
        if req.max_upload_bytes is not None:
            env_overrides["AGENTX_MAX_UPLOAD_BYTES"] = str(req.max_upload_bytes)
        if req.sandbox_mode is not None:
            env_overrides["AGENTX_SANDBOX_MODE"] = req.sandbox_mode
        if req.default_system_prompt is not None:
            env_overrides["AGENTX_DEFAULT_SYSTEM_PROMPT"] = req.default_system_prompt
        if req.subagents_config is not None:
            env_overrides["AGENTX_SUBAGENTS_CONFIG"] = json.dumps(req.subagents_config)
        if req.team_subagents_config is not None:
            env_overrides["AGENTX_TEAM_SUBAGENTS_CONFIG"] = json.dumps(req.team_subagents_config)
        if req.custom_subagents_config is not None:
            env_overrides["AGENTX_CUSTOM_SUBAGENTS_CONFIG"] = json.dumps(req.custom_subagents_config)
        if req.tools_config is not None:
            env_overrides["AGENTX_TOOLS_CONFIG"] = json.dumps(req.tools_config)
        if req.profile_auto_extract is not None:
            env_overrides["AGENTX_PROFILE_AUTO_EXTRACT"] = str(req.profile_auto_extract)
        if req.dream_enabled is not None:
            env_overrides["AGENTX_DREAM_ENABLED"] = str(req.dream_enabled)
        if req.mcp_servers_config is not None:
            env_overrides["AGENTX_MCP_SERVERS_CONFIG"] = json.dumps(req.mcp_servers_config)

        new_settings = reload_settings(env_overrides)

        # MCP 配置变更时触发重连（关闭旧连接 + 重新解析 + 初始化）
        mcp_refreshed = False
        if req.mcp_servers_config is not None:
            try:
                # 延迟 import：测试可能通过 monkeypatch app.main.get_mcp_manager
                from app.main import get_mcp_manager

                await get_mcp_manager().refresh()
                mcp_refreshed = True
            except Exception as exc:  # noqa: BLE001 — 热更新兜底
                logger.warning("MCP refresh after config reload failed: {}", exc)

        logger.info(
            "config reloaded: model={}, mcp_refreshed={}",
            new_settings.default_model,
            mcp_refreshed,
        )
        return {
            "ok": True,
            "default_model": new_settings.default_model,
            "mcp_refreshed": mcp_refreshed,
        }
