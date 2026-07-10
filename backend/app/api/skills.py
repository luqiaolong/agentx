"""技能列表 / 重载路由。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query

from app.memory.skills_store import list_skills


def register_skills_routes(app: FastAPI) -> None:
    """注册技能路由（``GET /api/skills`` + ``POST /api/skills/reload``）。"""

    @app.get("/api/skills")
    async def list_skills_endpoint(
        workspace_path: str | None = Query(
            None,
            description="工作区路径；非空则合并工作区技能（覆盖全局同名技能）",
        ),
    ) -> dict[str, Any]:
        """返回已加载技能列表。

        每项含 ``name`` / ``description`` / ``trigger`` / ``tools`` /
        ``content_preview``（body 前 200 字符）/ ``path``（SKILL.md 绝对路径）。
        ``data/skills/`` 不存在时返回空列表。

        ``workspace_path`` 非空时合并工作区级技能（``<workspace>/.agentx/skills/``），
        同名时工作区优先；为 None 时仅扫描全局 ``data/skills/``。
        """
        skills = list_skills(workspace_path=workspace_path)
        return {
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "trigger": s.trigger,
                    "tools": s.tools,
                    "content_preview": s.content[:200],
                    "path": s.path,
                }
                for s in skills
            ]
        }

    @app.post("/api/skills/reload")
    async def skills_reload(
        workspace_path: str | None = Query(
            None,
            description="工作区路径；非空则合并工作区技能",
        ),
    ) -> dict[str, Any]:
        """强制重新扫描 ``data/skills/`` 并返回当前技能数量。

        技能加载已由 deepagents ``skills=`` 参数接管，本端点保留仅用于前端刷新列表。
        """
        skills = list_skills(workspace_path=workspace_path)
        return {"ok": True, "count": len(skills)}
