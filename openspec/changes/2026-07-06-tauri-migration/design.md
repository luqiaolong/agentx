# Design: Electron → Tauri 2.x 全量替换

## Context

当前代码结构（截至 2026-07-06）：

```
agentx/
├── frontend/
│   ├── main/                       ← Electron main 进程（860 行 TS）
│   │   ├── index.ts                ← BrowserWindow + 30 个 IPC handler
│   │   ├── store.ts                ← electron-store + safeStorage 凭证加密
│   │   ├── logger.ts               ← 日志落盘
│   │   └── python/spawn.ts         ← Python 后端 spawn
│   ├── preload/index.ts            ← contextBridge (515 行)
│   ├── renderer/                   ← React UI（25+ 处 window.api.* 调用）
│   └── shared/api-types.ts         ← 共享类型
├── electron.vite.config.ts         ← 三入口（main/preload/renderer）
├── package.json                    ← electron + electron-builder + dugite 依赖
└── backend/                        ← Python 后端（不变）
```

依赖关系：
```
renderer → preload (window.api) → IPC → main (Node)
                                   ↓
                            electron-store + dugite
                                   ↓
                       Python subprocess (uv run)
```

## Goals / Non-Goals

**Goals:**
- 删除 `frontend/main/`、`frontend/preload/`，替换为 Tauri Rust 主进程
- 25+ 个 `window.api.*` 调用点全部迁移到 `@tauri-apps/api/core::invoke`
- 30 个 IPC handler 改写为 30 个 Tauri command（同名同参）
- `electron-store` 数据迁移到 `tauri-plugin-store` + `tauri-plugin-stronghold`
- `dugite` 8 个 Git 命令迁移到 `git2` crate
- Python 后端 spawn 改用 `tokio::process::Command` 注入 env
- 打包脚本从 `electron-builder` 收敛到 `tauri build`
- 自动更新从 `electron-updater` 切换到 `tauri-plugin-updater`
- 凭证数据自动迁移脚本（老用户零感知）
- 端到端冒烟脚本通过（25+ 用例）

**Non-Goals:**
- 不改后端 Python 代码（`backend/` 0 改动）
- 不改 SSE 事件契约（AGENTS.md §13）
- 不改 HTTP API 端点签名
- 不改前端 React 业务逻辑（仅改 IPC 调用方式）
- 不交付 macOS / Linux 平台打包（后续 PR）
- 不做 Electron ↔ Tauri 双轨兼容（用户已确认一次性迁移）

## Decisions

### D1. 目标项目结构

```
agentx/
├── src-tauri/                              ← 新增：Rust 主进程
│   ├── Cargo.toml                          ← Rust 依赖
│   ├── tauri.conf.json                     ← 窗口/打包/权限
│   ├── build.rs                            ← Tauri build script
│   ├── icons/                              ← 复用 build/icon.png + icon.ico
│   ├── capabilities/
│   │   └── default.json                    ← 命令权限声明
│   └── src/
│       ├── main.rs                         ← 入口（启动 Python、注册 commands）
│       ├── lib.rs                          ← run() 函数（Tauri 2.x 推荐）
│       ├── commands/                       ← 30 个 Tauri command
│       │   ├── mod.rs                      ← re-export
│       │   ├── dialog.rs                   ← openFile/openFolder/saveFile/saveDroppedFile
│       │   ├── shell.rs                    ← revealInFolder/openExternal
│       │   ├── notify.rs                   ← 系统通知
│       │   ├── clipboard.rs                ← 读写剪贴板
│       │   ├── window.rs                   ← minimize/maximize/close + maximized-change 事件
│       │   ├── logs.rs                     ← read + cleanOldLogs
│       │   ├── app.rs                      ← getVersion/quit/restart/restartBackend/reloadBackendConfig/initAgentsMd/getHomeWorkspaceDir
│       │   ├── settings.rs                 ← 14 个 settings 命令（凭证 + 配置 CRUD）
│       │   ├── git.rs                      ← 8 个 Git 命令（git2）
│       │   └── python_status.rs            ← python:status 事件发射
│       ├── backend/                        ← Python spawn（替代 spawn.ts）
│       │   ├── mod.rs
│       │   ├── env.rs                      ← build_env() 注入凭证
│       │   ├── handle.rs                  ← PythonHandle (start/stop/waitForReady)
│       │   └── reload.rs                  ← /api/config/reload 调用
│       ├── store/                          ← 凭证 + 配置存储
│       │   ├── mod.rs                      ← TauriStore 封装
│       │   ├── credentials.rs              ← stronghold 加密封装
│       │   └── migration.rs                ← electron-store JSON → stronghold 迁移
│       ├── git/                            ← Git 集成
│       │   ├── mod.rs                      ← 公共类型
│       │   ├── status.rs                   ← git:getStatus
│       │   ├── log.rs                      ← git:getLog
│       │   ├── branches.rs                 ← git:getBranches
│       │   ├── checkout.rs                 ← git:checkout
│       │   ├── stage.rs                    ← git:stage/unstage/discardChanges
│       │   ├── commit.rs                   ← git:commit
│       │   └── diff.rs                     ← git:getDiff
│       ├── logger/                         ← 日志落盘
│       │   └── mod.rs
│       └── migration/                      ← electron-store → stronghold 数据迁移
│           └── mod.rs
├── vite.config.ts                          ← 简化（去掉 electron-vite 三入口）
├── package.json                            ← 改 scripts 为 tauri dev/build
├── frontend/
│   ├── src/                                ← 原 renderer/
│   │   ├── components/                     ← 改 window.api.* → invoke
│   │   ├── hooks/
│   │   ├── stores/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   └── index.html
│   └── shared/api-types.ts                 ← 不变
└── backend/                                ← 不变
```

