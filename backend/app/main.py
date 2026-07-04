"""FastAPI 应用入口：lifespan 管理 + REST + SSE 端点。

端点总览：
- ``GET /``                       — 应用根健康检查。
- ``GET /api/health``             — 聚合 BGE-M3 + Milvus 健康状态（200 兜底）。
- ``POST /api/sandbox/authorize`` — 授权目录（拒绝系统关键目录 → 400）。
- ``POST /api/sandbox/revoke``    — 撤销授权。
- ``GET /api/sandbox/authorized/{thread_id}`` — 列出已授权目录。
- ``POST /api/chat/approve``      — 提交危险操作审批决定（写入内存 dict）。
- ``POST /api/chat/abort``        — 中止 SSE 流（写入内存 flag）。
- ``POST /api/chat``              — SSE 流式响应（Router 三路径分发：CHAT / SINGLE_TOOL / DEEP_TASK）。
- ``GET /api/memory/skills``      — 技能文件列表（不含完整 content）。
- ``GET /api/memory/skills/{name}`` — 读取单个技能文件完整内容。
- ``POST /api/memory/skills``     — 新建/覆盖技能文件（触发 reload_skills）。
- ``DELETE /api/memory/skills/{name}`` — 删除技能文件（触发 reload_skills）。
- ``GET /api/memory/profile``     — 用户画像列表。
- ``POST /api/memory/profile``    — 新建画像条目（key 重复 → 409）。
- ``PUT /api/memory/profile/{key}`` — 更新画像条目（不存在 → 404）。
- ``DELETE /api/memory/profile/{key}`` — 删除画像条目。
- ``POST /api/memory/profile/extract`` — LLM 抽取画像条目并写入。
- ``GET /api/memory/checkpointer`` — checkpointer 状态（db 大小 + thread 列表）。
- ``DELETE /api/memory/checkpointer/{thread_id}`` — 删除指定 thread 的 checkpoint。

跨进程状态：
- ``_pending_approvals: dict[str, bool]`` — thread_id → 审批决定，DeepAgent 轮询。
- ``_abort_flags: dict[str, bool]``       — thread_id → 中止标志，SSE 循环检查。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.embedding import get_embedding_client
from app.embedding.tei_client import healthcheck as embedding_healthcheck
from app.memory import (
    ProfileCategoryInvalid,
    ProfileContentTooLong,
    ProfileEntry,
    ProfileKeyInvalid,
    SkillNameInvalid,
    SkillPathEscape,
    ThreadIdInvalid,
    build_profile_prompt,
    close_checkpointer,
    delete_skill_file,
    delete_thread,
    get_async_checkpointer,
    get_checkpointer,
    get_db_size,
    get_skill_file,
    list_skills_files,
    list_threads,
)
from app.memory.skills_loader import get_skills, reload_skills
from app.memory.skills_store import save_skill_file
from app.observability.langsmith import mark_redacted, trace_span
from app.observability.logger import logger
from app.router import run_router
from app.tools.filesystem import list_workspace
from app.utils.security import PathNotAuthorized, get_sandbox
from app.vectorstore import MilvusUnavailable, get_milvus_client

# ---- 跨请求内存态（M2 迁移到 checkpoint / Redis）----
# thread_id → 审批决定（True=批准 / False=拒绝）。DeepAgent 经 wait_for_approval 消费。
_pending_approvals: dict[str, bool] = {}
# thread_id → 中止标志。SSE handler 每轮迭代检查。
_abort_flags: dict[str, bool] = {}

# CORS 允许的本地 origin（Electron renderer 与 dev server）
_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8123",
    "http://127.0.0.1:8123",
    "app://.",  # Electron production renderer
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动时初始化运行时目录 + checkpointer + 嵌入客户端 + Milvus（失败降级）。

    Milvus 凭证未配置或不可达时，记 warning 并继续启动（降级模式：检索工具返回
    错误字符串，其余功能正常）。关闭时优雅断开。
    """
    settings = get_settings()
    settings.ensure_runtime_dirs()

    # 0. Checkpointer 预热：同步 + 异步单例初始化
    try:
        get_checkpointer()
        await get_async_checkpointer()
        logger.info("checkpointer initialized on startup")
    except Exception as exc:  # noqa: BLE001
        logger.warning("checkpointer init failed on startup: {}", exc)

    # 1. 嵌入客户端：get_embedding_client() 懒构造，此处显式 warmup 记日志
    logger.info("embedding client initialized", url=settings.embedding_url)

    # 2. Milvus：仅当凭证配置时尝试连接，失败则降级
    milvus = get_milvus_client()
    if settings.milvus_credentials_configured:
        try:
            await milvus.connect()
            logger.info("Milvus connected on startup")
        except MilvusUnavailable as exc:
            # 降级模式：不阻塞启动，检索工具会返回降级字符串
            logger.warning("Milvus unavailable on startup, running degraded: {}", exc)
    else:
        logger.warning("Milvus credentials not configured, running degraded")

    try:
        yield
    finally:
        # 关闭：先 Milvus 后 embedding 后 checkpointer（逆序）
        if milvus._connected:  # noqa: SLF001 — 单例内部状态检查
            try:
                await milvus.disconnect()
                logger.info("Milvus disconnected on shutdown")
            except Exception as exc:  # noqa: BLE001 — 关闭阶段兜底
                logger.warning("Milvus disconnect failed: {}", exc)
        try:
            await get_embedding_client().aclose()
            logger.info("embedding client closed on shutdown")
        except Exception as exc:  # noqa: BLE001 — 关闭阶段兜底
            logger.warning("embedding client close failed: {}", exc)
        try:
            close_checkpointer()
            logger.info("checkpointer closed on shutdown")
        except Exception as exc:  # noqa: BLE001 — 关闭阶段兜底
            logger.warning("checkpointer close failed: {}", exc)


