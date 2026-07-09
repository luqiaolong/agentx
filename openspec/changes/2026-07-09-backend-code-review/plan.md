# 后端代码审查修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 2026-07-09 后端 Python 代码审查中发现的 P0/P1/P2 级缺陷与质量债，确保事件循环边界、安全策略、SSE 事件契约、LangGraph 架构规范与测试覆盖率达到项目基线。

**Architecture:** FastAPI + LangGraph + LangChain + DeepAgents + SQLite/Milvus/TEI

---

## 文件结构变更概览

- **修改**：`backend/app/memory/checkpointer.py` —  lifespan 中改用 `aclose_checkpointer` 关闭异步连接。
- **修改**：`backend/app/main.py` — 将 `close_checkpointer()` 改为 `await aclose_checkpointer()`。
- **修改**：`backend/app/embedding/tei_client.py`、`backend/app/vectorstore/milvus_client.py` — 移除/防护在运行事件循环中调用 `asyncio.run` 的清理路径。
- **修改**：`backend/app/cli/app.py`、`backend/app/eval/cli.py`、`backend/app/eval/pytest_plugin.py` — 确保 CLI/eval 入口的 `asyncio.run` 仅在真正同步顶层调用。
- **修改**：`backend/app/main.py` — `UTF8JSONBodyMiddleware` 不再直接写 `request._body` 私有属性，改为包装新 `Request`。
- **修改**：`backend/app/config/agents.py` — 将 `_DEFAULT_SUPERVISOR_TOOLS` 中的 `cli_execute` 改为 `execute`。
- **修改**：`backend/app/team/orchestrator.py` — 用 LangGraph `Send` / 并行分支替代 `_execute_node` 内的 `asyncio.Queue` + `Semaphore`。
- **修改**：`backend/app/security/command_filter.py` — 将换行符 `\n\r` 纳入 `FORBIDDEN_ARG_PATTERN`。
- **修改**：`backend/app/sandbox/session_sandbox.py` — 为 `is_path_authorized` 异常吞咽路径增加日志。
- **修改**：`backend/app/utils/sse_events.py` 及所有 yield error 路径 — 统一使用 `make_sse_event("error", {...})` 输出结构化 JSON。
- **修改**：`backend/app/agents/supervisor/work_supervisor.py` — `@coding` / `@rag` 等 mention 委派事件的 `source` 与目标 agent 一致。
- **修改**：`backend/app/security/approval_flow.py`、`backend/app/security/approval/state.py` — 修复 pause/resume 与 reaper 的竞态。
- **修改**：`backend/app/memory/checkpointer.py` — 为同步 + 异步连接显式设置 `PRAGMA journal_mode=WAL`。
- **新增**：`backend/app/utils/tasks.py` — 提供 `fire_and_forget(coro)` 统一封装，替换散落各地的 `asyncio.create_task` + `set` + `add_done_callback` 模式。
- **新增/修改**：`tests/python/unit/test_checkpointer_shutdown.py`、`test_utf8_middleware.py`、`test_security_command_filter.py`、`test_security_state.py` — 补充回归测试。
- **移动**：`tests/python/manual_*.py`、`tests/python/diag_*.py` — 移入 `tests/python/manual/` 或 `scripts/`。
- **修改**：`AGENTS.md` — 修正/补充引用路径与事件契约说明。

---

## Phase 1: P0 关键缺陷

### Task 1: Checkpointer 异步连接在 lifespan 中泄漏（P0-1）

**Files:**
- Modify: `backend/app/memory/checkpointer.py:83-112`
- Modify: `backend/app/main.py:185-189`

**问题:** `close_checkpointer()` 在 `main.py` lifespan 的 `finally` 中调用，内部使用 `asyncio.run(_async_conn.close())`。由于 FastAPI 正在运行事件循环，此调用恒抛 `RuntimeError` 并被吞掉，导致 `AsyncSqliteSaver` 的 aiosqlite 连接在每次干净关闭时泄漏。

