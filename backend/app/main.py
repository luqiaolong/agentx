"""FastAPI 应用入口：lifespan 管理 + 中间件 + 路由注册。

端点总览：
- ``GET /``                       — 应用根健康检查。
- ``GET /api/health``             — 聚合 BGE-M3 + Milvus 健康状态（200 兜底）。
- ``POST /api/sandbox/authorize`` — 授权目录（拒绝系统关键目录 → 400）。
- ``POST /api/sandbox/revoke``    — 撤销授权。
- ``GET /api/sandbox/authorized/{thread_id}`` — 列出已授权目录。
- ``GET /api/workspace/list``     — 沙箱白名单 / 已授权目录的条目列表。
- ``GET /api/workspace/read``     — 读取沙箱内文本文件内容（CodeViewer 用，>2MiB 拒绝）。
- ``POST /api/chat/approve``      — 提交危险操作审批决定（写入内存 dict）。
- ``POST /api/chat/abort``        — 中止 SSE 流（写入内存 flag）。
- ``POST /api/chat``              — SSE 流式响应（Router 三路径分发：CHAT / SINGLE_TOOL / DEEP_TASK）。
- ``GET /api/memory/skills``      — 技能文件列表（不含完整 content）。
- ``GET /api/memory/skills/{name}`` — 读取单个技能文件完整内容。
- ``POST /api/memory/skills``     — 新建/覆盖技能文件。
- ``DELETE /api/memory/skills/{name}`` — 删除技能文件。
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
- ``POST /api/models/test``      — 模型连接测试（设置面板「测试」按钮，发送最小 chat completion 请求）。

端点实现已拆分至 ``app.api`` 包（``app/api/<domain>.py``），Pydantic 模型集中于
``app/api/schemas.py``，由 ``register_routes(app)`` 统一注册。本模块保留
lifespan / 中间件 / app 实例 / ``__main__`` 入口。

以下名称以本模块为「命名空间锚点」向后兼容 re-export（测试通过
``monkeypatch app.main.<name>`` 或 ``from app.main import <name>`` 访问）：
- ``run_router`` / ``list_workspace`` / ``read_workspace_file``
  / ``read_workspace_file``
  / ``get_sandbox`` / ``get_async_checkpointer`` / ``get_mcp_manager`` / ``httpx``
  —— ``app.api.*`` 域文件内以延迟 ``from app.main import <name>`` 方式引用，
  保证 monkeypatch 在调用时生效。
- ``ChatRequest`` / ``_event_generator`` 等 schemas 与辅助函数 —— 直接 re-export。

跨进程状态（已迁移至 ``app.security.approval`` 模块）：
- 审批决策 dict — 见 ``app.security.approval.state.submit_approval`` / ``pop_approval``。
- 中止标志 dict — 见 ``app.security.approval.state.set_abort`` / ``is_aborted``。
- TTL reaper — 见 ``app.security.approval.state.start_reaper``（lifespan 启动）。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx  # noqa: F401 — re-export：测试 patch app.main.httpx.AsyncClient（模块级全局生效）
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ---- lifespan / 中间件依赖 ----
from app.config import get_settings
from app.embedding import get_embedding_client
from app.mcp import get_mcp_manager  # noqa: F401 — re-export：测试 monkeypatch app.main.get_mcp_manager
from app.memory import (
    aclose_checkpointer,
    close_checkpointer,  # noqa: F401 — re-export：CLI 等同步上下文使用
    get_async_checkpointer,  # noqa: F401 — re-export：测试 monkeypatch app.main.get_async_checkpointer
    get_checkpointer,
)
from app.observability.logger import logger
from app.observability.observation import (
    cleanup_old_observations,
    get_observation_sink,
    start_observation_reaper,
)
from app.vectorstore import MilvusUnavailable, get_milvus_client

# ---- 以下 import 仅为向后兼容 re-export（测试 monkeypatch app.main.<name>） ----
# app.api.* 域文件以延迟 `from app.main import <name>` 引用这些名字，
# 使 monkeypatch 在调用时从 app.main 命名空间取到 fake。
from app.router import run_router  # noqa: F401
from app.tools.filesystem import list_workspace, read_workspace_file  # noqa: F401
from app.sandbox import get_sandbox  # noqa: F401 — lifespan 亦用

# ---- 向后兼容 re-export（测试 from app.main import X）----
from app.api.schemas import *  # noqa: F401, F403 — ChatRequest / ApproveRequest 等模型
from app.api.chat import _event_generator  # noqa: F401 — 测试 from app.main import _event_generator

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
    await get_sandbox().bootstrap_from_store()

    # 0.6. 启动审批状态 reaper（清理 30 分钟无活动的 thread_id）
    from app.security.approval import start_reaper

    reaper_task = start_reaper()

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

    # 3. Observation sink：预热单例（建表 + WAL）+ 启动 TTL 清理
    #    失败不阻塞启动（观测降级，agent 主流程不受影响）
    observation_sink = None
    observation_reaper_task = None
    try:
        observation_sink = get_observation_sink()
        deleted = await cleanup_old_observations()
        if deleted > 0:
            logger.info("observation cleanup removed {} rows on startup", deleted)
        # FR-11.1: 启动后台 6h 周期 reaper，lifespan 关闭时 cancel
        observation_reaper_task = start_observation_reaper(interval_hours=6.0)
        logger.info("observation sink initialized on startup")
    except Exception as exc:  # noqa: BLE001
        logger.warning("observation sink init failed on startup: {}", exc)

    # 3.5. Checkpointer TTL 清理：删除超过 TTL 未活动的 thread checkpoint
    checkpoint_reaper_task = None
    try:
        from app.memory.checkpointer_view import cleanup_expired_checkpoints, start_checkpoint_reaper
        deleted_cp = await cleanup_expired_checkpoints()
        if deleted_cp > 0:
            logger.info("checkpoint cleanup removed {} expired threads on startup", deleted_cp)
        checkpoint_reaper_task = start_checkpoint_reaper(interval_hours=6.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("checkpoint cleanup init failed on startup: {}", exc)

    try:
        yield
    finally:
        # 关闭：先 drain 画像抽取任务，再 reaper，再 Milvus 后 embedding 后 checkpointer（逆序）
        # 画像抽取是 fire-and-forget，关闭前给它们 5s 完成窗口，避免数据丢失
        try:
            from app.deepagent.agent import drain_extract_tasks

            await drain_extract_tasks(timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("drain extract tasks failed on shutdown: {}", exc)
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass
        if observation_reaper_task is not None:
            observation_reaper_task.cancel()
            try:
                await observation_reaper_task
            except asyncio.CancelledError:
                pass
        if checkpoint_reaper_task is not None:
            checkpoint_reaper_task.cancel()
            try:
                await checkpoint_reaper_task
            except asyncio.CancelledError:
                pass
        if milvus._connected:  # noqa: SLF001 — 单例内部状态检查
            try:
                await milvus.disconnect()
                logger.info("Milvus disconnected on shutdown")
            except Exception as exc:  # noqa: BLE001 — 关闭阶段兜底
                logger.warning("Milvus disconnect failed on shutdown: {}", exc)
        try:
            await get_embedding_client().aclose()
            logger.info("embedding client closed on shutdown")
        except Exception as exc:  # noqa: BLE001 — 关闭阶段兜底
            logger.warning("embedding client close failed on shutdown: {}", exc)
        try:
            await aclose_checkpointer()
            logger.info("checkpointer closed on shutdown")
        except Exception as exc:  # noqa: BLE001 — 关闭阶段兜底
            logger.warning("checkpointer close failed on shutdown: {}", exc)
        # MCP 客户端：关闭所有 server 连接（stdio 子进程 / HTTP session）
        try:
            await get_mcp_manager().close()
            logger.info("MCP client closed on shutdown")
        except Exception as exc:  # noqa: BLE001 — 关闭阶段兜底
            logger.warning("MCP client close failed on shutdown: {}", exc)
        # Observation sink：关闭（仅标记 closed，SQLite 短连接无需释放）
        if observation_sink is not None:
            try:
                await observation_sink.close()
                logger.info("observation sink closed on shutdown")
            except Exception as exc:  # noqa: BLE001 — 关闭阶段兜底
                logger.warning("observation sink close failed on shutdown: {}", exc)


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


class UTF8JSONBodyMiddleware:
    """application/json request body 编码探测与解码（纯 ASGI 中间件）。

    背景：Windows Git Bash + curl 在命令行 ``-d '{"name":"测试"}'`` 时会做
    ``locale → wide-char → locale`` 双重转码，导致发送的字节流被序列化为
    GBK（即便 ``Content-Type: application/json`` 没声明 charset）。
    Starlette 默认按声明的 charset 解码 → 400。

    本 middleware 拦截 ``application/json`` 请求的 **request body receive callable**：
    1. UTF-8 解码成功 → 放回 body（正常路径）
    2. UTF-8 失败但 GBK 成功 → 转码为 UTF-8 再放回（兼容 Windows curl）
    3. 都失败 → 400 with 明确错误

    非 application/json 请求透传不动，避免误伤 form / multipart / SSE 上行。

    M6: 改为纯 ASGI 中间件（不再继承 BaseHTTPMiddleware）。BaseHTTPMiddleware
    内部用 memory object stream 桥接 request/response，会延迟 SSE 断连传播
    （客户端断开后 GeneratorExit 不能及时到达 generator），导致 abort/pause
    标志泄漏。纯 ASGI 实现只包装 receive callable，response stream 直接透传
    send callable，断连信号可即时传播到下游 generator。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # 检查是否是 application/json 请求
        headers = dict(scope.get("headers", []))
        content_type = headers.get(b"content-type", b"").decode("latin-1", "ignore").lower()
        if not content_type.startswith("application/json"):
            await self.app(scope, receive, send)
            return
        # 读取完整 request body
        body = b""
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.request":
                body += message.get("body", b"")
                more_body = message.get("more_body", False)
            else:
                # http.disconnect 等非 request 事件：直接透传
                await self.app(scope, receive, send)
                return
        if not body:
            # 空 body：用包装后的 receive 透传（保持 consumed 语义）
            await self.app(scope, _make_single_body_receive(b"", receive), send)
            return
        # 1. 优先 UTF-8
        try:
            body.decode("utf-8")
            new_body = body
        except UnicodeDecodeError:
            # 2. 回退 GBK（Windows cmd / Git Bash 默认）
            try:
                text = body.decode("gbk")
            except UnicodeDecodeError:
                # 3. 都失败 → 400 with 明确错误
                import json as _json

                payload = _json.dumps(
                    {"detail": "request body is not valid UTF-8 or GBK"}
                ).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 400,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(payload)).encode("latin-1")),
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": payload,
                        "more_body": False,
                    }
                )
                return
            # GBK 已是正确 Unicode，转回 UTF-8 字节给下游 Pydantic
            new_body = text.encode("utf-8")
        # 用包装后的 receive 把转换后的 body 注入下游
        await self.app(scope, _make_single_body_receive(new_body, receive), send)


def _make_single_body_receive(body: bytes, original_receive):
    """构造一个返回 body 后委托给原始 receive 的 receive callable。

    第一次调用返回 body（http.request），后续调用委托给 ``original_receive``，
    确保 ``EventSourceResponse._listen_for_disconnect`` 等消费者能正确收到
    ``http.disconnect`` 事件，避免 busy-loop 饿死事件循环。
    """
    consumed = False

    async def wrapped_receive():
        nonlocal consumed
        if not consumed:
            consumed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await original_receive()

    return wrapped_receive


app.add_middleware(UTF8JSONBodyMiddleware)


# ============================================================
# 路由注册（端点实现见 app/api/）
# ============================================================
from app.api import register_routes  # noqa: E402

register_routes(app)


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
