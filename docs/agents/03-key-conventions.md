# 关键约定 / 易踩坑（§14.1–§14.6）

> 原 `AGENTS.md` §14 拆分（不含 §14.7 重启 SOP，那部分在
> [04-restart-sop.md](file:///d:/java/agentprojects/agentx/docs/agents/04-restart-sop.md)）。
> 阅读时机：首次接入某个子系统、排查相关 bug、写代码前查阅约束。

---

## §14.1 凭证与配置

- 后端 `Settings` 用 `env_prefix="AGENTX_"` + `env_file=None`，**禁止**从 `.env` 读凭证。
- 凭证（LLM key / Milvus user/password）由 Rust 主进程从 `tauri-plugin-store`
  （`enc:` / `plain:` 前缀格式）→ 通过 `tokio::process::Command::env()` 注入进程环境。
  注入点在 [src-tauri/src/backend/env.rs::build_env](file:///d:/java/agentprojects/agentx/src-tauri/src/backend/env.rs)。
- 修改 `.env.example` 仅是文档用途，**运行时不会生效**。
- 旧 Electron 用户首次启动 Tauri 时，`migration::migrate_electron_store()` 自动迁移
  `%APPDATA%/agentx/config.json` → tauri-plugin-store；`enc:` 加密值无法跨进程解密，
  记录到 `MigrationReport.requires_reinput` 由前端提示用户重新输入。
- Milvus `auth_enabled=False` 时跳过凭证校验（myserver Milvus authorizationEnabled=false），
  见 [config.py::milvus_credentials_configured](file:///d:/java/agentprojects/agentx/backend/app/config.py#L550-L555)。

## §14.2 Tauri ↔ 后端进程

- 后端 8123 端口由 [src-tauri/src/backend/handle.rs::PythonHandle::start](file:///d:/java/agentprojects/agentx/src-tauri/src/backend/handle.rs)
  启动（`uv run python -m app.main`，uv 缺失则回退 `python -m app.main`）。
- 崩溃退避：指数 1s/2s/4s 最多 3 次 → `giving_up` 状态由前端遮罩兜底。
- **dev_mode 持久化 + 切换即重启**：切换 dev_mode 开关时前端先 `setDevMode()` 写 store，
  再 `restartBackend()` 立即以新值 spawn。`wait_for_ready` 保证 mask 不卡（emit Ready / GivingUp）。
  选项自动持久化，下次应用启动也按此值 spawn。dev_mode=true 时跨平台 console 拉起：
  | 平台 | 命令 |
  |---|---|
  | Windows | `powershell -NoExit -Command "Set-Location -LiteralPath <cwd>; uv run python -m app.main"` |
  | macOS   | `osascript -e 'tell application "Terminal" to do script "cd <cwd> && uv run python -m app.main; exec /bin/bash"'` |
  | Linux   | `x-terminal-emulator -e bash -lc "cd <cwd> && uv run python -m app.main; exec bash"`（缺失则回退 gnome-terminal / konsole，全缺失降级 tokio） |
- 关闭时 Windows 必须 `taskkill /T /F` 杀整棵进程树（uv→python 父子链），否则
  8123 端口被占用导致下次启动 Errno 10048。**完整的重启 SOP 见 [04-restart-sop.md](file:///d:/java/agentprojects/agentx/docs/agents/04-restart-sop.md)**。
- 配置存储统一走 `tauri-plugin-store`（文件 `config.json`），凭证用 `enc:` / `plain:`
  前缀格式，与 electron-store 旧格式兼容以便迁移。

## §14.3 沙箱与安全

> 2026-07-08 重构：沙箱与安全代码从 `utils/security.py` / `approval/` / `memory/sandbox_store.py` / `deep/approval.py` / `api/sandbox.py` 抽取为独立的 [sandbox/](file:///d:/java/agentprojects/agentx/backend/app/sandbox/) + [security/](file:///d:/java/agentprojects/agentx/backend/app/security/) 两个顶级包，与 `deep/` / `team/` / `tools/` 平行。

- **沙箱授权**：文件操作走 [app.sandbox.get_sandbox](file:///d:/java/agentprojects/agentx/backend/app/sandbox/session_sandbox.py)（`SessionSandbox` async + `asyncio.Lock`），未授权目录 → `PathNotAuthorized`。
- **持久化**：[app.sandbox.store](file:///d:/java/agentprojects/agentx/backend/app/sandbox/store.py) SQLite WAL + `busy_timeout=30000`，并发写不锁。
- **路径保护**：[app.sandbox.path_guard](file:///d:/java/agentprojects/agentx/backend/app/sandbox/path_guard.py) 归一化 + 关键目录黑名单（修复 Linux `Path('/')` 误判 bug）。
- **parent_thread_id 继承**：Team 模式子任务继承父 thread 授权（`run_coding_expert(parent_thread_id=thread_id)`）。
- **审批决策**：[app.security.approval.ApprovalDecision](file:///d:/java/agentprojects/agentx/backend/app/security/approval/decision.py)（`str, Enum`：`approve/once/session/deny`），`ApprovalResult.approved` 为 property。
- **审批状态**：[app.security.approval.state](file:///d:/java/agentprojects/agentx/backend/app/security/approval/state.py) 模块级 dict + `asyncio.Lock`，5 个 dict value 为 `tuple[T, float]`（TTL timestamp）。
- **TTL reaper**：`start_reaper()` 后台协程每 5 分钟清理 30 分钟无活动的 thread_id（`main.py` lifespan 启动）。
- **原子原语**：`wait_for_resume(thread_id, timeout)` / `wait_for_abort(thread_id, timeout)` 消除 "check 后、await 前 clear 已 set event" 竞态。
- **公共审批循环**：[app.security.approval.flow.run_approval_loop](file:///d:/java/agentprojects/agentx/backend/app/security/approval/flow.py) 统一 work/coding 两场景审批逻辑。
- **危险工具**：[app.security.dangerous_tools](file:///d:/java/agentprojects/agentx/backend/app/security/dangerous_tools.py) `DANGEROUS_TOOLS` + `FORBIDDEN_SUBAGENT_TOOLS`（`frozenset`，移除已废弃的 `shell_exec`）。
- **命令过滤**：[app.security.command_filter](file:///d:/java/agentprojects/agentx/backend/app/security/command_filter.py) `DEFAULT_BLOCKLIST` + `redact_args`（`cli_execute` 的 `command`/`arguments` 脱敏）。
- 沙箱授权目录通过 `POST /api/sandbox/authorize` 显式开启（renderer 直连 HTTP，**不**走 Tauri invoke）。
- 系统关键目录黑名单（Windows / Unix）在 [src-tauri/src/commands/dialog.rs::save_dropped_file](file:///d:/java/agentprojects/agentx/src-tauri/src/commands/dialog.rs)。
- `POST /api/sandbox/revoke` 撤销授权；`GET /api/sandbox/authorized/{thread_id}` 列出已授权目录。

## §14.4 SSE / 审批流

- 审批状态用模块级 `_pending_approvals: dict[str, tuple[ApprovalResult, float]]` 内存 dict 维护
  （带 TTL timestamp，reaper 自动清理）。
- 自动批准：`AGENTX_AUTO_APPROVE_AFTER_SECONDS > 0` 时倒计时归零自动 approve；
  `= 0` 禁用，等用户操作。
- `AGENTX_APPROVAL_MAX_WAIT`（默认 300s）控制单次审批最长等待；`0` = 上限 3600s（bug 已修复）。
- SSE handler 每轮检查 `_abort_flags[thread_id]`，用户中止立即退出循环。
- 审批类型 `kind`：`dangerous_tool`（写/编辑/cli_execute）| `directory_extension`
  （路径越界扩展授权，含 `requestedPath` + `writable`）。
- `full_trust` 模式跳过 `directory_extension` 预检查；`cli_execute` 始终需审批（workspace 授权仅放行 fs 工具）。

## §14.5 路径导入循环（已消除）

`2026-07-06-paths-refactor` 重构后 `graph.py` 与路径模块**无循环导入**：

- `graph.py` 顶层单向 import `app.chat.run` / `app.deepagent.agent` /
  `app.subagents.dispatch` / `app.team.runner`。
- `deepagent/agent.py` 用 `TYPE_CHECKING` 延迟导入 `RouterState`，**禁止**改为运行时导入。
- `app.paths` 包已删除，**禁止**重新创建 `backend/app/paths/` 目录。

## §14.6 路由别名（前端）

- `@` → `frontend/renderer`
- 见 [vite.config.ts](file:///d:/java/agentprojects/agentx/vite.config.ts) +
  [tsconfig.web.json](file:///d:/java/agentprojects/agentx/tsconfig.web.json)。