- [ ] **Step 1: lifespan 中直接使用异步关闭**

```python
# backend/app/main.py:185-189
        try:
            await aclose_checkpointer()
            logger.info("checkpointer closed on shutdown")
        except Exception as exc:  # noqa: BLE001
            logger.warning("checkpointer close failed on shutdown: {}", exc)
```

- [ ] **Step 2: 保留 `close_checkpointer()` 作为纯同步入口（CLI/脚本）**

在 `checkpointer.py` 中保留 `close_checkpointer()` 用于非异步上下文，但文档中明确声明它只能在**无运行事件循环**时调用；若检测到正在运行的事件循环，直接委托给 `aclose_checkpointer()` 的异步版本或抛出明确警告。

```python
def close_checkpointer() -> None:
    """关闭 checkpointer（仅在无运行事件循环的同步上下文中使用）。

    若从异步上下文中调用，请改用 ``aclose_checkpointer()``。
    """
    global _sync_saver, _sync_conn, _async_saver, _async_conn
    # ... 关闭 sync ...
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _async_conn is not None:
        if loop is not None:
            logger.warning(
                "close_checkpointer called inside a running event loop; "
                "use aclose_checkpointer() to close the async connection properly."
            )
        else:
            try:
                asyncio.run(_async_conn.close())
                logger.info("SQLite 异步 checkpointer 已关闭")
            except Exception as exc:  # noqa: BLE001
                logger.warning("关闭异步 checkpointer 失败", error=str(exc))
        _async_conn = None
        _async_saver = None
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/python/unit/test_memory_checkpointer.py -v
```
Expected: PASS

- [ ] **Step 4: 新增回归测试**

创建 `tests/python/unit/test_checkpointer_shutdown.py`：

```python
import asyncio
import pytest
from app.memory.checkpointer import get_async_checkpointer, aclose_checkpointer, close_checkpointer


@pytest.mark.asyncio
async def test_aclose_checkpointer_closes_async_connection():
    saver = await get_async_checkpointer()
    assert saver is not None
    await aclose_checkpointer()
    # 再次获取应创建新连接
    saver2 = await get_async_checkpointer()
    assert saver2 is not saver
    await aclose_checkpointer()


def test_close_checkpointer_warns_inside_loop():
    async def _inner():
        await get_async_checkpointer()
        close_checkpointer()

    asyncio.run(_inner())
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/memory/checkpointer.py backend/app/main.py tests/python/unit/test_checkpointer_shutdown.py
git commit -m "fix: close async checkpointer via aclose_checkpointer in lifespan"
```

---

### Task 2: 清理路径中的 `asyncio.run` 边界错误（P0-2）

**Files:**
- Modify: `backend/app/embedding/tei_client.py`
- Modify: `backend/app/vectorstore/milvus_client.py`
- Modify: `backend/app/cli/app.py:336,347,358`
- Modify: `backend/app/eval/cli.py:141`
- Modify: `backend/app/eval/pytest_plugin.py:121`

**问题:** 多个模块在同步函数中调用 `asyncio.run(...)` 来关闭异步客户端，当这些函数被异步测试或被 lifespan 调用时同样会触发 `RuntimeError`。`cli/app.py` 和 `eval/cli.py` 虽在 CLI 顶层，但 `pytest_plugin.py` 的 fixture 在 pytest-asyncio 的 loop 中运行，会直接报错。

- [ ] **Step 1: 为 `tei_client` / `milvus_client` 提供 `aclose()` 与 `close()` 双入口**

`close()` 检测是否在运行事件循环中：
- 无 loop：使用 `asyncio.run(aclose())` 安全关闭。
- 有 loop：记录 warning，提示调用方应使用 `aclose()`，并清理引用。

```python
def close(self) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(self.aclose())
        return
    logger.warning(
        "%s.close() called inside a running event loop; "
        "use aclose() instead. Connection may leak.",
        self.__class__.__name__,
    )
```

- [ ] **Step 2: lifespan 中调用异步 `aclose()`**