### D2. IPC → Tauri Command 映射（61 个 handler）

> **注**：preload 另有 19 处 HTTP fetch 代理调用后端 API（chat/sandbox/skills/workspace/approve/health/mcp/memory），不走 IPC，迁移后 renderer 直接 `fetch()` 调用，详见 D13。

| Electron IPC channel | Tauri Command | 参数类型 | 返回类型 |
|---|---|---|---|
| `dialog:openFile` | `dialog_open_file` | `Option<DialogOptions>` | `OpenDialogResult` |
| `dialog:openFolder` | `dialog_open_folder` | `()` | `OpenDialogResult` |
| `dialog:saveFile` | `dialog_save_file` | `Option<SaveDialogOptions>` | `SaveDialogResult` |
| `dialog:saveDroppedFile` | `dialog_save_dropped_file` | `(String, String)` | `String` |
| `shell:revealInFolder` | `shell_reveal_in_folder` | `String` | `()` |
| `shell:openInEditor` | `shell_open_in_editor` | `String` | `String` |
| `shell:openExternal` | `shell_open_external` | `String` | `()` |
| `notify:show` | `notify_show` | `NotifyOptions` | `()` |
| `clipboard:read` | `clipboard_read` | `()` | `String` |
| `clipboard:write` | `clipboard_write` | `String` | `()` |
| `window:minimize` | `window_minimize` | `()` | `()` |
| `window:maximize` | `window_maximize` | `()` | `()` |
| `window:close` | `window_close` | `()` | `()` |
| `window:isMaximized` | `window_is_maximized` | `()` | `bool` |
| `app:getVersion` | `app_get_version` | `()` | `String` |
| `app:quit` | `app_quit` | `()` | `()` |
| `app:restart` | `app_restart` | `()` | `()` |
| `app:restartBackend` | `app_restart_backend` | `()` | `RestartResult` |
| `app:reloadBackendConfig` | `app_reload_backend_config` | `()` | `ReloadResult` |
| `app:initAgentsMd` | `app_init_agents_md` | `()` | `InitAgentsMdResult` |
| `app:getHomeWorkspaceDir` | `app_get_home_workspace_dir` | `()` | `String` |
| `settings:setMilvusCredentials` | `settings_set_milvus_credentials` | `(String, String)` | `OkResult` |
| `settings:getMilvusCredentials` | `settings_get_milvus_credentials` | `()` | `MilvusCredentials` |
| `settings:getApiKey` | `settings_get_api_key` | `String` | `Option<String>` |
| `settings:setApiKey` | `settings_set_api_key` | `(String, String)` | `OkResult` |
| `settings:getLLMConfig` | `settings_get_llm_config` | `()` | `LLMConfig` |
| `settings:setLLMConfig` | `settings_set_llm_config` | `(String, String)` | `OkResult` |
| `settings:getSystemPrompt` | `settings_get_system_prompt` | `()` | `String` |
| `settings:setSystemPrompt` | `settings_set_system_prompt` | `String` | `OkResult` |
| `settings:getApprovalConfig` | `settings_get_approval_config` | `()` | `ApprovalConfig` |
| `settings:setApprovalConfig` | `settings_set_approval_config` | `ApprovalConfig` | `OkResult` |
| `settings:getKnowledgeConfig` | `settings_get_knowledge_config` | `()` | `KnowledgeConfig` |
| `settings:setKnowledgeConfig` | `settings_set_knowledge_config` | `KnowledgeConfig` | `OkResult` |
| `settings:getSubagentsConfig` | `settings_get_subagents_config` | `()` | `SubagentsConfig` |
| `settings:setSubagentsConfig` | `settings_set_subagents_config` | `SubagentsConfig` | `OkResult` |
| `settings:getTeamSubagentsConfig` | `settings_get_team_subagents_config` | `()` | `TeamSubagentsConfig` |
| `settings:setTeamSubagentsConfig` | `settings_set_team_subagents_config` | `TeamSubagentsConfig` | `OkResult` |
| `settings:getCustomSubagents` | `settings_get_custom_subagents` | `()` | `CustomSubagentsMap` |
| `settings:setCustomSubagents` | `settings_set_custom_subagents` | `CustomSubagentsMap` | `OkResult` |
| `settings:addCustomSubagent` | `settings_add_custom_subagent` | `CustomSubagentInput` | `OkResult` |
| `settings:removeCustomSubagent` | `settings_remove_custom_subagent` | `String` | `OkResult` |
| `settings:getToolsConfig` | `settings_get_tools_config` | `()` | `ToolsConfig` |
| `settings:setToolsConfig` | `settings_set_tools_config` | `ToolsConfig` | `OkResult` |
| `settings:getProfileAutoExtract` | `settings_get_profile_auto_extract` | `()` | `bool` |
| `settings:setProfileAutoExtract` | `settings_set_profile_auto_extract` | `bool` | `OkResult` |
| `settings:getMcpServersConfig` | `settings_get_mcp_servers_config` | `()` | `McpServersConfig` |
| `settings:setMcpServersConfig` | `settings_set_mcp_servers_config` | `McpServersConfig` | `OkResult` |
| `settings:getModelEntries` | `settings_get_model_entries` | `()` | `Vec<ModelEntry>` |
| `settings:setModelEntries` | `settings_set_model_entries` | `Vec<ModelEntry>` | `OkResult` |
| `settings:getActiveModelId` | `settings_get_active_model_id` | `()` | `Option<String>` |
| `settings:activateModel` | `settings_activate_model` | `String` | `OkResult` |
| `logs:read` | `logs_read` | `(Option<String>, Option<usize>)` | `String` |
| `git:getStatus` | `git_get_status` | `String` | `GitStatus` |
| `git:getLog` | `git_get_log` | `(String, Option<usize>)` | `Vec<GitCommit>` |
| `git:getBranches` | `git_get_branches` | `String` | `Vec<GitBranch>` |
| `git:checkout` | `git_checkout` | `(String, String)` | `GitOpResult` |
| `git:stage` | `git_stage` | `(String, Vec<String>)` | `GitOpResult` |
| `git:unstage` | `git_unstage` | `(String, Vec<String>)` | `GitOpResult` |
| `git:commit` | `git_commit` | `(String, String)` | `GitOpResult` |
| `git:discardChanges` | `git_discard_changes` | `(String, Vec<String>)` | `GitOpResult` |
| `git:getDiff` | `git_get_diff` | `(String, Option<String>)` | `String` |

