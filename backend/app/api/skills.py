"""技能列表 / 重载路由。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def register_skills_routes(app: FastAPI) -> None:
    """注册技能路由（``GET /api/skills`` + ``POST /api/skills/reload``）。"""

    @app.get("/api/skills")
    async def list_skills() -> dict[str, Any]:
        """返回已加载技能列表。

        每项含 ``name`` / ``description`` / ``trigger`` / ``tools`` / ``content_preview``（前 200 字符）。
        ``data/skills/`` 不存在时返回空列表。
        """
        # 延迟 import：测试通过 monkeypatch app.main.get_skills 注入 fake
        from app.main import get_skills

        skills = get_skills()
        return {
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "trigger": s.trigger,
                    "tools": s.tools,
                    "content_preview": s.content[:200],
                }
                for s in skills
            ]
        }

    @app.post("/api/skills/reload")
    async def skills_reload() -> dict[str, Any]:
        """强制重载技能列表（清缓存重新扫描 ``data/skills/``）。"""
        # 延迟 import：测试通过 monkeypatch app.main.reload_skills 注入 fake
        from app.main import reload_skills

        skills = reload_skills()
        return {"ok": True, "count": len(skills)}