在 `main.py` lifespan 中把 `await get_embedding_client().aclose()` / `await milvus.disconnect()` 的顺序保持正确，并确认 `milvus.disconnect()` 是 `async`。

- [ ] **Step 3: 修正 `eval/pytest_plugin.py`**

将 fixture 中的 `asyncio.run(_run())` 改为 `await _run()`，并确保该 fixture 是 `async` fixture。

```python
@pytest.fixture
def eval_case_runner(...) -> ...:
    async def _run(...):
        ...
    return _run
```

- [ ] **Step 4: 运行测试**

```bash
uv run pytest tests/python/unit/test_tei_client.py tests/python/unit/test_milvus_client.py tests/python/unit/test_eval_pytest_plugin.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/embedding/tei_client.py backend/app/vectorstore/milvus_client.py backend/app/eval/pytest_plugin.py
git commit -m "fix: avoid asyncio.run inside running event loop for client cleanup"
```

---

### Task 3: 替换 `UTF8JSONBodyMiddleware` 对 `request._body` 私有属性的写入（P0-3）

**Files:**
- Modify: `backend/app/main.py:236-259`
- Create: `tests/python/unit/test_utf8_middleware.py`

**问题:** 中间件直接设置 `request._body = ...`。Starlette 的 `body()` 属性依赖内部状态 `_body_consumed` 与 `_body`。私有属性随时可能因 Starlette 版本升级而变更语义，导致后续中间件再次读取 `request.body()` 时出错或忽略改写后的内容。

- [ ] **Step 1: 用自定义 `receive` 包装新 Request**

```python
from starlette.requests import Request

async def dispatch(self, request: Request, call_next):
    content_type = (request.headers.get("content-type") or "").lower()
    if not content_type.startswith("application/json"):
        return await call_next(request)

    raw = await request.body()
    if not raw:
        return await call_next(request)

    try:
        text = raw.decode("utf-8")
        body_bytes = raw
    except UnicodeDecodeError:
        try:
            text = raw.decode("gbk")
            body_bytes = text.encode("utf-8")
        except UnicodeDecodeError:
            from starlette.responses import JSONResponse
            return JSONResponse(
                {"detail": "request body is not valid UTF-8 or GBK"},
                status_code=400,
            )

    async def receive() -> dict:
        return {"type": "http.request", "body": body_bytes}

    new_request = Request(request.scope, receive=receive)
    return await call_next(new_request)
```

- [ ] **Step 2: 添加回归测试**

`tests/python/unit/test_utf8_middleware.py`：

```python
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

async def app(scope, receive, send):
    request = Request(scope, receive)
    body = await request.json()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": body["key"].encode("utf-8")})


def test_utf8_middleware_decodes_gbk_request():
    # 需把中间件挂到 app 上，测试 GBK 编码的 JSON 能被正确解码为 UTF-8
    ...
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/python/unit/test_utf8_middleware.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py tests/python/unit/test_utf8_middleware.py
git commit -m "fix: avoid mutating request._body in UTF8JSONBodyMiddleware"
```

---

### Task 4: 修正 Supervisor 默认工具名 `cli_execute` → `execute`（P0-4）

**Files:**
- Modify: `backend/app/config/agents.py:38-45`

**问题:** `dangerous_tools.py` 和 `safe_shell_backend.py` 已把 CLI 工具名统一为 `execute`（deepagents `LocalShellBackend` 内置），但 `_DEFAULT_SUPERVISOR_TOOLS` 仍包含旧名 `cli_execute`。Supervisor 在工具名不一致时无法调用 shell，或出现“已批准但工具不存在”的 footgun。

- [ ] **Step 1: 替换工具名**

```python
_DEFAULT_SUPERVISOR_TOOLS: list[str] = [
    "read_file", "list_dir", "glob", "grep",
    "write_file", "edit_file",
    "web_search", "rag_retrieve",
    "git_status", "git_diff", "git_log", "git_branches",
    "git_clone", "git_pull", "git_checkout", "git_stage", "git_commit",
    "execute",  # 修正：原 cli_execute 已废弃
]
```

