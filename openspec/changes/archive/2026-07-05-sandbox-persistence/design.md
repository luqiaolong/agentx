## Context

当前项目状态（截至 2026-07-05）：

- `SessionSandbox` 是按 `thread_id` 隔离的内存授权管理器（[security.py:68-260](file:///d:/java/agentprojects/agentx/backend/app/utils/security.py)），白名单 = `data/workspace` + `data/uploads` + `authorized_dirs[thread_id]`。
- `authorized_dirs: dict[str, set[tuple[Path, bool]]]` 纯内存，进程退出即丢。
- `_temp_authorized`（once 决策）与 `full_trust_threads` 也都是内存态——本次**不**持久化这两者。
- LangGraph `AsyncSqliteSaver` 已用 `data/agentx.db`（[checkpointer.py:27](file:///d:/java/agentprojects/agentx/backend/app/memory/checkpointer.py)），本次新建表共用此 DB 文件。
- 前端 `workspacePath` 仅是 zustand 元数据（[chat.ts:63](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/chat.ts)），后端不感知。
- 现有 [filesystem-sandbox spec](file:///d:/java/agentprojects/agentx/openspec/changes/archive/2026-07-03-adjust-m1-stack-and-structure/specs/filesystem-sandbox/spec.md) §"授权持久化与恢复" 已写明要持久化，**代码未落地**。

设计约束（来自 `AGENTS.md`）：

- §1.1 优先用现成框架：SQLite 用 `aiosqlite`（已在依赖中），不引入新 ORM。
- §14.3 沙箱授权走 HTTP（`POST /api/sandbox/authorize`），不走 IPC。
- §18 安全红线：`full_trust` 仍不持久化；`write_file` / `edit_file` / `shell_exec` 仍只在路径 C 暴露。

## Goals / Non-Goals

**Goals:**

- 后端重启后，所有 thread 的 `authorized_dirs` 自动从 SQLite 恢复，`check_read` 立即可用。
- 前端「选工作区」语义直接等于「后端已授权」，用户无需感知 `POST /api/sandbox/authorize` 的存在。
- 手动 `revoke` 后，chip 自动同步不会复活该路径。
- 删除 thread 时，`sandbox_authorize` 表中该 thread 的记录同步清除。
- 所有改动有单测覆盖，关键路径有集成测试。

**Non-Goals:**

- 不持久化 `full_trust_threads`（会话级临时态，语义上不应跨重启保留）。
- 不持久化 `_temp_authorized`（once 决策，语义就是临时）。
- 不迁移历史授权（首次启动表为空，行为不变；用户重新点一次 chip 即可触发隐式授权）。
- 不做跨设备同步（`manuallyRevokedPaths` 仅持久化到本机 localStorage）。
- 不改 SSE 事件契约。
- 不改 `check_read` / `check_write` 放行逻辑（仅扩展持久化层）。
- 不写 ADR（属 §1.1 框架内常规扩展，非反面清单例外）。

## Decisions

### D1. 持久化介质：独立 SQLite 表 `sandbox_authorize`

**选择**：在 `data/agentx.db` 新建独立表，不接 LangGraph checkpoint。

**理由**：

- LangGraph checkpoint 的 state schema 是 `RouterState` TypedDict，加 `authorized_dirs` 字段需要改 `router/state.py` + 三路径的 state 传递链——改动面太大，且 `SessionSandbox` 是单例而非 per-graph 状态，耦合 checkpoint 反而破坏隔离。
- 独立表 schema 简单（5 字段），CRUD 直观，可用 `aiosqlite` 直接写，无需 LangGraph 的 checkpoint 序列化协议。
- 复用同一 DB 文件（`data/agentx.db`），不增加文件管理复杂度。
- 删除 thread 时，独立表可用 `DELETE WHERE thread_id=?` 直接清理，不需要解析 checkpoint blob。

**备选**：

- 写入 LangGraph `RouterState.authorized_dirs` → 改动面大，state schema 耦合沙箱内部状态。
- JSON 落盘 `data/sandbox.json` → 并发写要加锁，启动加载慢，无索引。
- 新建独立 SQLite 文件 → 文件管理复杂度增加，与 checkpoint 文件分裂。

### D2. 启动加载策略：一次性全量加载

**选择**：`main.py` lifespan 启动阶段 `SELECT * FROM sandbox_authorize` → 按 `thread_id` 分组注入 `SessionSandbox.authorized_dirs`。

**理由**：

- 热路径（`check_read` / `check_write`）仍然是纯内存 dict 查找，零 IO 改动。
- 启动一次 IO 可控（即使 1000 个 thread × 5 个授权 = 5000 行，SQLite SELECT < 50ms）。
- 简单可靠，无需懒加载的缓存失效逻辑。

**备选**：

- 懒加载（check_read 找不到时再 SELECT）→ 热路径多一次 DB 查询，且要处理并发加载。
- 启动加载 + 后台 poll → 过度工程，YAGNI。

### D3. source 字段与手动优先语义

**选择**：表加 `source TEXT NOT NULL` 字段，取值 `'manual' | 'chip' | 'legacy'`。UPSERT 规则：

```sql
INSERT INTO sandbox_authorize (thread_id, resolved_path, writable, source, created_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(thread_id, resolved_path) DO UPDATE SET
    writable = excluded.writable,
    source = CASE
        WHEN sandbox_authorize.source = 'manual' THEN 'manual'
        ELSE excluded.source
    END;
```

**理由**：

- `manual`（用户显式 authorize / 工作区 chip 选择）优先级最高，chip 自动同步不应降级。
- `chip`（前端启动 / 切会话时自动 authorize）优先级次之。
- `legacy`（首次启动时把内存中已有但 source 未知的记录补写）优先级最低——本 change 不实际产生 `legacy` 记录（首次启动表为空），保留字段为未来迁移留口子。
- 手动 `revoke` 后，前端把 path 写入 `manuallyRevokedPaths`，后续 chip 同步跳过——**不依赖 source 字段**，靠前端 store 状态决定。

**备选**：

- 不加 source 字段，所有授权同等优先级 → 无法区分「用户主动」与「自动同步」，revoke 后 chip 会复活。
- source 用整数枚举 → 可读性差，调试不友好。

### D4. DB 写失败语义：内存优先，不回滚

**选择**：`authorize()` / `revoke()` / `clear()` 双写时，DB 写失败仅 `logger.warning`，**不回滚内存**，**不抛异常**。

**理由**：

- 工具调用热路径不能因持久化失败而阻塞——用户当前操作必须完成。
- 内存优先保证本次会话可用；DB 失败仅影响下次重启恢复（降级为「这次授权重启后丢」）。
- `sandbox_persistence_enabled=False` 时直接跳过 DB 写入，便于故障注入测试。

**风险**：

- 极端情况：DB 文件只读 / 磁盘满 → 重启后授权丢失。可接受（用户重新点 chip 即可恢复）。

**备选**：

- DB 写失败时回滚内存 → 工具调用被阻塞，用户体验差。
- 抛异常让上层处理 → 破坏 `SessionSandbox` 现有契约（不抛异常，返回错误字符串）。

### D5. 前端 hydrate 不调 authorize

**选择**：前端启动 hydrate 时**不**遍历 sessions 调 authorize，仅依赖后端 `bootstrap_from_store`。

**理由**：

- 后端 lifespan 早于前端 renderer 的 hydrate（Electron main → spawn backend → renderer load），后端 bootstrap 完成时前端还没发请求。
- 前端 hydrate 时调 authorize 会触发对 8123 的早期请求，可能因后端未就绪而失败，引入启动竞态。
- 后端 bootstrap 已覆盖所有持久化记录，前端无需重复同步。

**例外**：

- `createSession(workspacePath)` / `moveSessionToWorkspace(id, path)` 是**用户主动操作**，此时后端必然已就绪，这两个入口触发隐式 authorize。

**备选**：

- 前端 hydrate 时遍历 sessions 调 authorize → 启动竞态 + 重复 IO，已否决。

### D6. manuallyRevokedPaths 持久化位置

**选择**：zustand `persist` 中间件持久化到 localStorage，key 与现有 chat store 同前缀。

**理由**：

- 跨设备同步非目标（YAGNI）。
- localStorage 已被 chat store 使用，无需新引入存储介质。
- 简单可靠，前端可独立读写。

**备选**：

- 写入后端 SQLite → 跨设备同步，但非目标，且增加表 / 端点。
- 写入 electron-store → 主进程 IPC 开销，且与 chat store 持久化分裂。

### D7. 手动 authorize 清除 revoked 标记

**选择**：前端 store 提供 `authorizeAndUnmark(sessionId, path, writable)` 方法，调用 `window.api.sandbox.authorize` 同时从 `manuallyRevokedPaths` 移除 path。`handleAttachWorkspace`（用户点击工作区 chip 选目录）复用此方法。

**理由**：

- 用户手动重新选同一目录 = 撤销之前的 revoke 意图，语义自洽。
- 避免前端 store 出现「path 在 manuallyRevokedPaths 中但后端已授权」的不一致状态。
- `revokeAndMark` 与 `authorizeAndUnmark` 成对出现，构成完整的「手动优先」语义闭环。

**备选**：

- 手动 authorize 不清除 revoked 标记 → 用户 revoke 后再手动 authorize，下次 chip 同步仍被跳过，状态不一致。
- 在 `handleAttachWorkspace` 内联处理 → 逻辑分散，测试困难。

### D8. UI 范围：本次不新增「彻底撤销授权」按钮

**选择**：本次 change 仅在 store 层提供 `revokeAndMark` / `authorizeAndUnmark` 两个方法，**不**在 ChatComposer / SessionList 新增 UI 按钮。

**理由**：

- 本次核心是持久化 + chip 隐式授权，UI 新入口是独立的用户体验决策（按钮放哪、文案、确认对话框）。
- 把 UI 拆到下一 change，避免本次范围膨胀。
- store 层方法已暴露，未来 UI 改动只需调一行 `revokeAndMark`，无后端改动。

**备选**：

- 本次一并加 UI → 范围扩大，review 成本高，且 UI 设计需要单独 brainstorm。
- 不提供 `revokeAndMark` → 未来 UI 改动要重新改 store，重复工作。
