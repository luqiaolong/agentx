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
- ``GET /api/mcp/servers``        — MCP server 列表 + 连接状态（懒初始化）。
- ``GET /api/mcp/tools``          — 当前已发现的 MCP 工具列表。
- ``POST /api/mcp/servers/test``  — 测试单个 server 配置连接（不入主客户端状态）。
- ``POST /api/mcp/refresh``       — 强制重连所有 server（配置热更新后调用）。
- ``POST /api/config/reload``    — 热更新后端配置（无需重启进程，清除 settings 缓存）。

跨进程状态：
- ``_pending_approvals: dict[str, ApprovalDecision]`` — thread_id → 审批决策，DeepAgent 轮询。
- ``_abort_flags: dict[str, bool]``       — thread_id → 中止标志，SSE 循环检查。
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings, reload_settings
from app.embedding import get_embedding_client
from app.embedding.tei_client import healthcheck as embedding_healthcheck
from app.mcp import get_mcp_manager
from app.memory import (
    ProfileCategoryInvalid,
    ProfileContentTooLong,
    ProfileEntry,
    ProfileKeyInvalid,
    SkillNameInvalid,
    SkillPathEscape,
    ThreadIdInvalid,
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
from app.memory.sandbox_store import get_sandbox_store
from app.memory.skills_loader import get_skills, reload_skills
from app.memory.skills_store import save_skill_file
from app.observability.langsmith import mark_redacted, trace_span
from app.observability.logger import logger
from app.router import run_router
from app.tools.filesystem import list_workspace
from app.utils.security import ApprovalDecision, PathNotAuthorized, get_sandbox
from app.vectorstore import MilvusUnavailable, get_milvus_client

# ---- 跨请求内存态（M2 迁移到 checkpoint / Redis）----
# thread_id → 审批决策（含 decision/path/writable）。DeepAgent 经 wait_for_approval 消费。
_pending_approvals: dict[str, ApprovalDecision] = {}
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

    # 0.5. 沙箱授权从 DB 恢复
    # bootstrap_from_store 内部 try/except + 记日志（成功 bootstrap_loaded / 失败 bootstrap_failed），
    # 不阻塞启动，外层无需再包 try/except（否则失败时仍会误报 completed）。
    get_sandbox().bootstrap_from_store()

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
            logger.warning("checkpointer close failed on shutdown: {}", exc)
        # MCP 客户端：关闭所有 server 连接（stdio 子进程 / HTTP session）
        try:
            await get_mcp_manager().close()
            logger.info("MCP client closed on shutdown")
        except Exception as exc:  # noqa: BLE001 — 关闭阶段兜底
            logger.warning("MCP client close failed on shutdown: {}", exc)


app = FastAPI(
    title="agentx",
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


class UTF8JSONBodyMiddleware(BaseHTTPMiddleware):
    """application/json request body 编码探测与解码。

    背景：Windows Git Bash + curl 在命令行 ``-d '{"name":"测试"}'`` 时会做
    ``locale → wide-char → locale`` 双重转码，导致发送的字节流被序列化为
    GBK（即便 ``Content-Type: application/json`` 没声明 charset）。
    Starlette 默认按声明的 charset 解码 → 400。

    本 middleware 拦截 ``application/json`` 请求：
    1. UTF-8 解码成功 → 放回 body（正常路径）
    2. UTF-8 失败但 GBK 成功 → 转码为 UTF-8 再放回（兼容 Windows curl）
    3. 都失败 → 400 with 明确错误

    非 application/json 请求透传不动，避免误伤 form / multipart / SSE 上行。
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        content_type = (request.headers.get("content-type") or "").lower()
        if content_type.startswith("application/json"):
            raw = await request.body()
            if raw:
                # 1. 优先 UTF-8
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    # 2. 回退 GBK（Windows cmd / Git Bash 默认）
                    try:
                        text = raw.decode("gbk")
                    except UnicodeDecodeError:
                        from starlette.responses import JSONResponse
                        return JSONResponse(
                            {"detail": "request body is not valid UTF-8 or GBK"},
                            status_code=400,
                        )
                    # GBK 已是正确 Unicode，转回 UTF-8 字节给下游 Pydantic
                    request._body = text.encode("utf-8")  # noqa: SLF001
                else:
                    # UTF-8 合法，按原样放回
                    request._body = raw  # noqa: SLF001
        return await call_next(request)


app.add_middleware(UTF8JSONBodyMiddleware)


# ============================================================
# 请求体模型（Pydantic v2）
# ============================================================


class AuthorizeRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")
    path: str = Field(..., description="待授权目录绝对路径")
    writable: bool = Field(False, description="是否允许写入（默认只读）")
    source: Literal["manual", "chip", "legacy"] = Field(
        "manual", description="授权来源：manual（用户手动）/ chip（工作区自动同步）/ legacy（历史数据）"
    )


class RevokeRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")
    path: str = Field(..., description="待撤销目录绝对路径")


class ApproveRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")
    approval: bool = Field(..., description="True=批准 / False=拒绝")
    decision: str = Field(
        default="approve",
        description='审批决策类型：approve/deny（dangerous_tool）或 once/session/deny（directory_extension）',
    )
    path: str | None = Field(default=None, description="directory_extension 目标路径")
    writable: bool = Field(default=False, description="directory_extension 是否允许写入")


class AbortRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")


class ChatRequest(BaseModel):
    message: str = Field(..., description="用户消息（/reset 触发会话重置）")
    thread_id: str = Field(..., description="会话 ID")
    permission_mode: str = Field(
        default="workspace",
        description='权限模式：workspace（仅当前工作区，越界/危险操作审批）或 full_trust（会话内全量放行）',
    )
    system_prompt: str | None = Field(
        default=None,
        description="可选场景 prompt；非空时覆盖 default_system_prompt（场景切换器注入）",
    )
    agent_mode: str = Field(
        default="agent",
        description='代理模式：agent（单代理，默认）或 agent_team（多代理协作）',
    )


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


class McpServerTestRequest(BaseModel):
    """MCP server 连接测试请求体。

    用于 ``POST /api/mcp/servers/test``，前端提交单个 server 配置进行试探性连接，
    不写入主客户端状态，测试完即关闭。
    """

    name: str = Field(..., pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    transport: str = Field("stdio")
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    enabled: bool = True
    trusted: bool = False


class ConfigReloadRequest(BaseModel):
    """配置热更新请求体。所有字段可选，仅传需要更新的字段。

    传入的字段会映射到对应的 ``AGENTX_*`` env var，然后清除 ``get_settings`` 的
    ``lru_cache``，后续所有 ``get_settings()`` 调用返回新配置。MCP 配置变更时
    额外触发 ``manager.refresh()`` 重连。
    """

    # LLM
    default_model: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    deepseek_api_key: str | None = None
    tavily_api_key: str | None = None
    max_output_tokens: int | None = None
    # 审批
    approval_max_wait: float | None = None
    max_upload_bytes: int | None = None
    auto_approve_after_seconds: int | None = None
    # 系统提示词
    default_system_prompt: str | None = None
    # 子代理 + 工具 + 用户画像
    subagents_config: dict[str, Any] | None = None
    team_subagents_config: dict[str, Any] | None = None
    custom_subagents_config: dict[str, Any] | None = None
    tools_config: dict[str, Any] | None = None
    profile_auto_extract: bool | None = None
    # MCP
    mcp_servers_config: list[Any] | None = None


# ============================================================
# 健康检查
# ============================================================


@app.get("/")
async def root() -> dict[str, str]:
    """应用根健康检查。"""
    return {"app": "agentx", "version": "0.1.0", "status": "ok"}


async def _health_with_timeout(coro: Any, timeout: float = 1.0) -> dict[str, Any]:
    """带超时的健康检查子项，避免慢依赖拖垮整体响应。"""
    import asyncio

    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return {"status": "unhealthy", "error": "health check timeout"}
    except Exception as exc:  # noqa: BLE001 — 健康检查兜底
        return {"status": "unhealthy", "error": str(exc)}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """聚合健康检查：BGE-M3 + Milvus 子项。整体 200 即使子项 unhealthy。

    每个子项最多等待 1 秒，保证 Electron 启动轮询在 2 秒内拿到响应。
    """
    embedding_status = await _health_with_timeout(embedding_healthcheck(), timeout=1.0)
    milvus_status = await _health_with_timeout(get_milvus_client().healthcheck(), timeout=1.0)

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
async def workspace_list(
    path: str = "data/workspace",
    thread_id: str = "",
) -> dict[str, Any]:
    """列出沙箱白名单内或已授权目录的条目（含 type/size/mtime）。

    允许 ``data/workspace`` 和 ``data/uploads``（始终可读），
    以及通过 ``POST /api/sandbox/authorize`` 授权给 ``thread_id`` 的目录。
    其他路径 → 400。
    """
    try:
        entries = await list_workspace(path, thread_id or None)
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
        resolved = sandbox.authorize(
            req.thread_id, req.path, writable=req.writable, source=req.source
        )
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
    """提交审批决定，写入 ``_pending_approvals`` 供 DeepAgent 消费。

    支持两种审批场景：
    - dangerous_tool：approval=True/False，decision="approve"/"deny"
    - directory_extension：decision="once"/"session"/"deny"，path/writable 描述目标

    若 ``auto_approve_after_seconds > 0`` 且倒计时归零，记录 auto_approve trace；
    否则按用户实际操作记录 user_approve / user_reject。
    """
    settings = get_settings()
    _pending_approvals[req.thread_id] = ApprovalDecision(
        approved=req.approval,
        decision=req.decision,
        path=req.path,
        writable=req.writable,
    )

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
        decision=req.decision,
        path=req.path,
        args=mark_redacted(),
    ):
        pass

    logger.info(
        "approval submitted",
        thread_id=req.thread_id,
        approval=req.approval,
        decision=req.decision,
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

    事件契约（与前端 preload 一致，chat-rendering-trace-v2 扩展）：
    - ``token``         — 增量 token（visible text，已剥离 ``<think>`` 块）。
    - ``reasoning``     — 思考过程 chunk（data 为 JSON ``{"content": str, "source": str}``，
                          由 ThinkFilter retain_think 模式从 token 流分离）。
    - ``tool_call``     — 工具调用开始（data 为 JSON ``{"id","name","args","source"}``，
                          id 用于前端配对 tool_result）。
    - ``tool_result``   — 工具调用结束（data 为 JSON ``{"id","name","result","source","error?"}``）。
    - ``delegation``    — 子代理委派标记（data 为 JSON ``{"target","source","message"}``）。
    - ``todo_update``   — DeepAgent 任务列表更新。
    - ``approval_request`` — 危险工具/目录扩展审批请求（含 tool_name / args / preview）。
    - ``team_plan``     — AgentTeam Orchestrator 生成的子任务计划（data 为 JSON
                          ``{"plan": [{"agent","input","purpose"}], "reasoning": str}``）。
    - ``team_progress`` — AgentTeam 子任务状态变化（data 为 JSON
                          ``{"agent": str, "status": "running"|"done"|"error", "message?": str}``）。
    - ``team_result``   — AgentTeam 子任务结果摘要（data 为 JSON ``{"agent": str, "summary": str}``）。
    - ``team_done``     — AgentTeam 整体结束（data 为 JSON ``{"status": "done"|"error"}``）。
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

        # 其他消息：走 Router 分发（传入 checkpointer 加载历史）
        checkpointer = await get_async_checkpointer()
        effective_agent_mode = req.agent_mode if settings.agent_team_enabled else "agent"
        async for event in run_router(
            req.message,
            req.thread_id,
            checkpointer=checkpointer,
            permission_mode=req.permission_mode,
            scene_prompt=req.system_prompt,
            agent_mode=effective_agent_mode,
        ):
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

    事件契约（chat-rendering-trace-v2 扩展，详见 ``_event_generator`` docstring）：
    - ``token`` / ``reasoning`` / ``tool_call`` / ``tool_result`` / ``delegation``
    - ``todo_update`` / ``approval_request`` / ``done`` / ``error``
    """
    return EventSourceResponse(_event_generator(req))


class CompactRequest(BaseModel):
    """``/compact`` 请求体。"""

    thread_id: str = Field(..., description="会话 ID")


@app.post("/api/chat/compact")
async def chat_compact(req: CompactRequest) -> dict[str, Any]:
    """压缩会话历史：把早期消息摘要化，保留最近 2 条。

    流程:
    1. 从 checkpointer 读取 ``thread_id`` 的历史 messages
    2. 若消息不足 4 条，返回 ``{ok: false, error: "消息不足"}``
    3. 调 LLM 把前 N-2 条压缩成摘要
    4. 用 ``[SystemMessage(summary), 最近 2 条]`` 写回 checkpoint
    5. 返回 ``{ok: true, summary: str, compressed_count: int}``

    LLM 失败时不写回 checkpoint，返回 ``{ok: false, error: str}``。
    """
    from langchain_core.messages import SystemMessage

    from app.memory import summarize_messages

    checkpointer = await get_async_checkpointer()
    config = {"configurable": {"thread_id": req.thread_id}}

    # 1. 读取历史 messages
    try:
        if hasattr(checkpointer, "aget"):
            checkpoint = await checkpointer.aget(config)
        else:
            checkpoint = checkpointer.get(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("compact: load checkpoint failed", thread_id=req.thread_id, error=str(exc))
        return {"ok": False, "error": f"加载 checkpoint 失败: {exc}"}

    if not checkpoint:
        return {"ok": False, "error": "无 checkpoint 可压缩"}

    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    messages = list(channel_values.get("messages", []))
    if len(messages) < 4:
        return {"ok": False, "error": "消息不足，无需压缩"}

    # 2. 调 LLM 压缩前 N-2 条
    to_compress = messages[:-2]
    keep_recent = messages[-2:]
    try:
        summary = await summarize_messages(to_compress)
    except Exception as exc:  # noqa: BLE001
        logger.warning("compact: summarize failed", thread_id=req.thread_id, error=str(exc))
        return {"ok": False, "error": f"摘要生成失败: {exc}"}

    # 3. 写回 checkpoint：[SystemMessage(summary), *keep_recent]
    new_messages = [SystemMessage(content=summary), *keep_recent]
    new_channel_values = {**channel_values, "messages": new_messages}
    new_checkpoint = {**checkpoint, "channel_values": new_channel_values}

    try:
        if hasattr(checkpointer, "aput"):
            await checkpointer.aput(config, new_checkpoint, {"messages": "any"}, [])
        elif hasattr(checkpointer, "put"):
            checkpointer.put(config, new_checkpoint, {"messages": "any"}, [])
        else:
            return {"ok": False, "error": "checkpointer 不支持写回"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("compact: write checkpoint failed", thread_id=req.thread_id, error=str(exc))
        return {"ok": False, "error": f"写回 checkpoint 失败: {exc}"}

    logger.info(
        "compact done",
        thread_id=req.thread_id,
        compressed_count=len(to_compress),
        summary_len=len(summary),
    )
    return {
        "ok": True,
        "summary": summary,
        "compressed_count": len(to_compress),
    }


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
    """删除指定 thread 的所有 checkpoint + 沙箱授权记录。"""
    try:
        deleted = await delete_thread(thread_id)
    except ThreadIdInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # 联动删除沙箱授权记录
    try:
        get_sandbox_store().delete_by_thread(thread_id)
        get_sandbox().clear(thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sandbox cleanup failed for thread {}: {}", thread_id, exc)
    return {"ok": True, "deleted": deleted}


# ============================================================
# MCP (Model Context Protocol) API
# ============================================================
#
# MCP server 配置由 Electron Main 从 electron-store 读取后通过
# ``AGENTX_MCP_SERVERS_CONFIG`` 环境变量注入。后端启动时解析配置，
# 首次 ``GET /api/mcp/servers`` 或 ``GET /api/mcp/tools`` 时懒连接所有启用的 server。
# 配置变更（前端增删改）需重启后端生效，``POST /api/mcp/refresh`` 可强制重连
# （仅适用于未改配置的重连场景，配置变更必须重启）。


@app.get("/api/mcp/servers")
async def mcp_servers_list() -> dict[str, Any]:
    """返回 MCP server 列表 + 连接状态。

    首次调用触发懒初始化（连接所有启用的 server）。返回每项含 ``name`` /
    ``transport`` / ``enabled`` / ``trusted`` / ``connected`` / ``error`` /
    ``tool_count``。
    """
    manager = get_mcp_manager()
    servers = await manager.list_servers()
    return {"servers": servers}


@app.get("/api/mcp/tools")
async def mcp_tools_list() -> dict[str, Any]:
    """返回当前已发现的 MCP 工具列表（name + description）。"""
    manager = get_mcp_manager()
    tools = await manager.get_tools()
    return {
        "tools": [
            {
                "name": getattr(t, "name", ""),
                "description": getattr(t, "description", "") or "",
            }
            for t in tools
        ]
    }


@app.post("/api/mcp/servers/test")
async def mcp_servers_test(req: McpServerTestRequest) -> dict[str, Any]:
    """测试单个 server 配置连接，返回工具列表或错误。

    不影响主客户端状态，测试完即关闭。前端「测试连接」按钮调用。
    """
    from app.mcp.config import McpServerConfig

    try:
        cfg = McpServerConfig(
            name=req.name,
            transport=req.transport,  # type: ignore[arg-type]
            command=req.command,
            args=req.args,
            env=req.env,
            url=req.url,
            enabled=req.enabled,
            trusted=req.trusted,
        )
    except Exception as exc:  # noqa: BLE001 — 配置校验失败
        raise HTTPException(status_code=400, detail=str(exc))

    manager = get_mcp_manager()
    result = await manager.test_server(cfg)
    return result


@app.post("/api/mcp/refresh")
async def mcp_refresh() -> dict[str, Any]:
    """强制重连所有 server（关闭旧连接 + 重新解析配置 + 初始化）。

    仅适用于「未改配置但需重连」的场景（如远端 server 重启后恢复）。
    配置变更（增删改 server）必须重启后端——env 变量在启动期注入，运行时不可改。
    """
    manager = get_mcp_manager()
    await manager.refresh()
    servers = await manager.list_servers()
    return {"ok": True, "servers": servers}


# ============================================================
# 配置热更新
# ============================================================


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
    if req.tavily_api_key is not None:
        env_overrides["AGENTX_TAVILY_API_KEY"] = req.tavily_api_key
    if req.max_output_tokens is not None:
        env_overrides["AGENTX_MAX_OUTPUT_TOKENS"] = str(req.max_output_tokens)
    if req.approval_max_wait is not None:
        env_overrides["AGENTX_APPROVAL_MAX_WAIT"] = str(req.approval_max_wait)
    if req.max_upload_bytes is not None:
        env_overrides["AGENTX_MAX_UPLOAD_BYTES"] = str(req.max_upload_bytes)
    if req.auto_approve_after_seconds is not None:
        env_overrides["AGENTX_AUTO_APPROVE_AFTER_SECONDS"] = str(req.auto_approve_after_seconds)
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
    if req.mcp_servers_config is not None:
        env_overrides["AGENTX_MCP_SERVERS_CONFIG"] = json.dumps(req.mcp_servers_config)

    new_settings = reload_settings(env_overrides)

    # MCP 配置变更时触发重连（关闭旧连接 + 重新解析 + 初始化）
    mcp_refreshed = False
    if req.mcp_servers_config is not None:
        try:
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


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
