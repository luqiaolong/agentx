"""记忆路由：技能文件 CRUD + 用户画像 CRUD + Checkpointer 状态视图。

所有端点支持可选 ``workspace_path`` 查询参数：
- 非空 → 操作工作区级记忆（``<workspace>/.agentx/``）
- 为空 → 操作全局级记忆（``data/``）

读取端点（GET）合并工作区 + 全局，工作区优先。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import FastAPI, HTTPException, Query

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
    rewind_thread,
)
from app.sandbox.store import get_sandbox_store
from app.memory.skills_store import save_skill_file
from app.observability.logger import logger
from app.sandbox import get_sandbox


def register_memory_routes(app: FastAPI) -> None:
    """注册记忆路由（``/api/memory/*``）。"""

    # ============================================================
    # 技能文件 CRUD（工作区 + 全局）
    # ============================================================

    @app.get("/api/memory/skills")
    async def memory_skills_list(
        workspace_path: str | None = Query(None, description="工作区路径；非空则合并工作区技能"),
    ) -> dict[str, Any]:
        """返回技能文件列表（合并工作区 + 全局，不含完整 content）。

        每项含 ``name`` / ``size`` / ``mtime`` / ``content_preview`` / ``path`` / ``scope``。
        """
        files = list_skills_files(workspace_path=workspace_path)
        return {
            "skills": [
                {
                    "name": f.name,
                    "size": f.size,
                    "mtime": f.mtime,
                    "content_preview": f.content_preview,
                    "path": f.path,
                    "scope": f.scope,
                }
                for f in files
            ]
        }

    @app.get("/api/memory/skills/{name}")
    async def memory_skills_get(
        name: str,
        workspace_path: str | None = Query(None),
    ) -> dict[str, str]:
        """返回单个技能文件完整内容（工作区优先查找）。"""
        try:
            content = get_skill_file(name, workspace_path=workspace_path)
        except (SkillNameInvalid, SkillPathEscape) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"name": name, "content": content}

    @app.post("/api/memory/skills")
    async def memory_skills_save(
        req: SkillSaveRequest,
        workspace_path: str | None = Query(None, description="工作区路径；非空则写入工作区级"),
    ) -> dict[str, Any]:
        """新建/覆盖技能文件。``workspace_path`` 非空 → 写入工作区级。"""
        try:
            save_skill_file(req.name, req.content, workspace_path=workspace_path)
        except (SkillNameInvalid, SkillPathEscape) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "name": req.name}

    @app.delete("/api/memory/skills/{name}")
    async def memory_skills_delete(
        name: str,
        workspace_path: str | None = Query(None),
    ) -> dict[str, Any]:
        """删除技能文件（优先工作区，回退全局）。"""
        try:
            deleted = delete_skill_file(name, workspace_path=workspace_path)
        except (SkillNameInvalid, SkillPathEscape) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not deleted:
            raise HTTPException(status_code=404, detail=f"技能文件不存在: {name}")
        return {"ok": True, "deleted": True}

    # ============================================================
    # 用户画像 CRUD（工作区 + 全局）
    # ============================================================

    @app.get("/api/memory/profile")
    async def memory_profile_list(
        category: str | None = None,
        workspace_path: str | None = Query(None, description="工作区路径；非空则合并工作区画像"),
        scope: str | None = Query(
            None,
            description="限定来源：workspace=只读工作区级；默认合并工作区+全局",
        ),
    ) -> dict[str, Any]:
        """返回画像条目；可选按 category / scope 过滤。

        - ``scope=workspace``：只读工作区 profile.json + ``.agentx/memory/*.md``，
          不掺全局条目；``workspace_path`` 为空时返回空列表。
        - 默认：合并工作区 + 全局（工作区优先）。
        """
        from app.memory.profile_store import get_all as get_all_profile

        # profile.json 层（按 scope 决定是否合并全局）
        entries = get_all_profile(
            category, workspace_path=workspace_path, scope=scope
        )

        # 合并字典，便于后续 overlay 新格式
        merged: dict[str, Any] = {e.key: e for e in entries}

        # 再 overlay 新格式 .agentx/memory/*.md（同 key 覆盖旧数据）
        if workspace_path:
            from app.workspace.memory_store import list_entries

            try:
                ws_entries = list_entries(workspace_path, category=category)
                for e in ws_entries:
                    merged[e.key] = e
            except Exception as exc:  # noqa: BLE001
                logger.warning("workspace_memory.list_failed", workspace=workspace_path, error=str(exc))

        # 统一序列化为前端兼容的 dict 格式
        result = []
        for e in merged.values():
            if hasattr(e, "model_dump"):
                result.append(e.model_dump(mode="json"))
            elif hasattr(e, "to_dict"):
                result.append(e.to_dict())
            else:
                result.append(dict(e))
        return {"entries": result}

    @app.post("/api/memory/profile")
    async def memory_profile_add(
        req: ProfileEntryRequest,
        workspace_path: str | None = Query(None, description="工作区路径；非空则写入工作区级"),
    ) -> dict[str, Any]:
        """新建画像条目；key 重复 → 409。``workspace_path`` 非空 → 写入工作区级 ``.agentx/memory/*.md``。"""
        if workspace_path:
            from app.workspace.memory_store import get_entry, save_entry

            existing = get_entry(workspace_path, req.key)
            if existing is not None:
                raise HTTPException(status_code=409, detail=f"key 已存在: {req.key}，请用 PUT 更新")
            try:
                entry = await save_entry(
                    workspace_path,
                    req.key,
                    req.category,
                    req.content,
                    source="manual",
                    title=req.title,
                    keywords=req.keywords,
                    scenarios=req.scenarios,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            return {"ok": True, "entry": {**entry.to_dict(), "created_at": entry.updated_at, "scope": "workspace"}}

        # 全局画像仍走 profile.json
        from app.memory.profile_store import add

        entry = ProfileEntry(
            key=req.key,
            category=req.category,
            content=req.content,
            source="manual",
            created_at="",
            updated_at="",
            title=req.title,
            keywords=req.keywords,
            scenarios=req.scenarios,
        )
        try:
            new_entry = await add(entry, workspace_path=None)
        except (ProfileKeyInvalid, ProfileContentTooLong, ProfileCategoryInvalid) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"ok": True, "entry": new_entry.model_dump(mode="json")}

    @app.put("/api/memory/profile/{key}")
    async def memory_profile_update(
        key: str,
        req: ProfileUpdateRequest,
        workspace_path: str | None = Query(None),
    ) -> dict[str, Any]:
        """更新画像条目（优先工作区，回退全局）；不存在 → 404。"""
        if workspace_path:
            from app.workspace.memory_store import get_entry, save_entry

            existing = get_entry(workspace_path, key)
            if existing is not None:
                try:
                    entry = await save_entry(
                        workspace_path,
                        key,
                        req.category or existing.category,
                        req.content,
                        source="manual",
                        title=req.title if req.title is not None else existing.title,
                        keywords=req.keywords if req.keywords is not None else existing.keywords,
                        scenarios=req.scenarios if req.scenarios is not None else existing.scenarios,
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc))
                return {"ok": True, "entry": {**entry.to_dict(), "created_at": entry.updated_at, "scope": "workspace"}}

        # 回退全局
        from app.memory.profile_store import update

        try:
            updated = await update(
                key,
                req.content,
                req.category,
                workspace_path=None,
                title=req.title,
                keywords=req.keywords,
                scenarios=req.scenarios,
            )
        except (ProfileKeyInvalid, ProfileContentTooLong, ProfileCategoryInvalid) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"ok": True, "entry": updated.model_dump(mode="json")}

    @app.delete("/api/memory/profile/{key}")
    async def memory_profile_delete(
        key: str,
        workspace_path: str | None = Query(None),
    ) -> dict[str, Any]:
        """删除画像条目（优先工作区，回退全局）。"""
        if workspace_path:
            from app.workspace.memory_store import delete_entry

            try:
                deleted = await delete_entry(workspace_path, key)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            if deleted:
                return {"ok": True, "deleted": True}
            # 工作区不存在则回退全局删除

        from app.memory.profile_store import delete as profile_delete

        try:
            deleted = await profile_delete(key, workspace_path=None)
        except ProfileKeyInvalid as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "deleted": deleted}

    @app.post("/api/memory/profile/extract")
    async def memory_profile_extract(
        req: ExtractRequest,
        workspace_path: str | None = Query(None, description="工作区路径；非空则写入工作区级"),
    ) -> dict[str, Any]:
        """LLM 抽取画像条目并写入。``workspace_path`` 非空 → 写入工作区级 ``.agentx/memory/*.md``。"""
        from app.memory.profile_extractor import extract_profile_via_llm

        try:
            entries = await extract_profile_via_llm(req.message, req.assistant_reply)
        except Exception as exc:  # noqa: BLE001
            logger.warning("profile extract via llm failed", error=str(exc))
            return {"extracted": 0}

        if workspace_path:
            from app.workspace.memory_store import upsert_from_llm

            try:
                written = await upsert_from_llm(workspace_path, entries)
            except Exception as exc:  # noqa: BLE001
                logger.warning("workspace_memory.extract_upsert_failed", workspace=workspace_path, error=str(exc))
                return {"extracted": 0}
            return {"extracted": written}

        # 全局画像仍走 profile.json
        from app.memory.profile_store import upsert_from_llm

        try:
            written = await upsert_from_llm(entries, workspace_path=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("profile extract endpoint failed", error=str(exc))
            return {"extracted": 0}
        return {"extracted": written}

    # ============================================================
    # Dream 记忆整理
    # ============================================================

    @app.post("/api/memory/dream")
    async def memory_dream(
        workspace_path: str | None = Query(None, description="工作区路径（可选，提供时同时整理工作区记忆）"),
    ) -> dict[str, Any]:
        """整理全部记忆：全局画像 + 工作区记忆。

        - 读取全局 ``data/config/profile.json`` 全部条目
        - 若提供 workspace_path，同时读取 ``<workspace>/.agentx/memory/*.md``
        - LLM 分析后输出整理结果：
          - promoted → 提炼/移动到全局长期记忆
          - compressed → 压缩/合并后写回原位置
          - removed → 删除冗余/重复条目
        """
        from app.memory.dream import dream_all_memory
        from app.config import get_settings

        if not get_settings().dream_enabled:
            raise HTTPException(status_code=403, detail="Dream 功能未启用，请在设置 → 记忆中开启")
        try:
            result = await dream_all_memory(workspace_path=workspace_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dream failed", workspace=workspace_path, error=str(exc))
            raise HTTPException(status_code=500, detail=str(exc))
        return {"ok": True, **result}

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

    @app.post("/api/memory/checkpointer/{thread_id}/rewind")
    async def memory_checkpointer_rewind(
        thread_id: str,
        keep_messages_count: int = Query(..., ge=0, description="保留前多少条消息对应的状态"),
    ) -> dict[str, Any]:
        """回退指定 thread 的 checkpoint，保留编辑点之前的状态。

        用于「编辑历史消息」场景：用户编辑第 N 条消息后重新发送，
        需要回退到第 N 条消息之前的状态（保留前 N-1 条消息的上下文）。
        """
        try:
            result = await rewind_thread(thread_id, keep_messages_count)
        except ThreadIdInvalid as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except sqlite3.Error as exc:
            logger.warning(
                "rewind endpoint failed",
                thread_id=thread_id,
                keep_messages_count=keep_messages_count,
                error=str(exc),
            )
            raise HTTPException(status_code=500, detail=f"回退 checkpoint 失败: {exc}")
        return {"ok": True, **result}