app = FastAPI(
    title="agent-py",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 请求体模型（Pydantic v2）
# ============================================================


class AuthorizeRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")
    path: str = Field(..., description="待授权目录绝对路径")
    writable: bool = Field(False, description="是否允许写入（默认只读）")


class RevokeRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")
    path: str = Field(..., description="待撤销目录绝对路径")


class ApproveRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")
    approval: bool = Field(..., description="True=批准 / False=拒绝")


class AbortRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")


class ChatRequest(BaseModel):
    message: str = Field(..., description="用户消息（/reset 触发会话重置）")
    thread_id: str = Field(..., description="会话 ID")


class SkillSaveRequest(BaseModel):
    """技能文件保存请求体。"""

    name: str = Field(..., description="技能名（不含 .md 扩展名）")
    content: str = Field(
        ...,
        max_length=65536,
        description="文件完整内容（YAML frontmatter + Markdown body），上限 64KB",
    )


class ProfileEntryRequest(BaseModel):
    """画像新建请求体。"""

    key: str
    category: str  # preference/project/fact/custom
    content: str


class ProfileUpdateRequest(BaseModel):
    """画像更新请求体。"""

    content: str
    category: str | None = None


class ExtractRequest(BaseModel):
    """LLM 抽取请求体。"""

    thread_id: str
    message: str
    assistant_reply: str


# ============================================================
# 健康检查
# ============================================================


@app.get("/")
async def root() -> dict[str, str]:
    """应用根健康检查。"""
    return {"app": "agent-py", "version": "0.1.0", "status": "ok"}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """聚合健康检查：BGE-M3 + Milvus 子项。整体 200 即使子项 unhealthy。"""
    # 嵌入服务健康：调 tei_client.healthcheck（嵌入 "healthcheck" 字符串）
    try:
        embedding_status = await embedding_healthcheck()
    except Exception as exc:  # noqa: BLE001 — 健康检查兜底
        embedding_status = {"status": "unhealthy", "error": str(exc)}

    # Milvus 健康：调 client.healthcheck（不抛异常）
    try:
        milvus_status = await get_milvus_client().healthcheck()
    except Exception as exc:  # noqa: BLE001 — 健康检查兜底
        milvus_status = {"status": "unhealthy", "error": str(exc)}

    return {"status": "ok", "embedding": embedding_status, "milvus": milvus_status}


# ============================================================
# 技能 API
# ============================================================


@app.get("/api/skills")
async def list_skills() -> dict[str, Any]:
    """返回已加载技能列表。

    每项含 ``name`` / ``description`` / ``trigger`` / ``tools`` / ``content_preview``（前 200 字符）。
    ``data/skills/`` 不存在时返回空列表。
    """
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
    skills = reload_skills()
    return {"ok": True, "count": len(skills)}


# ============================================================
# Workspace API
# ============================================================


@app.get("/api/workspace/list")
async def workspace_list(path: str = "data/workspace") -> dict[str, Any]:
    """列出沙箱白名单内目录的条目（含 type/size/mtime）。

    仅允许 ``data/workspace`` 和 ``data/uploads``，其他路径 → 400。
    """
    try:
        entries = await list_workspace(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"entries": entries}


# ============================================================
# 沙箱授权 API
# ============================================================


@app.post("/api/sandbox/authorize")
async def sandbox_authorize(req: AuthorizeRequest) -> dict[str, Any]:
    """授权目录。系统关键目录或非法路径 → 400。"""
    sandbox = get_sandbox()
    try:
        resolved = sandbox.authorize(req.thread_id, req.path, writable=req.writable)
    except ValueError as exc:
        # 系统关键目录
        logger.warning(
            "sandbox.authorize rejected",
            thread_id=req.thread_id,
            path=req.path,
            error=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc))
    except PathNotAuthorized as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "authorized": True,
        "path": str(resolved),
        "writable": req.writable,
    }