事件（双向推送）：
| Electron 事件 | Tauri Event | 触发时机 |
|---|---|---|
| `python:status` | `python:status` | Python spawn 状态变化（starting/ready/error/giving_up） |
| `window:maximized-change` | `window:maximized-change` | 窗口最大化状态变化 |

### D3. 凭证存储迁移（electron-store → stronghold）

**electron-store 现状**（[frontend/main/store.ts](file:///d:/java/agentprojects/agentx/frontend/main/store.ts)）：
- 普通配置：明文 JSON 存储（`milvus.user`/`milvus.password`/`apikey.*` 也用同一存储）
- 凭证加密：`enc:<base64(safeStorage.encrypt)>` 格式，fallback `plain:<value>`

**迁移方案**：

1. **配置存储**：用 `tauri-plugin-store`（API 几乎对等：`store.get(key)` / `store.set(key, value)`）
2. **凭证加密**：用 `tauri-plugin-stronghold`（封装 OS keychain：Windows Credential Manager / macOS Keychain / Linux Secret Service）
3. **数据迁移**：
   - 启动时检测 electron-store 默认路径（`new Store()` 无自定义 name）：
     - Windows: `%APPDATA%/agentx/config.json`
     - macOS: `~/Library/Application Support/agentx/config.json`
     - Linux: `~/.config/agentx/config.json`
   - 若存在且 stronghold 无数据 → 读取所有 key → 写入 stronghold → 备份原文件到 `config.json.migrated` → 删除
   - 加密值检测：`enc:` 前缀 → 用 Rust 端解密（需实现 safeStorage 兼容解码）→ 重新加密进 stronghold
   - 明文值：`plain:` 前缀或裸字符串 → 直接加密进 stronghold

**数据迁移的关键决策**：
- safeStorage 解密在 Rust 端需重新实现（OS DPAPI / Keychain 解密）
- Windows DPAPI Rust crate：`windows` crate 的 `CryptUnprotectData`
- macOS Keychain Rust crate：`security-framework`
- Linux Secret Service Rust crate：`secret-service`

**简化路径**（推荐）：**不实现 safeStorage 解密**，迁移脚本只迁移明文值（`plain:` 和裸字符串），加密值（`enc:`）提示用户重新输入。
理由：
- safeStorage 是 Electron 内部抽象，跨平台解密复杂
- 凭证（API key）老用户重新输入成本低（最多 5 个 key：openai/anthropic/deepseek/tavily/milvus user/password）
- 避免引入 DPAPI/Keychain Rust crate 拖慢迁移进度

### D4. Git 命令迁移（dugite → git2）

**dugite 现状**：调 `git status --porcelain -u --branch`、`git log --pretty=format:...` 等命令，解析 stdout 字符串。

**git2 方案**：
- `git2` crate 提供 libgit2 绑定
- 但 libgit2 不直接支持所有 porcelain 输出（特别是 porcelain v2）
- 部分操作（如 stage/unstage/commit）仍可走 `git2` 的 high-level API
- status 解析：可以用 `git2::Repository::statuses(None)` 返回 `Status` 列表，与 `--porcelain` 语义类似

**决策**：**用 git2 crate**，逐个迁移：
| dugite 命令 | git2 替代 |
|---|---|
| `git status --porcelain -u --branch` | `repo.statuses(None)` + `repo.head()` 解析 |
| `git log --pretty=format:...` | `repo.revwalk()` + `commit.message()` 等 |
| `git branch -a -vv` | `repo.branches(None)` + `branch.name()` 等 |
| `git checkout <branch>` | `repo.checkout_tree(...)` + `repo.set_head(...)` |
| `git add -- <files>` | `repo.index().add_path(...)` |
| `git reset HEAD -- <files>` | `repo.reset(...)` |
| `git commit -m <msg>` | `repo.commit(...)` |
| `git checkout -- <files>` | `repo.checkout_index(...)` |
| `git diff` / `git diff -- <file>` | `repo.diff(...).to_buf()` |

### D5. Python 后端 spawn（Node spawn → tokio::process::Command）

**Node spawn 现状**（[frontend/main/python/spawn.ts](file:///d:/java/agentprojects/agentx/frontend/main/python/spawn.ts)）：
- `child_process.spawn('uv', ['run', 'python', '-m', 'app.main'], { cwd, env })`
- env 注入 `AGENTX_*` 凭证 + 配置（由 Electron Main 从 electron-store 读）
- 启动握手：轮询 `http://127.0.0.1:8123/` **连续 2 次 200** 才标记 ready（30s 超时）
- 崩溃重试：非零退出指数退避（1s/2s/4s）最多 3 次，uv 失败回退 `python -m app.main`
- 进程清理：Windows 用 `taskkill /T /F` 递归杀进程树，Unix 用负 PID 杀进程组
- 凭证注入完整列表（`AGENTX_` 前缀）：
  - `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / `TAVILY_API_KEY`
  - `MILVUS_USER` / `MILVUS_PASSWORD` / `MILVUS_HOST` / `MILVUS_PORT` / `MILVUS_DB` / `MILVUS_COLLECTION` / `MILVUS_AUTH_ENABLED`
  - `EMBEDDING_URL` / `DEFAULT_MODEL` / `OPENAI_BASE_URL` / `DEFAULT_SYSTEM_PROMPT`
  - `APPROVAL_MAX_WAIT` / `MAX_UPLOAD_BYTES` / `MAX_OUTPUT_TOKENS` / `THINK_FILTER_MAX_HOLD`
  - `SUBAGENTS_CONFIG` / `TEAM_SUBAGENTS_CONFIG` / `CUSTOM_SUBAGENTS_CONFIG` / `TOOLS_CONFIG`（JSON 字符串）
  - `PROFILE_AUTO_EXTRACT` / `MCP_SERVERS_CONFIG`（JSON 字符串）
  - `LANGSMITH_API_KEY`（注意：无 `AGENTX_` 前缀）

**Rust spawn 方案**：
```rust
use tokio::process::Command;

let mut cmd = Command::new("uv");
cmd.args(["run", "python", "-m", "app.main"])
   .current_dir(backend_cwd)
   .envs(env_map)
   .stdout(Stdio::piped())
   .stderr(Stdio::piped());

let mut child = cmd.spawn()?;
let status_tx = app_handle.emit("python:status", status);

// 启动握手：连续 2 次 200 才算 ready（与 Node 逻辑一致）
let mut consecutive = 0;
loop {
    match reqwest::get(format!("http://127.0.0.1:{}/", port)).await {
        Ok(r) if r.status().is_success() => {
            consecutive += 1;
            if consecutive >= 2 { break; }
        }
        _ => consecutive = 0,
    }
    sleep(Duration::from_millis(200)).await;
}
```

**必须复现的 Node 逻辑**：
1. uv 缺失时回退 `python -m app.main`
2. 崩溃重试：指数退避（1s/2s/4s）最多 3 次，重试时沿用已切换的命令
3. 进程清理：Windows `taskkill /T /F` 递归杀进程树，Unix 负 PID 杀进程组
4. 握手：连续 2 次 200（非单次），30s 超时
5. 状态推送：`starting` / `ready` / `crashed` / `giving_up` 四种状态

### D6. 窗口与平台特定功能

**无边框窗口**（[frontend/main/index.ts:114-131](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L114-L131)）：
- Electron `frame: false`, `titleBarStyle: "hidden"`, `backgroundColor: "#020617"`
- Tauri 等价：`tauri.conf.json::windows[].decorations = false` + `transparent = false`

**AppUserModelID（Windows）**（[frontend/main/index.ts:805-807](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L805-L807)）：
- Tauri 在 `tauri.conf.json::bundle.identifier = "com.agentx.desktop"` 自动处理

**JumpList（Windows）**（[frontend/main/index.ts:814-839](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L814-L839)）：
- Tauri 2.x 通过 `tauri::App::set_jump_list` API 支持（Windows 平台特性）
- 实现为 `commands::set_jump_list` 在 app setup 时调用一次

**最大化/最小化/关闭**：直接 `window.minimize()` / `window.maximize()` / `window.close()`

**最大化状态变化事件**：
- Tauri 通过 `window.on_window_event` 监听 `WindowEvent::Resized` → emit `window:maximized-change`

### D7. 前端 IPC 调用点改造（125 处 / 27 个文件）

**改造方式**：删除 preload，renderer 直接调 Tauri API + fetch。不做封装层，架构最干净。

```typescript
// 原 (Electron preload IPC 代理)
window.api.dialog.openFile(opts)
window.api.settings.getLLMConfig()

// 改后 (Tauri invoke 直调)
import { invoke } from "@tauri-apps/api/core";
await invoke("dialog_open_file", { opts })
await invoke("settings_get_llm_config")

// 原 (Electron preload HTTP 代理)
window.api.chat.send(msg, opts)
window.api.sandbox.authorize(threadId, p, writable)

// 改后 (renderer 直接 fetch)
const res = await fetch("http://127.0.0.1:8123/api/chat", { method: "POST", ... })
```

**事件订阅改造**：
```typescript
// 原
window.api.python.onStatus((status) => ...)

// 改后
import { listen } from "@tauri-apps/api/event";
const unlisten = await listen<string>("python:status", (e) => ...);
```

**调用点完整清单**（125 处，来自 Grep `window\.api\.`）：
- `frontend/renderer/components/chat/ChatView.tsx` × 11
- `frontend/renderer/components/chat/ChatComposer.tsx` × 4
- `frontend/renderer/components/chat/ApprovalDialog.tsx` × 3
- `frontend/renderer/components/chat/ContextUsage.tsx` × 1
- `frontend/renderer/components/workspace/GitPanel.tsx` × 7
- `frontend/renderer/components/workspace/FileTree.tsx` × 3
- `frontend/renderer/components/settings/ApprovalSettings.tsx` × 3
- `frontend/renderer/components/settings/McpSettings.tsx` × 9
- `frontend/renderer/components/settings/MilvusCredentialsForm.tsx` × 4
- `frontend/renderer/components/settings/ModelProviderSettings.tsx` × 9
- `frontend/renderer/components/settings/SandboxSettings.tsx` × 2
- `frontend/renderer/components/settings/SubagentsSettings.tsx` × 12
- `frontend/renderer/components/settings/SystemPromptSettings.tsx` × 3
- `frontend/renderer/components/settings/ToolsSettings.tsx` × 6
- `frontend/renderer/components/settings/LogViewer.tsx` × 1
- `frontend/renderer/components/settings/memory/PreferenceManager.tsx` × 7
- `frontend/renderer/components/settings/memory/ProfileManager.tsx` × 5
- `frontend/renderer/components/settings/memory/SessionManager.tsx` × 2
- `frontend/renderer/components/settings/memory/SkillsManager.tsx` × 4
- `frontend/renderer/components/settings/memory/ProjectMemoryManager.tsx` × 4
- `frontend/renderer/stores/chat.ts` × 3
- `frontend/renderer/stores/git.ts` × 3
- `frontend/renderer/stores/model.ts` × 6
- `frontend/renderer/stores/skills.ts` × 1
- `frontend/renderer/hooks/useChatStream.ts` × 2
- `frontend/renderer/components/ErrorBoundary.tsx` × 1
- `frontend/renderer/App.tsx` × 9

**改造规则**：
- preload IPC 代理（56 处 `ipcRenderer.invoke`）→ renderer 直接 `invoke()`
- preload HTTP 代理（19 处 `fetch`）→ renderer 直接 `fetch()`，URL 硬编码 `http://127.0.0.1:8123` 或抽到常量
- preload 事件代理（`python.onStatus` / `window.onMaximizedChange`）→ renderer 直接 `listen()`
- `window.api` 全局类型声明删除，所有 import 从 `@tauri-apps/api/*` 来

### D8. 打包与更新

**electron-builder NSIS** → `tauri build` 内置 NSIS：
```json
{
  "bundle": {
    "active": true,
    "targets": ["nsis"],
    "identifier": "com.agentx.desktop",
    "icon": ["build/icon.ico"]
  }
}
```

**electron-updater** → `tauri-plugin-updater`（本次仅配置骨架）：
```json
{
  "plugins": {
    "updater": {
      "endpoints": ["https://releases.agentx.com/{{target}}/{{arch}}/{{current_version}}"],
      "pubkey": "<占位公钥，后续 PR 生成真实密钥后替换>"
    }
  }
}
```

**签名密钥**（后续 PR 处理，本次不做）：
- 生成：`tauri signer generate -w ~/.tauri/agentx.key`
- 公钥写入 `tauri.conf.json`
- 私钥存 CI secret（`TAURI_SIGNING_PRIVATE_KEY` + `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`）

### D9. 依赖变更清单

**package.json 删除**：
```json
{
  "dependencies": {
    "dugite": "^3.2.2",          // 改 git2 crate
    "electron-store": "^10.0.0"  // 改 tauri-plugin-store
  },
  "devDependencies": {
    "electron": "^32.0.0",        // 不需要
    "electron-builder": "^25.0.0", // 不需要
    "electron-updater": "^6.3.0",  // 不需要
    "electron-vite": "^2.3.0",     // 不需要
    "rcedit": "^5.0.2"             // Tauri 内置
  }
}
```

**package.json 新增**：
```json
{
  "dependencies": {
    "@tauri-apps/api": "^2.0.0",
    "@tauri-apps/plugin-store": "^2.0.0",
    "@tauri-apps/plugin-stronghold": "^2.0.0",
    "@tauri-apps/plugin-updater": "^2.0.0",
    "@tauri-apps/plugin-dialog": "^2.0.0",
    "@tauri-apps/plugin-fs": "^2.0.0",
    "@tauri-apps/plugin-shell": "^2.0.0",
    "@tauri-apps/plugin-notification": "^2.0.0",
    "@tauri-apps/plugin-clipboard-manager": "^2.0.0",
    "@tauri-apps/plugin-os": "^2.0.0",
    "@tauri-apps/plugin-process": "^2.0.0"
  },
  "devDependencies": {
    "@tauri-apps/cli": "^2.0.0"
  }
}
```

**Cargo.toml 新增**：
```toml
[dependencies]
tauri = { version = "2", features = ["protocol-asset"] }
tauri-plugin-store = "2"
tauri-plugin-stronghold = "2"
tauri-plugin-updater = "2"
tauri-plugin-dialog = "2"
tauri-plugin-fs = "2"
tauri-plugin-shell = "2"
tauri-plugin-notification = "2"
tauri-plugin-clipboard-manager = "2"
tauri-plugin-os = "2"
tauri-plugin-process = "2"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tokio = { version = "1", features = ["full"] }
reqwest = { version = "0.12", features = ["json"] }
git2 = "0.19"
anyhow = "1"
thiserror = "2"
chrono = { version = "0.4", features = ["serde"] }
uuid = { version = "1", features = ["v4", "serde"] }
log = "0.4"
env_logger = "0.11"
```

### D10. 文件删除清单

```
DELETE frontend/main/index.ts                 (855 行 TS → src-tauri/src/main.rs + commands/)
DELETE frontend/main/store.ts                 (electron-store + safeStorage → stronghold)
DELETE frontend/main/logger.ts                (Rust 日志重写)
DELETE frontend/main/python/spawn.ts          (tokio::process::Command)
DELETE frontend/preload/index.ts              (514 行 contextBridge → renderer 直调 invoke+fetch)
DELETE electron.vite.config.ts                (三入口 → 单 vite.config.ts)
DELETE scripts/patch-electron-icon.cjs        (Tauri 不需要)
DELETE scripts/_verify_taskbar/               (Tauri AppUserModelID 自动处理)
DELETE scripts/_tmp_check_taskbar.py          (调试脚本)
DELETE scripts/register-appuser.ps1           (Tauri 自动处理 AppUserModelID)
```

> **注意**：[frontend/main/store.ts](file:///d:/java/agentprojects/agentx/frontend/main/store.ts) 中的 `migrateLegacyLLMConfig()` 逻辑（把 legacy `llm.defaultModel` + `apikey.*` 种子为 model entries）必须在 Tauri 迁移脚本中保留，否则老用户升级后已配置的模型会丢失。

### D11. AGENTS.md 更新

- §10 技术栈表格：Electron 行替换为 Tauri 2.x + Rust
- §11 文件地图：删除 `frontend/main/` 和 `frontend/preload/`，新增 `src-tauri/`
- §14.2 Electron ↔ 后端进程：替换为 Tauri spawn 章节
- §14.7 重启前后端 SOP：Tauri 适配（清理 tauri.exe + uv + python 父子链）

### D12. 凭证数据迁移脚本

启动时在 `setup()` hook 中执行：
```rust
fn setup(app: &mut App) -> Result<(), Box<dyn std::error::Error>> {
    // 1. 检测旧 electron-store 文件（new Store() 默认路径，文件名 config.json）
    //    Windows: %APPDATA%/agentx/config.json
    //    macOS:   ~/Library/Application Support/agentx/config.json
    //    Linux:   ~/.config/agentx/config.json
    let legacy_path = app.path().app_config_dir()?.join("config.json");
    if legacy_path.exists() {
        // 2. 读取所有 key
        let data: serde_json::Value = serde_json::from_str(&fs::read_to_string(&legacy_path)?)?;
        // 3. 仅迁移明文值（plain: 前缀 + 裸字符串），enc: 值提示用户重新输入
        // 4. 写入 tauri-plugin-store
        // 5. 备份原文件为 .migrated
        fs::rename(&legacy_path, legacy_path.with_extension("json.migrated"))?;
    }
    // 6. 复现 migrateLegacyLLMConfig：若 models.entries 为空但 llm.defaultModel + apikey.* 存在，
    //    种子一条默认 model entry，避免老用户升级后丢失已配置的模型
    migrate_legacy_llm_config(app)?;
    Ok(())
}
```

### D13. preload HTTP 代理调用处理（全部直调）

preload 中有 19 处 `fetch(${API_BASE}/api/...)` 代理调用后端 HTTP API，不走 IPC。迁移后 renderer 直接 `fetch()`，不做封装层。

**涉及的 8 个命名空间**：
| preload 命名空间 | 后端 API 路径 | renderer 改造方式 |
|---|---|---|
| `chat` | `/api/chat`、`/api/chat/abort`、`/api/chat/compact` | renderer 直接 fetch + SSE stream 解析 |
| `sandbox` | `/api/sandbox/authorize`、`/api/sandbox/revoke`、`/api/sandbox/authorized/:id` | renderer 直接 fetch |
| `skills` | `/api/skills`、`/api/skills/reload` | renderer 直接 fetch |
| `workspace` | `/api/workspace/list` | renderer 直接 fetch |
| `approve` | `/api/chat/approve` | renderer 直接 fetch |
| `health` | `/api/health` | renderer 直接 fetch |
| `mcp` | `/api/mcp/*` | renderer 直接 fetch |
| `memory` | `/api/memory/*` | renderer 直接 fetch |

**API_BASE 常量**：renderer 中定义 `const API_BASE = "http://127.0.0.1:8123"`（与 preload 一致），所有 fetch 调用复用。

**SSE stream 解析**：preload 中 `streamChat()` 函数的 SSE 解析逻辑（`event:` / `data:` 行解析 + JSON parse + 事件分发）需迁移到 renderer 的 `useChatStream` hook 或独立工具函数。

### D14. updater 仅配置骨架（不实现完整链路）

本次迁移只配置 `tauri-plugin-updater` 骨架，不实现完整更新链路：
- `tauri.conf.json::plugins.updater`：endpoints 占位 + pubkey 占位
- `Cargo.toml`：引入 `tauri-plugin-updater` 依赖
- `src-tauri/src/lib.rs`：注册 `tauri_plugin_updater::Builder::new().build()`
- **不做**：生成签名密钥对、配置 CI workflow、搭建 releases 端点、staging 验证

后续 PR 处理完整链路时，替换 pubkey + 配 CI + 搭端点即可。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| Rust 编译时间长（首次 3-5 分钟） | CI 缓存 `target/`；本地用 `cargo check` 而非 `cargo build` |
| tauri-plugin-stronghold 跨平台差异 | 锁版本 + Windows 完整冒烟 |
| safeStorage 加密值无法自动迁移 | 启动时检测到 `enc:` 值 → 弹窗提示用户重新输入 |
| git2 某些边缘 case 行为与 dugite 不一致 | 8 个 Git 命令逐个 e2e 测试覆盖 |
| Windows WebView2 Runtime 缺失 | README 加说明 + 安装包检测 |
| 一次性迁移无回退路径 | main 分支保持可运行 Electron 状态直到合并；worktree 隔离 |
| Tauri 命令权限配置错误 | capabilities/default.json 显式列出所有命令，权限最小化 |

## Migration Plan

1. 创建 `.worktrees/tauri-migration/` 隔离工作区
2. 初始化 `src-tauri/` Rust 项目骨架（`tauri init`）
3. 迁移 `electron-store` → `tauri-plugin-store`（最小命令验证）
4. 迁移 Python spawn（启动 + 健康检查）
5. 迁移 settings 命令（凭证 + 配置 CRUD，14 个）
6. 迁移 dialog / shell / window / clipboard / notify / logs 命令
7. 迁移 Git 8 个命令（git2 crate）
8. 迁移事件订阅（python:status / window:maximized-change）
9. 前端 `window.api.*` → `invoke()` 改造（25+ 处）
10. 凭证数据迁移脚本（electron-store → stronghold）
11. 打包脚本（tauri.conf.json + bundler 配置）
12. 自动更新插件配置（tauri-plugin-updater）
13. 全量回归冒烟脚本
14. 更新 AGENTS.md / OpenSpec 归档
15. 删除 Electron 残留文件
16. 主分支合并 + 删除 worktree

## Open Questions

| # | 问题 | 默认假设 |
|---|---|---|
| Q1 | macOS / Linux 是否本次同步交付 | 否，后续 PR |
| Q2 | safeStorage 加密值是否尝试解密迁移 | 否，仅迁移明文 |
| Q3 | 是否使用 Tauri 2.x 最新 stable | 是（2026-07 当前 stable） |
| Q4 | tauri-plugin-updater 签名密钥如何管理 | 本次仅配置骨架（pubkey 占位），后续 PR 生成密钥 + 配 CI + 搭端点 |
| Q5 | GitPanel 是否做完整 UI 测试 | 否，依赖手动 e2e 验证 |