- [ ] **Step 2: 运行相关测试**

```bash
uv run pytest tests/python/unit/test_agents_config.py tests/python/unit/test_security_command_filter.py -v
```
Expected: PASS

- [ ] **Step 3: 新增工具名一致性断言**

在 `tests/python/unit/test_agents_config.py` 中增加：

```python
def test_supervisor_default_tools_use_current_shell_name():
    from app.config.agents import _DEFAULT_SUPERVISOR_TOOLS
    from app.security.dangerous_tools import DANGEROUS_TOOLS
    assert "execute" in _DEFAULT_SUPERVISOR_TOOLS
    assert "cli_execute" not in _DEFAULT_SUPERVISOR_TOOLS
    assert (DANGEROUS_TOOLS & set(_DEFAULT_SUPERVISOR_TOOLS)) == {"execute", "write_file", "edit_file", "git_clone", "git_pull", "git_checkout", "git_stage", "git_commit"}
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/config/agents.py tests/python/unit/test_agents_config.py
git commit -m "fix: rename cli_execute to execute in default supervisor tools"
```

---

### Task 5: Team Orchestrator 并行执行改为 LangGraph 原生机制（P0-5）

**Files:**
- Modify: `backend/app/team/orchestrator.py`
- Modify: `backend/app/team/scheduler.py`
- Create: `tests/python/unit/test_team_langgraph_parallel.py`

**问题:** `_execute_node` 内使用 `asyncio.Queue` + `asyncio.Semaphore` 手工管理并发子任务，违反了 AGENTS.md §3 R1/R13（避免手写并行编排）。该实现还无法被 LangGraph 检查点恢复、可视化与调试。

- [ ] **Step 1: 设计并行图结构**

将每个子任务拆分为独立的动态节点，通过 `langgraph.types.Send` 从 `_plan_node` 发送给 `subtask_node`，并返回 `Command` 继续。使用 `StateGraph` 的 `add_conditional_edges` + `Send` API 实现 Map-Reduce 风格。

```python
from langgraph.types import Send

graph.add_conditional_edges(
    "plan",
    _dispatch_subtasks,
    ["subtask"],
)
graph.add_node("subtask", _subtask_node)
graph.add_conditional_edges(
    "subtask",
    _route_after_subtask,
    {"aggregate": "aggregate", "subtask": "subtask"},
)
graph.add_node("aggregate", _aggregate_node)
graph.add_edge("aggregate", END)
```

- [ ] **Step 2: 提取 `_subtask_node` 并保留事件透传**

`_run_subtask` 返回的 `AsyncIterator` 在节点内部通过 `get_stream_writer()` 实时发出事件；`approval_request` 等 passthrough 事件仍然直接透传，避免审批死锁。

```python
async def _subtask_node(state: TeamState) -> dict:
    writer = get_stream_writer()
    task = state["current_task"]
    async for ev in _run_subtask(...):
        writer(ev)
    return {"subtask_results": [result]}
```

- [ ] **Step 3: 确保 abort 透传**

在 `subtask_node` 每轮迭代前检查 `get_abort_event(thread_id).is_set()`，若已中止则 yield 错误事件并返回空结果。

- [ ] **Step 4: 运行测试**

```bash
uv run pytest tests/python/unit/test_team_path.py tests/python/unit/test_team_langgraph_parallel.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/team/orchestrator.py backend/app/team/scheduler.py tests/python/unit/test_team_langgraph_parallel.py
git commit -m "refactor: use LangGraph Send for team subtask parallelism"
```

---

## Phase 2: P1 安全与稳定性

### Task 6: 强化 Shell 命令元字符过滤，防御换行绕过（P1-1）

**Files:**
- Modify: `backend/app/security/command_filter.py:57`
- Modify: `tests/python/unit/test_security_command_filter.py`