@app.post("/api/sandbox/revoke")
async def sandbox_revoke(req: RevokeRequest) -> dict[str, Any]:
    """撤销授权。"""
    sandbox = get_sandbox()
    revoked = sandbox.revoke(req.thread_id, req.path)
    return {"revoked": revoked}


@app.get("/api/sandbox/authorized/{thread_id}")
async def sandbox_authorized(thread_id: str) -> dict[str, Any]:
    """列出会话已授权目录。"""
    sandbox = get_sandbox()
    entries = sandbox.list_authorized(thread_id)
    return {"dirs": [{"path": str(p), "writable": w} for (p, w) in entries]}


# ============================================================
# 审批 / 中止 API
# ============================================================


@app.post("/api/chat/approve")
async def chat_approve(req: ApproveRequest) -> dict[str, Any]:
    """提交危险操作审批决定，写入 ``_pending_approvals`` 供 DeepAgent 消费。

    若 ``auto_approve_after_seconds > 0`` 且倒计时归零，记录 auto_approve trace；
    否则按用户实际操作记录 user_approve / user_reject。
    """
    settings = get_settings()
    _pending_approvals[req.thread_id] = req.approval

    # LangSmith trace：区分用户批准 / 拒绝 / 自动批准
    if req.approval and settings.auto_approve_after_seconds > 0:
        # 自动批准由 SSE handler 倒计时触发后调本端点（approval=True）
        span_name = "approval.auto_approve"
        action = "auto_approve"
        auto_approved = True
    elif req.approval:
        span_name = "approval.user_approve"
        action = "user_approve"
        auto_approved = False
    else:
        span_name = "approval.user_reject"
        action = "user_reject"
        auto_approved = False

    with trace_span(
        span_name,
        thread_id=req.thread_id,
        action=action,
        auto_approved=auto_approved,
        args=mark_redacted(),
    ):
        pass

    logger.info(
        "approval submitted",
        thread_id=req.thread_id,
        approval=req.approval,
        auto_approved=auto_approved,
    )
    return {"ok": True}


@app.post("/api/chat/abort")
async def chat_abort(req: AbortRequest) -> dict[str, Any]:
    """设置中止标志，SSE handler 在下一轮迭代退出。"""
    _abort_flags[req.thread_id] = True
    logger.info("abort flag set", thread_id=req.thread_id)
    return {"ok": True}


# ============================================================
# SSE 聊天端点
# ============================================================


async def _clear_thread_state(thread_id: str) -> None:
    """清空指定会话的 checkpointer 状态（best-effort）。

    尝试用异步 checkpointer 的 ``adelete_thread`` 删除该 thread 的所有 checkpoint。
    失败时仅记日志，不阻塞 /reset 流程。
    """
    try:
        checkpointer = await get_async_checkpointer()
        if hasattr(checkpointer, "adelete_thread"):
            await checkpointer.adelete_thread(thread_id)
            logger.info("checkpoint cleared for thread", thread_id=thread_id)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("clear checkpoint failed", thread_id=thread_id, error=str(exc))


