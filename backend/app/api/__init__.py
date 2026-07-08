"""API 路由注册聚合。

各域路由在 ``register_routes(app)`` 中依次注册到 FastAPI app 实例。
端点实现拆分至 ``app/api/<domain>.py``，Pydantic 模型集中于 ``app/api/schemas.py``。
"""

from __future__ import annotations

from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
    """注册全部 API 路由到给定 FastAPI app 实例。

    依次调用 10 个域注册函数。使用延迟 import 避免循环依赖
    （``app.main`` import 本模块时，本模块不能再 top-level import ``app.main``）。
    """
    from app.api.agents import register_agents_routes
    from app.api.chat import register_chat_routes
    from app.api.config_reload import register_config_reload_routes
    from app.api.health import register_health_routes
    from app.api.mcp import register_mcp_routes
    from app.api.memory import register_memory_routes
    from app.api.models_test import register_models_test_routes
    from app.sandbox.api import register_sandbox_routes
    from app.api.skills import register_skills_routes
    from app.api.workspace import register_workspace_routes

    register_health_routes(app)
    register_sandbox_routes(app)
    register_skills_routes(app)
    register_workspace_routes(app)
    register_chat_routes(app)
    register_agents_routes(app)
    register_memory_routes(app)
    register_mcp_routes(app)
    register_config_reload_routes(app)
    register_models_test_routes(app)