**问题:** `FORBIDDEN_ARG_PATTERN = re.compile(r"[;&|`$<>]")` 未包含 `\n` / `\r`。多行命令字符串可直接绕过，配合 `shell=True` 的 `SafeLocalShellBackend.execute` 执行任意多行 bash 脚本。

- [ ] **Step 1: 扩展正则**

```python
FORBIDDEN_ARG_PATTERN: re.Pattern[str] = re.compile(r"[;&|`$<>\r\n]")
```

- [ ] **Step 2: 增加测试用例**

```python
@pytest.mark.parametrize("payload", ["echo 1\necho 2", "echo 1\r\necho 2", "echo $'\n'"])
def test_has_forbidden_args_blocks_newline(payload: str):
    from app.security.command_filter import has_forbidden_args
    assert has_forbidden_args(payload) is True
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/python/unit/test_security_command_filter.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/security/command_filter.py tests/python/unit/test_security_command_filter.py
git commit -m "fix: block newline shell metacharacters in command filter"
```

---

### Task 7: 为沙箱路径授权失败增加可观测日志（P1-2）

**Files:**
- Modify: `backend/app/sandbox/session_sandbox.py:380-383`

**问题:** `is_path_authorized` 对 `normalize_path` 的任意异常静默返回 `False`，攻击者探测路径解析漏洞时无审计记录。

- [ ] **Step 1: 添加 warning 日志**

```python
try:
    resolved = normalize_path(path, base=base)
except Exception as exc:  # noqa: BLE001
    logger.warning(
        "path authorization check failed",
        path=str(path),
        base=str(base),
        error=str(exc),
    )
    return False
```

- [ ] **Step 2: 运行测试**

```bash
uv run pytest tests/python/unit/test_sandbox_session.py -v
```
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/sandbox/session_sandbox.py
git commit -m "chore: add observability for path authorization failures"
```

---

### Task 8: 统一 error 事件为结构化 JSON（P1-3）

**Files:**
- Modify: `backend/app/deep/agent.py:154-160`
- Modify: `backend/app/deep/execution.py:160,180,215-218,247-249,322,348,366`
- Modify: `backend/app/agents/supervisor/work_supervisor.py:405,409`
- Modify: `backend/app/team/orchestrator.py:153,167,172,388-389` 等
- Modify: `backend/app/utils/sse_events.py`（如需要统一 helper）

**问题:** 多处 `yield make_sse_event("error", f"...")` 输出纯字符串，但 SSE 契约 §13 要求 `error` 事件为结构化 JSON（至少 `{message}`）。前端解析可能不一致。

- [ ] **Step 1: 定义 error 结构**

```python
# backend/app/utils/sse_events.py
def make_error_event(message: str, code: str | None = None) -> dict[str, str]:
    payload: dict[str, str] = {"message": message}
    if code:
        payload["code"] = code
    return make_sse_event("error", payload)
```

- [ ] **Step 2: 全量替换所有 error 事件的字符串 payload**

使用 Grep 找到所有 `make_sse_event("error"` 和 `{"event": "error"` 调用点，替换为 `make_error_event(...)`。如果 `make_sse_event` 已在内部将非 dict 包装，则确认包装方式并在调用处统一传 dict。

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/python/unit/test_sse_contract.py tests/python/unit/test_chat_endpoint.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/utils/sse_events.py backend/app/deep/agent.py backend/app/deep/execution.py backend/app/agents/supervisor/work_supervisor.py backend/app/team/orchestrator.py ...
git commit -m "fix: emit structured JSON for all error SSE events"
```

---

### Task 9: Mention 委派事件 source 与目标 agent 对齐（P1-4）

**Files:**
- Modify: `backend/app/agents/supervisor/work_supervisor.py:319-323`（Expert @mention）
- Modify: `backend/app/agents/supervisor/work_supervisor.py:349-353`（subagent @mention）

**问题:** `delegation` 事件当前硬编码 `source: "work"`，对 `@coding` 派发到 Coding Expert 的情况应标记为 `source: "coding"`；对 `@rag` / `@web` 应标记为对应子代理 source。

- [ ] **Step 1: 按目标设置 source**

```python
# Expert @coding
yield make_sse_event(
    "delegation",
    {"target": "coding", "source": "coding", "message": task},
)
# Subagent @rag / @web
yield make_sse_event(
    "delegation",
    {"target": agent_name, "source": agent_name, "message": task},
)
```

- [ ] **Step 2: 运行测试**

```bash
uv run pytest tests/python/unit/test_supervisor.py tests/python/unit/test_sse_contract.py -v
```
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/agents/supervisor/work_supervisor.py
git commit -m "fix: set delegation source to target agent name"
```

