"""记忆路由：技能文件 CRUD + 用户画像 CRUD + Checkpointer 状态视图。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from app.api.schemas import (
    ExtractRequest,
    ProfileEntryRequest,
    ProfileUpdateRequest,
    SkillSaveRequest,
)
from app.memory import (
    ProfileCategoryInvalid,
    ProfileContentTooLong,
    ProfileEntry,
    ProfileKeyInvalid,
    SkillNameInvalid,
    SkillPathEscape,
    ThreadIdInvalid,
    delete_skill_file,
    delete_thread,
    get_db_size,
    get_skill_file,
    list_skills_files,
    list_threads,
)
from app.sandbox.store import get_sandbox_store
from app.memory.skills_store import save_skill_file
from app.observability.logger import logger
from app.sandbox import get_sandbox


def register_memory_routes(app: FastAPI) -> None:
    """注册记忆路由（``/api/memory/*``）。"""

    # ============================================================
    # 技能文件 CRUD
    # ============================================================

    @app.get("/api/memory/skills")
    async def memory_skills_list() -> dict[str, Any]:
        """返回技能文件列表（不含完整 content）。

        每项含 ``name`` / ``size`` / ``mtime`` / ``content_preview`` / ``path``。
        """
        files = list_skills_files()
        return {
            "skills": [
                {
                    "name": f.name,
                    "size": f.size,
                    "mtime": f.mtime,
                    "content_preview": f.content_preview,
                    "path": f.path,
                }
                for f in files
            ]
        }

    @app.get("/api/memory/skills/{name}")
    async def memory_skills_get(name: str) -> dict[str, str]:
        """返回单个技能文件完整内容。"""
        try:
            content = get_skill_file(name)
        except (SkillNameInvalid, SkillPathEscape) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"name": name, "content": content}

    @app.post("/api/memory/skills")
    async def memory_skills_save(req: SkillSaveRequest) -> dict[str, Any]:
        """新建/覆盖技能文件。"""
        try:
            save_skill_file(req.name, req.content)
        except (SkillNameInvalid, SkillPathEscape) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "name": req.name}

    @app.delete("/api/memory/skills/{name}")
    async def memory_skills_delete(name: str) -> dict[str, Any]:
        """删除技能文件。"""
        try:
            deleted = delete_skill_file(name)
        except (SkillNameInvalid, SkillPathEscape) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not deleted:
            raise HTTPException(status_code=404, detail=f"技能文件不存在: {name}")
        return {"ok": True, "deleted": True}

    # ============================================================
    # 用户画像 CRUD
    # ============================================================

    @app.get("/api/memory/profile")
    async def memory_profile_list(category: str | None = None) -> dict[str, Any]:
        """返回画像条目；可选按 category 过滤。"""
        from app.memory.profile_store import get_all

        entries = get_all(category)
        return {"entries": [e.model_dump(mode="json") for e in entries]}

    @app.post("/api/memory/profile")
    async def memory_profile_add(req: ProfileEntryRequest) -> dict[str, Any]:
        """新建画像条目；key 重复 → 409。"""
        from app.memory.profile_store import add

        entry = ProfileEntry(
            key=req.key,
            category=req.category,
            content=req.content,
            source="manual",
            created_at="",
            updated_at="",
        )
        try:
            new_entry = add(entry)
        except (ProfileKeyInvalid, ProfileContentTooLong, ProfileCategoryInvalid) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ValueError as exc:
            # key 已存在
            raise HTTPException(status_code=409, detail=str(exc))
        return {"ok": True, "entry": new_entry.model_dump(mode="json")}

    @app.put("/api/memory/profile/{key}")
    async def memory_profile_update(key: str, req: ProfileUpdateRequest) -> dict[str, Any]:
        """更新画像条目；不存在 → 404。"""
        from app.memory.profile_store import update

        try:
            updated = update(key, req.content, req.category)
        except (ProfileKeyInvalid, ProfileContentTooLong, ProfileCategoryInvalid) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"ok": True, "entry": updated.model_dump(mode="json")}

    @app.delete("/api/memory/profile/{key}")
    async def memory_profile_delete(key: str) -> dict[str, Any]:
        """删除画像条目。"""
        from app.memory.profile_store import delete as profile_delete

        try:
            deleted = profile_delete(key)
        except ProfileKeyInvalid as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "deleted": deleted}

    @app.post("/api/memory/profile/extract")
    async def memory_profile_extract(req: ExtractRequest) -> dict[str, Any]:
        """LLM 抽取画像条目并写入 profile.json。

        调用 ``extract_profile_via_llm`` 抽取，再 ``upsert_from_llm`` 写入。
        失败不报错（仅 warning 日志），返回 ``{extracted: 0}``。
        """
        from app.memory.profile_store import upsert_from_llm
        from app.memory.profile_extractor import extract_profile_via_llm

        try:
            entries = await extract_profile_via_llm(req.message, req.assistant_reply)
            written = upsert_from_llm(entries)
        except Exception as exc:  # noqa: BLE001 — 抽取失败不报错
            logger.warning("profile extract endpoint failed", error=str(exc))
            return {"extracted": 0}
        return {"extracted": written}

    # ============================================================
    # Checkpointer 状态视图
    # ============================================================

    @app.get("/api/memory/checkpointer")
    async def memory_checkpointer_status() -> dict[str, Any]:
        """返回 checkpointer 状态：db 文件大小 + thread 列表。"""
        db_size = await get_db_size()
        threads = await list_threads()
        return {"db_size": db_size, "threads": threads}

    @app.delete("/api/memory/checkpointer/{thread_id}")
    async def memory_checkpointer_delete(thread_id: str) -> dict[str, Any]:
        """删除指定 thread 的所有 checkpoint + 沙箱授权记录。"""
        try:
            deleted = await delete_thread(thread_id)
        except ThreadIdInvalid as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # 联动删除沙箱授权记录
        try:
            await get_sandbox_store().delete_by_thread(thread_id)
            await get_sandbox().clear(thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sandbox cleanup failed for thread {}: {}", thread_id, exc)
        return {"ok": True, "deleted": deleted}
