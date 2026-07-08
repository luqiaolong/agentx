"""Workspace 包：管理用户工作区（项目目录）的所有后端能力。

子模块：
- config: .agentx/ 项目级配置（生成/加载/合并到全局 Settings）
- api: /api/workspace/* 与 /api/project-config/* 路由（register_routes）

> 注意：本包下存在 ``config`` 子模块，与 ``app.config`` 顶级包（全局应用配置）
> 含义不同。``app.workspace.config`` 管 workspace 内的 .agentx/ 目录配置，
> ``app.config`` 管 pydantic Settings + 子代理 + 专家 prompt。两者职责独立。
"""

from app.workspace.api import register_routes

__all__ = ["register_routes"]