---

### Task 10: 修复 pause/resume 与 reaper 的竞态（P1-5）

**Files:**
- Modify: `backend/app/security/approval/state.py:43-55`
- Modify: `backend/app/deep/execution.py:170-176`
- Create: `tests/python/unit/test_security_state.py`

**问题:** `_pause_events` 可能在 `wait_for_resume` 期间被 reaper 清理，导致恢复时事件对象已丢失、`await event.wait()` 永久挂起。

- [ ] **Step 1: 在 `wait_for_resume` 中允许事件重建**

```python
async def wait_for_resume(thread_id: str, timeout: float | None = None) -> bool:
    deadline = None if timeout is None else asyncio.get_event_loop().time() + timeout
    while True:
        event = _get_or_create_event(_pause_events, thread_id)
        if not is_paused(thread_id):
            return True
        wait_time = None if deadline is None else max(0, deadline - asyncio.get_event_loop().time())
        try:
            await asyncio.wait_for(event.wait(), timeout=wait_time)
        except asyncio.TimeoutError:
            return False
        if not is_paused(thread_id):
            return True
        # 若仍 pause 且事件被重建，则继续循环
```

- [ ] **Step 2: 为 reaper 加锁保护事件清理**

reaper 清理 `_pause_events` 时先持有 `_locks[thread_id]`，确保不会与 `wait_for_resume` 的 `_get_or_create_event` 竞争。

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/python/unit/test_security_state.py tests/python/unit/test_interrupt_resume.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/security/approval/state.py tests/python/unit/test_security_state.py
git commit -m "fix: recreate pause event if reaper evicts it during wait_for_resume"
```

---

### Task 11: 为 Checkpointer 同步+异步连接设置 WAL（P1-6）

**Files:**
- Modify: `backend/app/memory/checkpointer.py:44-60` 与 `64-80`

**问题:** 同步与异步连接共享同一 SQLite 文件，但未显式启用 WAL，可能导致并发写时 `database is locked`。

- [ ] **Step 1: 同步连接启用 WAL**

```python
_sync_conn = sqlite3.connect(str(db_path), check_same_thread=False)
_sync_conn.execute("PRAGMA journal_mode=WAL")
_sync_conn.execute("PRAGMA busy_timeout=30000")
```

- [ ] **Step 2: 异步连接启用 WAL**

```python
_async_conn = await aiosqlite.connect(str(db_path))
await _async_conn.execute("PRAGMA journal_mode=WAL")
await _async_conn.execute("PRAGMA busy_timeout=30000")
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/python/unit/test_memory_checkpointer.py tests/python/integration/test_checkpointer_writeback_integration.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/memory/checkpointer.py
git commit -m "fix: enable WAL and busy_timeout on checkpointer SQLite connections"
```

---

## Phase 3: P2 可维护性与测试

### Task 12: 集中 `_workspace_prompt_suffix` 重复代码（P2-1）

**Files:**
- Modify: `backend/app/utils/prompts.py`
- Modify: `backend/app/agents/supervisor/work_supervisor.py:54-63`
- Modify: `backend/app/deep/agent.py:56-65`
- Modify: `backend/app/agents/expert/coding.py`（如有重复）

**问题:** 同样的 9 行 workspace 提示后缀函数在 work_supervisor.py、deep/agent.py 中重复定义，漂移风险高。

- [ ] **Step 1: 在 `app/utils/prompts.py` 中提供统一函数**

```python
def build_workspace_prompt_suffix(workspace_path: str | None) -> str:
    if not workspace_path:
        return ""
    return (
        f"\n\n当前 workspace: {workspace_path}\n"
        "对该路径下的文件操作需已被用户授权；"
        "若涉及越界读写，会触发审批请求。"
    )
