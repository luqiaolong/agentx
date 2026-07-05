## ADDED Requirements

### Requirement: 沙箱授权 SQLite 持久化

系统 SHALL 在 `data/agentx.db` 中维护 `sandbox_authorize` 表，存储所有 thread 的授权目录记录。表结构 MUST 包含字段：`thread_id TEXT`、`resolved_path TEXT`（已 resolve 的绝对路径）、`writable INTEGER`（0/1）、`source TEXT`（`'manual' | 'chip' | 'legacy'`）、`created_at TEXT`。主键 MUST 为 `(thread_id, resolved_path)`，并 MUST 在 `thread_id` 上建索引。

#### Scenario: 表自动创建

- **WHEN** 后端首次启动且 `data/agentx.db` 中不存在 `sandbox_authorize` 表
- **THEN** 系统 `CREATE TABLE IF NOT EXISTS sandbox_authorize (...)` 创建表与索引，不抛异常

#### Scenario: 授权写入持久化

- **WHEN** `SessionSandbox.authorize(thread_id="abc", path="d:/docs", writable=True, source="manual")` 被调用
- **THEN** 内存 `authorized_dirs["abc"]` 立即写入，且 `sandbox_authorize` 表 UPSERT 一条记录 `(abc, d:/docs, 1, manual, <now>)`，主键冲突时按 source 优先级合并（manual 不被 chip 覆盖）

#### Scenario: 撤销授权持久化

- **WHEN** `SessionSandbox.revoke(thread_id="abc", path="d:/docs")` 被调用
- **THEN** 内存移除该条目，且 `sandbox_authorize` 表 `DELETE WHERE thread_id='abc' AND resolved_path='d:/docs'`

#### Scenario: 删除会话时清空授权

- **WHEN** 用户删除 thread_id=abc 的会话（`DELETE /api/memory/checkpointer/{thread_id}`）
- **THEN** 系统**同时**删除 LangGraph checkpoint 与 `sandbox_authorize` 表中该 thread 的所有记录，返回 `{ok: true, deleted: <checkpoint_row_count>}`（`deleted` 字段保持现有语义：仅 checkpoint 行数，sandbox 删除行数不暴露给 API）

### Requirement: 启动时全量加载授权

系统 SHALL 在 `main.py` lifespan 启动阶段调用 `SessionSandbox.bootstrap_from_store()`，一次性 `SELECT * FROM sandbox_authorize` 按 `thread_id` 分组注入 `authorized_dirs` 内存 dict。加载失败时 MUST 仅 `logger.error` 并降级为空授权（不阻塞启动）。

#### Scenario: 启动恢复授权

- **WHEN** 后端重启，`sandbox_authorize` 表中有记录 `(abc, d:/docs, 1, manual, ...)` 与 `(abc, d:/book, 0, chip, ...)`
- **THEN** `SessionSandbox.authorized_dirs["abc"] = {(Path("d:/docs"), True), (Path("d:/book"), False)}`，后续 `check_read("d:/docs/x")` 直接通过，无需用户重新授权

#### Scenario: 启动加载失败降级

- **WHEN** `bootstrap_from_store()` 执行时 SQLite 抛 `OperationalError`
- **THEN** 系统 `logger.error("sandbox.bootstrap_failed", ...)`，`authorized_dirs` 保持为空 dict，后端正常启动，用户重新点 chip 触发隐式授权即可恢复

### Requirement: source 字段优先级

系统 SHALL 在 UPSERT 时保留 `source='manual'` 记录不被 `source='chip'` 覆盖。`source` 字段 MUST NOT 影响 `check_read` / `check_write` 的放行逻辑（仅用于 UPSERT 优先级判定）。

#### Scenario: manual 不被 chip 覆盖

- **WHEN** DB 已有记录 `(abc, d:/docs, 1, manual, ...)`，前端 chip 同步调 `authorize(abc, d:/docs, True, source="chip")`
- **THEN** UPSERT 后记录变为 `(abc, d:/docs, 1, manual, ...)`（writable 更新为 True，source 保持 manual）

#### Scenario: chip 升级为 manual

- **WHEN** DB 已有记录 `(abc, d:/docs, 0, chip, ...)`，用户手动调 `authorize(abc, d:/docs, True, source="manual")`
- **THEN** UPSERT 后记录变为 `(abc, d:/docs, 1, manual, ...)`

### Requirement: DB 写失败不阻塞内存

系统 MUST 在 `authorize()` / `revoke()` / `clear()` 双写时，若 DB 写失败仅 `logger.warning`，**不回滚内存**，**不抛异常**。`sandbox_persistence_enabled=False` 时 MUST 跳过所有 DB 写入（降级为纯内存模式）。

#### Scenario: DB 写失败内存仍生效

- **WHEN** `authorize(thread_id, path)` 调用时 SQLite 抛 `OperationalError`（如磁盘满）
- **THEN** 内存 `authorized_dirs` 仍写入该条目，`logger.warning("sandbox.db.write_failed", ...)` 记录失败，本次工具调用 `check_read` 通过

