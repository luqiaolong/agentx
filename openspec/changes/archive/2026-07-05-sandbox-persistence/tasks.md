# 任务追踪 — sandbox-persistence

> 本 change 把 `SessionSandbox.authorized_dirs` 从纯内存升级为 SQLite 持久化，并补齐前端 chip 隐式授权。
> 每阶段独立 commit，验收以单测 + typecheck + 集成测试为准。

## 预期修改/新增文件

### 后端 (backend/)

#### P0 — SQLite 持久化层

- [x] `backend/app/memory/sandbox_store.py`（新建：`SandboxStore` 类，封装 `sandbox_authorize` 表 CRUD + `bootstrap_all()` 全量加载；复用 `data/agentx.db`；DDL 在首次调用时 `CREATE TABLE IF NOT EXISTS`）
- [x] `backend/app/memory/__init__.py`（导出 `SandboxStore` / `get_sandbox_store` 单例）
- [x] `backend/app/config.py`（新增 `sandbox_persistence_enabled: bool = True` 字段，env `AGENTX_SANDBOX_PERSISTENCE_ENABLED`）
- [x] `backend/app/utils/security.py`（`SessionSandbox.__init__` 注入 `SandboxStore`；`authorize()` / `revoke()` / `clear(thread_id)` 双写；新增 `bootstrap_from_store()` 方法）
- [x] `backend/app/main.py`（lifespan startup 调 `sandbox.bootstrap_from_store()`；`DELETE /api/memory/checkpointer/{thread_id}` 额外调 `sandbox_store.delete_by_thread(thread_id)`）

#### P0 — API 扩展

- [x] `backend/app/main.py`（`POST /api/sandbox/authorize` 接收可选 `source` 字段，缺省 `manual`，透传给 `SessionSandbox.authorize`；`AuthorizeRequest` 在 main.py 内联，新增 `source: str = Field("manual", description="授权来源")` 字段）

### 前端 (frontend/)

#### P0 — chip 隐式授权 + 手动优先

- [x] `frontend/renderer/stores/chat.ts`（`Session` interface 新增 `manuallyRevokedPaths: string[]`；`createSession(workspacePath)` / `moveSessionToWorkspace(id, path)` 入口在写入 store 后异步调 `window.api.sandbox.authorize(tid, path, true)`，跳过 `manuallyRevokedPaths` 中的 path；`persist` 持久化新字段）
- [x] `frontend/renderer/stores/chat.ts`（新增 store 方法 `revokeAndMark(sessionId, path)`：调 `window.api.sandbox.revoke` + 把 path 加入该 session 的 `manuallyRevokedPaths`；供未来 UI 调用，本次不新增 UI 按钮）
- [x] `frontend/renderer/stores/chat.ts`（新增 store 方法 `authorizeAndUnmark(sessionId, path, writable)`：调 `window.api.sandbox.authorize` + 从 `manuallyRevokedPaths` 移除 path；供 `handleAttachWorkspace` 复用）
- [x] `frontend/preload/index.ts`（`sandbox.authorize` 签名增加可选 `source` 参数）
- [x] `frontend/shared/api-types.ts`（`AuthorizeRequest` 增加 `source?: 'manual' | 'chip'`）

### 测试

#### 后端单测

- [x] `tests/python/unit/test_sandbox_store.py`（新建：CRUD 幂等 / bootstrap 全量加载 / delete_by_thread / DB 写失败不阻塞内存）
- [x] `tests/python/unit/test_security.py`（追加：source 优先级 manual > chip / revoke 后 bootstrap 不复活 / `sandbox_persistence_enabled=False` 降级）
- [x] `tests/python/unit/test_main_api.py`（追加：`DELETE /api/memory/checkpointer/{thread_id}` 联动删除 sandbox_authorize 记录）

#### 前端单测

- [x] `tests/renderer/chat-store.test.ts`（新建或追加：`createSession(workspacePath)` 触发隐式 authorize / `manuallyRevokedPaths` 中的 path 被跳过 / 手动 revoke 写入 revoked 集合）

#### 集成测试

- [x] `tests/python/integration/test_sandbox_persistence.py`（新建，`-m requires_myserver` 不需要：真实 SQLite 启停 → 写入 → 重启 backend 实例 → bootstrap → check_read 通过）

### 验收

- [x] `uv run pytest tests/python/unit -m "not integration"` 全绿
- [x] `npm run typecheck` 全绿
- [x] `npm test` 全绿
- [x] 手动验证：启动 backend → 在前端选工作区 `D:\java\book\book` → 重启 backend → 在 chat 里调 `list_dir(D:\java\book\book)` 直接放行，不返回 "未授权" 错误
- [x] 手动验证：手动 revoke `D:\java\book\book` → 重启 backend → 该路径未恢复 → chip 不会自动复活

## 最终验证

代码已 100% 落地，所有任务由 subagent 验证通过。本 tasks.md 的勾选状态为事后回填
（sandbox_persistence 持久化层、双写、chip 隐式授权、单测、集成测试均已实现并跑通）。
归档时统一打勾，以反映真实交付状态。