```

- [ ] **Step 2: 替换各 agent 中的重复实现**

删除各自模块中的 `_workspace_prompt_suffix`，统一改为 `from app.utils.prompts import build_workspace_prompt_suffix`。

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/python/unit/test_supervisor.py tests/python/unit/test_custom_agent_deep.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/utils/prompts.py backend/app/agents/supervisor/work_supervisor.py backend/app/deep/agent.py backend/app/agents/expert/coding.py
git commit -m "refactor: centralize workspace prompt suffix helper"
```

---

### Task 13: 新增并发 + reaper 竞态测试（P2-2）

**Files:**
- Create: `tests/python/unit/test_checkpointer_concurrency.py`
- Create/Modify: `tests/python/unit/test_security_state.py`

**问题:** 缺少同步/异步连接并发写检查、以及 pause 事件被 reaper 清理的回归测试。

- [ ] **Step 1: 并发写测试**

```python
@pytest.mark.asyncio
async def test_concurrent_sync_and_async_checkpointer_writes():
    sync_saver = get_checkpointer()
    async_saver = await get_async_checkpointer()
    # 并发写入同一 thread_id，验证不抛 database is locked
    ...
```

- [ ] **Step 2: reaper 竞态测试**

见 Task 10 Step 3。

- [ ] **Step 3: Commit**

```bash
git add tests/python/unit/test_checkpointer_concurrency.py tests/python/unit/test_security_state.py
git commit -m "test: add checkpointer concurrency and reaper race tests"
```

---

### Task 14: 为 UTF-8 中间件与错误事件结构新增测试（P2-3）

**Files:**
- Create: `tests/python/unit/test_utf8_middleware.py`
- Modify: `tests/python/unit/test_sse_contract.py`

**问题:** 缺少 UTF-8/GBK 回退路径的回归测试；SSE 错误事件结构未显式断言。

- [ ] **Step 1: UTF-8 middleware 测试**

见 Task 3 Step 2。

- [ ] **Step 2: SSE 错误事件结构断言**

```python
def test_error_event_is_structured_json():
    from app.utils.sse_events import make_error_event
    ev = make_error_event("something went wrong", code="E123")
    assert ev["event"] == "error"
    data = json.loads(ev["data"])
    assert data["message"] == "something went wrong"
    assert data["code"] == "E123"
```

- [ ] **Step 3: Commit**

```bash
git add tests/python/unit/test_utf8_middleware.py tests/python/unit/test_sse_contract.py
git commit -m "test: cover UTF-8 middleware and structured error events"
```

---

### Task 15: 清理测试目录中的手动/诊断脚本（P2-4）

**Files:**
- Move: `tests/python/manual_approval.py`、`manual_listdir.py`、`manual_noauto.py`、`manual_smoke.py`
- Move: `tests/python/diag_agent_direct.py`、`diag_build_run.py`、`diag_deep_path.py`、`diag_exact_match.py`、`diag_full_flow.py`、`diag_full_run.py`

**问题:** 这些脚本不是自动测试，放在 `tests/python/` 根目录会干扰测试发现，并可能误导新开发者。

- [ ] **Step 1: 移动到 `tests/python/manual/`**

```bash
mkdir -p tests/python/manual tests/python/diag
git mv tests/python/manual_*.py tests/python/manual/
git mv tests/python/diag_*.py tests/python/diag/
```

- [ ] **Step 2: 确保 pytest 不会收集这些文件**