#### Scenario: 持久化开关关闭

- **WHEN** `AGENTX_SANDBOX_PERSISTENCE_ENABLED=false` 且用户调 `authorize(thread_id, path)`
- **THEN** 仅写内存，不触发任何 DB 操作，行为与本次 change 之前完全一致

### Requirement: 前端 chip 隐式授权

系统 SHALL 在 `createSession(workspacePath)` 与 `moveSessionToWorkspace(id, path)` 两个 store 入口自动调用 `window.api.sandbox.authorize(tid, path, true, source="chip")`，**除非**该 path 在该 session 的 `manuallyRevokedPaths` 中。`manuallyRevokedPaths` MUST 通过 zustand `persist` 持久化到 localStorage。

#### Scenario: 创建会话时隐式授权

- **WHEN** 前端调 `createSession(workspacePath="d:/book")` 且 `d:/book` 不在该 session 的 `manuallyRevokedPaths` 中
- **THEN** store 写入新 session，并异步调 `POST /api/sandbox/authorize` body `{"thread_id": "<tid>", "path": "d:/book", "writable": true, "source": "chip"}`

#### Scenario: 切换工作区时隐式授权

- **WHEN** 前端调 `moveSessionToWorkspace(id, "d:/new")` 且 `d:/new` 不在 `manuallyRevokedPaths` 中
- **THEN** store 更新 `workspacePath`，并异步调 `authorize(tid, "d:/new", true, source="chip")`

#### Scenario: 手动 revoke 后 chip 跳过

- **WHEN** 用户对 session id=abc 手动 revoke `d:/book`，store 把 `d:/book` 加入 `manuallyRevokedPaths`，随后该 session 因 `moveSessionToWorkspace(abc, "d:/book")` 触发隐式授权
- **THEN** 前端检测到 `d:/book` 在 `manuallyRevokedPaths` 中，**不**调 `authorize`，后端 `authorized_dirs[abc]` 不含 `d:/book`

#### Scenario: 手动 authorize 清除 revoked 标记

- **WHEN** 用户对 session id=abc 手动调 `authorize(tid, "d:/book", true, source="manual")`（如重新点击工作区 chip 选择同一目录）
- **THEN** 前端从 `manuallyRevokedPaths` 中移除 `d:/book`，后续 chip 同步不再跳过该 path

### Requirement: full_trust 与临时授权不持久化

系统 MUST NOT 持久化 `full_trust_threads` 集合与 `_temp_authorized` dict（once 决策）。这两者 MUST 保持为进程内内存态，进程重启即清空。

#### Scenario: full_trust 重启清空

- **WHEN** 用户在 session id=abc 启用 `full_trust` 模式后重启后端
- **THEN** 重启后 `SessionSandbox.full_trust_threads` 不含 abc，`check_read` 恢复正常授权检查

#### Scenario: once 决策不持久化

- **WHEN** 用户对某次工具调用选择 "once" 授权 `d:/temp`，工具调用完成后 `clear_temp` 清理，随后重启后端
- **THEN** 重启后 `sandbox_authorize` 表中无 `d:/temp` 记录，`check_read("d:/temp")` 抛 `PathNotAuthorized`

## MODIFIED Requirements

### Requirement: 授权生命周期管理（修改自 [filesystem-sandbox spec](file:///d:/java/agentprojects/agentx/openspec/changes/archive/2026-07-03-adjust-m1-stack-and-structure/specs/filesystem-sandbox/spec.md)）

原 spec 描述的「授权与当前 `thread_id` 绑定」「会话结束时 clear」「关闭 chat tab / 退出应用不触发 clear」语义**保持不变**。本次修改仅补充：

- **退出应用时不 clear** 的实现方式从「内存保留」改为「写入 SQLite，重启后 `bootstrap_from_store` 恢复」。
- **删除会话时 clear** 的实现方式从「内存 clear」改为「内存 clear + SQLite `DELETE WHERE thread_id=?`」。
- `/reset` 时根据「跨会话保留授权目录」开关决定是否调 `clear(thread_id)`——开关开启时**不** clear，SQLite 记录保留；开关关闭时 clear，SQLite 记录删除。

#### Scenario: 退出应用后授权恢复（修改）

- **WHEN** 用户退出应用（process 退出），下次启动进入同一 thread_id 会话
- **THEN** `SessionSandbox.bootstrap_from_store()` 从 `sandbox_authorize` 表加载该 thread 的所有授权记录，`authorized_dirs[thread_id]` 恢复，`check_read` 立即可用

#### Scenario: 删除会话时同步清空（修改）

- **WHEN** 用户点击「删除会话」删除 thread_id=abc
- **THEN** 系统调用 `SessionSandbox.clear(thread_id)`（内存清空）+ `sandbox_store.delete_by_thread(thread_id)`（SQLite 清空），两处都清空后才返回成功
