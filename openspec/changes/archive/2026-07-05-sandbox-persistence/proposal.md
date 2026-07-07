## Why

当前 `SessionSandbox.authorized_dirs` 是**纯内存 dict**（[security.py:72](file:///d:/java/agentprojects/agentx/backend/app/utils/security.py)），未接任何持久化层，导致三类用户可感知的故障：

1. **后端重启授权丢失** — Electron 退出后下次启动，所有 thread 的授权目录清空。用户在前端工作区 chip 上明明挂着 `D:\java\book\book`，后端 `check_read` 仍抛 `PathNotAuthorized`，工具调用返回 `"路径 ... 未授权，请通过 dialog 选择目录后重试"`。
2. **会话切换失去授权** — 同一进程内，如果 `SessionSandbox` 因为某种原因被重建（目前是单例不会，但代码没硬约束），授权也会丢。
3. **工作区 chip 与后端授权状态不同步** — 前端 `workspacePath` 仅是 zustand 元数据（[chat.ts:63](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/chat.ts)），后端完全不感知；用户「选过工作区」≠「后端已授权」，二者必须靠 `POST /api/sandbox/authorize` 显式同步一次。

现有 [filesystem-sandbox spec](file:///d:/java/agentprojects/agentx/openspec/changes/archive/2026-07-03-adjust-m1-stack-and-structure/specs/filesystem-sandbox/spec.md) 已经在 §"授权持久化与恢复" 写明「系统 SHALL 将授权目录列表写入 LangGraph `RouterState.authorized_dirs`，会话重开时从 checkpoint 恢复」——**但代码没落地**，`RouterState` 根本没 `authorized_dirs` 字段。本 change 把这条 spec 真正实现，并把"工作区 chip 自动授权"补齐，让"用户选过工作区"语义直接等于"后端已放行"。

## What Changes

### P0 — 后端独立 SQLite 表 + 启动加载

- **NEW** `backend/app/memory/sandbox_store.py`：封装 `sandbox_authorize` 表 CRUD。表结构 `(thread_id, resolved_path, writable, source, created_at)`，主键 `(thread_id, resolved_path)`，索引 `idx_sandbox_thread(thread_id)`。复用 `data/agentx.db`（与 LangGraph checkpoint 同一文件）。
- **MODIFY** `backend/app/utils/security.py`：`SessionSandbox` 构造注入 `SandboxStore`；`authorize()` / `revoke()` / `clear(thread_id)` 三个写操作**双写**（内存 + DB）；新增 `bootstrap_from_store()` 启动加载方法。
- **MODIFY** `backend/app/main.py`：lifespan 启动阶段调 `sandbox.bootstrap_from_store()`；`DELETE /api/memory/checkpointer/{thread_id}` 端点**额外**调 `sandbox_store.delete_by_thread(thread_id)`，保持「删 thread = 删授权」语义。
- **MODIFY** `backend/app/memory/__init__.py`：导出 `SandboxStore` 与 `get_sandbox_store`。

### P0 — 前端 chip 隐式授权 + 手动优先

- **MODIFY** `frontend/renderer/stores/chat.ts`：`Session` interface 新增 `manuallyRevokedPaths: string[]`（持久化到 localStorage）；`createSession(workspacePath)` / `moveSessionToWorkspace(id, path)` 两个入口在写入 store 后**自动**调 `window.api.sandbox.authorize(tid, path, true)`，**除非** path 在该 session 的 `manuallyRevokedPaths` 中。
- **MODIFY** `frontend/renderer/stores/chat.ts`：新增 `revokeAndMark(sessionId, path)` / `authorizeAndUnmark(sessionId, path, writable)` 两个 store 方法（供未来 UI 调用，本次不新增 UI 按钮）；`handleAttachWorkspace` 复用 `authorizeAndUnmark`，保持「手动授权清除 revoked 标记」语义。
- **MODIFY** `frontend/renderer/components/chat/ChatComposer.tsx`：`handleAttachWorkspace` 改调 `authorizeAndUnmark`（行为等价，但同步清除 revoked 标记）；`handleRemoveWorkspace` 保持现状（仅迁回 Home，不 revoke）。**本次不新增「彻底撤销授权」UI 按钮**——留给下一 change。

### P1 — 配置与观测

- **NEW** `backend/app/config.py`：`sandbox_persistence_enabled: bool = True`（env `AGENTX_SANDBOX_PERSISTENCE_ENABLED`），关闭时所有双写降级为内存-only（兼容现有行为，便于故障注入测试）。
- **MODIFY** `backend/app/observability/logger.py`：双写失败时 `logger.warning("sandbox.db.write_failed", ...)`，不抛异常。

## Capabilities

### New Capabilities

- `sandbox-persistence`: 沙箱授权持久化 — SQLite 表存储 + 启动加载 + chip 隐式授权 + 手动优先冲突解决

### Modified Capabilities

- `filesystem-sandbox`（[archive spec](file:///d:/java/agentprojects/agentx/openspec/changes/archive/2026-07-03-adjust-m1-stack-and-structure/specs/filesystem-sandbox/spec.md)）：把"授权持久化与恢复"requirement 从「待实现」标记为「已实现」，并补充 `source` 字段语义。

## Impact

- **代码影响**：
  - 后端：`backend/app/memory/sandbox_store.py` 新建；`backend/app/utils/security.py` 双写 + bootstrap；`backend/app/main.py` lifespan + DELETE 端点联动；`backend/app/config.py` 新增 1 个开关。
  - 前端：`frontend/renderer/stores/chat.ts` 新增字段 + 隐式授权；`frontend/renderer/components/chat/ChatComposer.tsx` 区分手动/自动入口。
- **API 影响**：无新增端点；`POST /api/sandbox/authorize` 增加可选 `source` 字段（缺省 `manual`，向后兼容）。
- **依赖影响**：无新增依赖（`aiosqlite` 已被 checkpointer 使用）。
- **数据影响**：`data/agentx.db` 新增 `sandbox_authorize` 表；首次启动表为空，行为不变（用户重新点一次 chip 即可触发隐式授权）。
- **测试影响**：新增 `test_sandbox_store.py`（CRUD + bootstrap）；`test_security.py` 追加 source 优先级、revoke 后 bootstrap 不复活；`tests/renderer/` 新增 chat store 隐式授权跳过 revoked 的测试。
- **运维影响**：`AGENTX_SANDBOX_PERSISTENCE_ENABLED=false` 可临时降级为内存模式（故障注入 / 调试）。
- **安全影响**：`source` 字段不影响 `check_read` / `check_write` 放行逻辑（仅用于 UPSERT 优先级判定）；`full_trust` 仍不持久化（会话级临时态）。