在 `tests/python/manual/` 和 `tests/python/diag/` 添加 `__init__.py`（空文件）即可被识别为包，或在 `pytest.ini` / `pyproject.toml` 中配置 `norecursedirs = manual diag`。

- [ ] **Step 3: Commit**

```bash
git add tests/python/manual tests/python/diag pyproject.toml
# 或仅添加 __init__.py
git commit -m "chore: move manual/diag scripts out of pytest discovery path"
```

---

### Task 16: 移除 streaming.py 中的无意义 wrapper（P2-5）

**Files:**
- Modify: `backend/app/deep/streaming.py:38-46`
- Modify: `backend/app/utils/plan_extraction.py`

**问题:** `_extract_plan_or_update` 函数只是 inline import 后转发，没有增加任何行为，且每次调用都重新 import。

- [ ] **Step 1: 直接导入**

```python
from app.utils.plan_extraction import extract_plan_or_update
```

删除 `_extract_plan_or_update` wrapper 函数，调用处直接使用 `extract_plan_or_update`。

- [ ] **Step 2: 运行测试**

```bash
uv run pytest tests/python/unit/test_plan_extraction.py tests/python/unit/test_deep_planning.py -v
```
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/deep/streaming.py
git commit -m "refactor: remove redundant extract_plan_or_update wrapper"
```

---

## Phase 4: 文档同步

### Task 17: 更新 AGENTS.md 路径与事件契约（P2-6）

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: 检查 §11 文件地图中的过时路径**

确认 `backend/app/paths/` 已删除；`app/api/chat.py` 是 SSE 入口；`app/subagents/dispatch.py` 是否存在（如不存在则从文件地图中移除）。

- [ ] **Step 2: 补充 error 事件结构化说明**

在 §13 表格中，`error` 事件的数据类型明确为 JSON `{"message": str, "code?: str}`。

- [ ] **Step 3: 补充 `pause` / `resume` 端点路径**

确认 §13 已列出 `/api/chat/pause` 与 `/api/chat/resume`（如没有则补充）。

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs: sync AGENTS.md with backend paths and SSE error contract"
```

---

## 自评

**1. 覆盖范围：**
- P0-1 事件循环边界泄漏 → Task 1、Task 2
- P0-2 `request._body` 私有属性 → Task 3
- P0-3 `cli_execute` 名称漂移 → Task 4
- P0-4 Team Orchestrator 手写并行 → Task 5
- P1-1 Shell 换行绕过 → Task 6
- P1-2 路径授权失败无日志 → Task 7
- P1-3 error 事件结构不一致 → Task 8
- P1-4 delegation source 错误 → Task 9
- P1-5 pause/resume reaper 竞态 → Task 10
- P1-6 SQLite WAL 缺失 → Task 11
- P2-1 重复 workspace prompt 后缀 → Task 12
- P2-2 并发/reaper 测试缺口 → Task 13
- P2-3 UTF-8/error 结构测试缺口 → Task 14
- P2-4 manual/diag 脚本污染 → Task 15
- P2-5 无意义 wrapper → Task 16
- P2-6 文档同步 → Task 17

**2. 依赖关系：**
- Task 2 依赖 Task 1 的 `aclose_checkpointer` 模式思路，但可并行。
- Task 5 较大，建议与 Task 10 在代码合并时协调 abort 事件接口。
- Task 8 为全文件搜索替换，需在其他提交后 rebase 或最后执行。

**3. 验收标准：**
- `uv run pytest tests/python/unit -m "not integration"` 全绿。
- `uv run ruff check backend/` 无新增风格错误。
- `uv run pytest tests/python/integration -m requires_myserver` 在 myserver 可达时全绿（仅涉及修改到的模块）。

---

## 执行方式

**Plan complete.**

Two execution options:
1. **Subagent-Driven (recommended)** — 按 Phase 分组，每个 Task 独立交付并 review。
2. **Inline Execution** — 在当前会话中按任务顺序直接修改，建议每完成一个 Task 运行一次测试并提交。