async def _event_generator(req: ChatRequest) -> AsyncIterator[dict[str, str]]:
    """SSE 事件生成器：/reset 清空状态，其他消息走 Router 三路径分发。

    事件契约（与前端 preload 一致）：
    - ``token``         — 增量 token。
    - ``todo_update``   — DeepAgent 任务列表更新。
    - ``approval_request`` — 危险工具审批请求（含 tool_name / args / preview）。
    - ``done``          — 流结束。
    - ``error``         — 错误（含消息）。
    """
    settings = get_settings()
    logger.info(
        "chat request",
        thread_id=req.thread_id,
        message_len=len(req.message),
    )

    try:
        # /reset：清空 checkpointer + 沙箱（当不持久化时）
        if req.message.startswith("/reset"):
            await _clear_thread_state(req.thread_id)
            if not settings.persist_authorized_dirs:
                get_sandbox().clear(req.thread_id)
                yield {"event": "token", "data": "已清空会话状态与授权目录"}
            else:
                yield {"event": "token", "data": "已清空会话状态（授权目录已持久化，未清空）"}
            yield {"event": "done", "data": "{}"}
            return

        # 其他消息：走 Router 三路径分发
        async for event in run_router(req.message, req.thread_id):
            # 检查中止标志
            if _abort_flags.get(req.thread_id):
                yield {"event": "error", "data": "用户已中止"}
                _abort_flags.pop(req.thread_id, None)
                return
            yield event

    except Exception as exc:  # noqa: BLE001 — SSE 兜底，避免连接挂起
        logger.exception("SSE chat error", thread_id=req.thread_id)
        yield {"event": "error", "data": f"内部错误: {exc}"}


@app.post("/api/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    """SSE 流式聊天端点。

    事件契约：
    - ``token``         — 增量 token。
    - ``todo_update``   — DeepAgent 任务列表更新。
    - ``approval_request`` — 危险工具审批请求（含 tool_name / args / preview）。
    - ``done``          — 流结束。
    - ``error``         — 错误（含消息）。
    """
    return EventSourceResponse(_event_generator(req))


# ============================================================
# 记忆：技能文件 CRUD
# ============================================================


@app.get("/api/memory/skills")
async def memory_skills_list() -> dict[str, Any]:
    """返回技能文件列表（不含完整 content）。

    每项含 ``name`` / ``size`` / ``mtime`` / ``content_preview``。
    """
    files = list_skills_files()
    return {
        "skills": [
            {
                "name": f.name,
                "size": f.size,
                "mtime": f.mtime,
                "content_preview": f.content_preview,
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
    """新建/覆盖技能文件，触发 ``reload_skills``。"""
    try:
        save_skill_file(req.name, req.content)
    except (SkillNameInvalid, SkillPathEscape) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "name": req.name}


@app.delete("/api/memory/skills/{name}")
async def memory_skills_delete(name: str) -> dict[str, Any]:
    """删除技能文件，触发 ``reload_skills``。"""
    try:
        deleted = delete_skill_file(name)
    except (SkillNameInvalid, SkillPathEscape) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=404, detail=f"技能文件不存在: {name}")
    return {"ok": True, "deleted": True}


# ============================================================
# 记忆：用户画像 CRUD
# ============================================================


@app.get("/api/memory/profile")
async def memory_profile_list() -> dict[str, Any]:
    """返回全部画像条目。"""
    from app.memory.profile_store import get_all

    entries = get_all()
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

    调用 ``_extract_profile_via_llm`` 抽取，再 ``upsert_from_llm`` 写入。
    失败不报错（仅 warning 日志），返回 ``{extracted: 0}``。
    """
    from app.memory.profile_store import upsert_from_llm
    from app.paths.deep_path import _extract_profile_via_llm

    try:
        entries = await _extract_profile_via_llm(req.message, req.assistant_reply)
        written = upsert_from_llm(entries)
    except Exception as exc:  # noqa: BLE001 — 抽取失败不报错
        logger.warning("profile extract endpoint failed", error=str(exc))
        return {"extracted": 0}
    return {"extracted": written}


# ============================================================
# 记忆：Checkpointer 状态视图
# ============================================================


@app.get("/api/memory/checkpointer")
async def memory_checkpointer_status() -> dict[str, Any]:
    """返回 checkpointer 状态：db 文件大小 + thread 列表。"""
    db_size = await get_db_size()
    threads = await list_threads()
    return {"db_size": db_size, "threads": threads}


@app.delete("/api/memory/checkpointer/{thread_id}")
async def memory_checkpointer_delete(thread_id: str) -> dict[str, Any]:
    """删除指定 thread 的所有 checkpoint。"""
    try:
        deleted = await delete_thread(thread_id)
    except ThreadIdInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "deleted": deleted}


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
