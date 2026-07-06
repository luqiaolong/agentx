# Tasks: Electron → Tauri 2.x 全量替换

> **执行原则**：每个 Phase 完成后必须跑对应验证命令；未通过的 Phase 不进入下一阶段。
> **隔离原则**：所有改动在 `.worktrees/tauri-migration/` 进行，main 分支保持可运行的 Electron 状态直到合并。

## Phase 0: Worktree 隔离 + 环境准备

- [ ] 创建 worktree：`git worktree add .worktrees/tauri-migration -b feature/tauri-migration`
- [ ] 安装 Rust 工具链（如未安装）：`rustup install stable && rustup default stable`
- [ ] 安装 Tauri CLI：`cargo install tauri-cli --version "^2.0"` 或 `npm install -D @tauri-apps/cli`
- [ ] 安装 WebView2 Runtime（Windows）：下载 Evergreen Bootstrapper 或通过 Edge 自动安装
- [ ] 验证 Rust 环境：`cargo --version && rustc --version`

**Phase 0 验证**：`rustc --version` 返回 stable 版本号

---

## Phase 1: Tauri 项目骨架初始化

- [ ] 在 worktree 根目录执行 `npm install` 确保前端依赖最新
- [ ] 创建 `src-tauri/` 目录结构：
  ```
  src-tauri/
  ├── Cargo.toml
  ├── tauri.conf.json
  ├── build.rs
  ├── capabilities/
  │   └── default.json
  ├── icons/                   ← 从 build/icon.ico 复制 + 生成 Tauri 所需的多尺寸
  └── src/
      ├── main.rs              ← 入口（创建 App + 注册 commands）
      └── lib.rs               ← run() 函数（Tauri 2.x 推荐模式）
  ```
- [ ] 初始化 `Cargo.toml`：包含 tauri + 必要插件依赖
- [ ] 初始化 `tauri.conf.json`：
  - `productName: "AgentX"`
  - `version: "0.2.0"`（bump 版本区分 Electron 旧版）
  - `identifier: "com.agentx.desktop"`
  - `bundle.targets: ["nsis"]`
  - `app.windows[0]: { width: 1280, height: 800, decorations: false, transparent: false, backgroundColor: "#020617" }`
- [ ] 初始化 `capabilities/default.json`：声明 `core:default` 权限
- [ ] 创建最小化 `src-tauri/src/main.rs`：
  ```rust
  fn main() {
      agentx_lib::run();
  }
  ```
- [ ] 创建最小化 `src-tauri/src/lib.rs`：
  ```rust
  #[cfg_attr(mobile, tauri::mobile_entry_point)]
  pub fn run() {
      tauri::Builder::default()
          .plugin(tauri_plugin_os::init())
          .plugin(tauri_plugin_process::init())
          .plugin(tauri_plugin_dialog::init())
          .plugin(tauri_plugin_shell::init())
          .plugin(tauri_plugin_notification::init())
          .plugin(tauri_plugin_clipboard_manager::init())
          .plugin(tauri_plugin_fs::init())
          .plugin(tauri_plugin_store::Builder::new().build())
          .plugin(tauri_plugin_stronghold::Builder::new().build())
          .plugin(tauri_plugin_updater::Builder::new().build())
          .run(tauri::generate_context!())
          .expect("error while running tauri application");
  }
  ```
- [ ] 复制图标：把 `build/icon.ico` 复制到 `src-tauri/icons/icon.ico`，生成多尺寸 PNG（32x32, 128x128, 128x128@2x, icon.icns）
- [ ] 创建简化版 `vite.config.ts`（去掉 electron-vite 三入口）：
  ```ts
  import { resolve } from 'node:path'
  import react from '@vitejs/plugin-react'
  import tailwindcss from '@tailwindcss/vite'
  
  export default {
    root: resolve(__dirname, 'frontend/renderer'),
    resolve: { alias: { '@': resolve(__dirname, 'frontend/renderer') } },
    plugins: [react(), tailwindcss()],
  }
  ```

**Phase 1 验证**：
```bash
cd src-tauri && cargo check
```
返回 `Finished` 无 error。前端 `npm run dev`（纯 vite）能打开空白 React 页面。

---

## Phase 2: Python 后端 spawn

- [ ] 创建 `src-tauri/src/backend/mod.rs`：定义 `PythonHandle` 结构体（start/stop/waitForReady/onStatus）
- [ ] 创建 `src-tauri/src/backend/env.rs`：实现 `build_env()`：
  - 从 `tauri-plugin-store` 读所有凭证（`store::credentials::get_credential`：milvus.user/password, openai/anthropic/deepseek/tavily api keys）
  - 从 tauri-plugin-store 读所有配置（llm.*, approval.*, knowledge.*, subagents_config, custom_subagents_config, tools_config, profile_auto_extract, mcp_servers_config）
  - 拼装为 `HashMap<String, String>`（key 加 `AGENTX_` 前缀）
- [ ] 创建 `src-tauri/src/backend/handle.rs`：
  ```rust
  pub struct PythonHandle {
      child: Option<Child>,
      port: u16,
      status_tx: mpsc::Sender<PythonStatus>,
  }
  
  impl PythonHandle {
      pub async fn start(cwd: PathBuf, port: u16, env: HashMap<String, String>) -> Result<Self> {
          let cmd = if which("uv").is_ok() {
              Command::new("uv").args(["run", "python", "-m", "app.main"])
          } else {
              Command::new("python").args(["-m", "app.main"])
          };
          // spawn + 状态推送 + 握手
      }
      pub async fn wait_for_ready(&self, timeout: Duration) -> bool { ... }
      pub fn stop(&mut self) { ... }
  }
  ```
- [ ] 创建 `src-tauri/src/commands/python_status.rs`：
  ```rust
  #[tauri::command]
  pub async fn python_status(app: AppHandle) -> Result<(), String> { ... }
  ```
- [ ] 修改 `src-tauri/src/lib.rs`：在 `setup()` hook 中启动 Python：
  ```rust
  .setup(|app| {
      let handle = app.handle().clone();
      tokio::spawn(async move {
          let env = backend::env::build_env(&handle).await?;
          let mut py = backend::handle::PythonHandle::start(...).await?;
          // 启动 + emit("python:status", ...)
      });
      Ok(())
  })
  ```

**Phase 2 验证**：
- `cargo build` 成功
- `npm run tauri dev` 启动后 Python 后端能正常监听 8123
- 前端 fetch `http://127.0.0.1:8123/` 返回 200

---

## Phase 3: 配置存储（tauri-plugin-store）

- [ ] 创建 `src-tauri/src/store/mod.rs`：封装 tauri-plugin-store，统一 key 前缀管理
- [ ] 创建 `src-tauri/src/store/credentials.rs`：封装 tauri-plugin-store 凭证读写（`enc:`/`plain:` 前缀格式）：
  ```rust
  pub fn get_credential(app: &AppHandle, name: &str) -> Option<String>;
  pub fn set_credential(app: &AppHandle, name: &str, value: &str);
  ```
  > **注**：不使用 `tauri-plugin-stronghold`（v2.3.1 无公开 Rust runtime API）。stronghold 插件仅注册不用于凭证存储。
- [ ] 实现 `load_config()` 和 `save_config()` 函数（使用 tauri-plugin-store）：
  ```rust
  pub struct AppConfig {
      pub llm: LlmConfig,
      pub approval: ApprovalConfig,
      pub knowledge: KnowledgeConfig,
      pub subagents: SubagentsConfig,
      // ...
  }
  ```

**Phase 3 验证**：单元测试覆盖 `load_config()` / `save_config()` 序列化往返。

---

## Phase 4: Settings 命令（凭证 + 配置 CRUD，28 个）

- [ ] 创建 `src-tauri/src/commands/settings.rs`：实现 28 个 settings command
  | Tauri command | 对应原 IPC handler |
  |---|---|
  | `settings_get_milvus_credentials` | `settings:getMilvusCredentials` |
  | `settings_set_milvus_credentials` | `settings:setMilvusCredentials` |
  | `settings_get_api_key` | `settings:getApiKey` |
  | `settings_set_api_key` | `settings:setApiKey` |
  | `settings_get_llm_config` / `settings_set_llm_config` | `settings:getLLMConfig` / `settings:setLLMConfig` |
  | `settings_get_system_prompt` / `settings_set_system_prompt` | `settings:getSystemPrompt` / `settings:setSystemPrompt` |
  | `settings_get_approval_config` / `settings_set_approval_config` | `settings:getApprovalConfig` / `settings:setApprovalConfig` |
  | `settings_get_knowledge_config` / `settings_set_knowledge_config` | `settings:getKnowledgeConfig` / `settings:setKnowledgeConfig` |
  | `settings_get_subagents_config` / `settings_set_subagents_config` | `settings:getSubagentsConfig` / `settings:setSubagentsConfig` |
  | `settings_get_team_subagents_config` / `settings_set_team_subagents_config` | `settings:getTeamSubagentsConfig` / `settings:setTeamSubagentsConfig` |
  | `settings_get_custom_subagents` / `settings_set_custom_subagents` | `settings:getCustomSubagents` / `settings:setCustomSubagents` |
  | `settings_add_custom_subagent` / `settings_remove_custom_subagent` | `settings:addCustomSubagent` / `settings:removeCustomSubagent` |
  | `settings_get_tools_config` / `settings_set_tools_config` | `settings:getToolsConfig` / `settings:setToolsConfig` |
  | `settings_get_profile_auto_extract` / `settings_set_profile_auto_extract` | `settings:getProfileAutoExtract` / `settings:setProfileAutoExtract` |
  | `settings_get_mcp_servers_config` / `settings_set_mcp_servers_config` | `settings:getMcpServersConfig` / `settings:setMcpServersConfig` |
  | `settings_get_model_entries` / `settings_set_model_entries` | `settings:getModelEntries` / `settings:setModelEntries` |
  | `settings_get_active_model_id` / `settings_activate_model` | `settings:getActiveModelId` / `settings:activateModel` |
- [ ] 每个 command 内部调用 `store::credentials::*` 和 `store::*`
- [ ] 更新 `capabilities/default.json`：在 `permissions` 数组中加入所有 settings_* 权限

**Phase 4 验证**：
- 单元测试覆盖每个 command（mock AppHandle + Store）
- 集成测试：启动 tauri dev → 通过 invoke 测试 28 个 settings 端点

---

## Phase 5: 系统命令（dialog / shell / window / clipboard / notify / logs / app）

- [ ] 创建 `src-tauri/src/commands/dialog.rs`：4 个命令
  - `dialog_open_file` / `dialog_open_folder` / `dialog_save_file` / `dialog_save_dropped_file`
  - `save_dropped_file` 复用 [frontend/main/index.ts:242-288](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L242-L288) 的系统目录黑名单 + 大小限制 + 路径清洗逻辑
- [ ] 创建 `src-tauri/src/commands/shell.rs`：3 个命令
  - `shell_reveal_in_folder`（对应 `shell:revealInFolder`）
  - `shell_open_in_editor`（对应 `shell:openInEditor`，用 `tauri-plugin-shell` 的 `open` 或 `std::process::Command` 调系统默认编辑器）
  - `shell_open_external`（对应 `shell:openExternal`，用 `tauri-plugin-shell` 的 `open` 打开 URL）
- [ ] 创建 `src-tauri/src/commands/window.rs`：4 个命令 + 1 个事件
  - `window_minimize` / `window_maximize` / `window_close` / `window_is_maximized`
  - 监听 `WindowEvent::Resized` → emit `window:maximized-change`
- [ ] 创建 `src-tauri/src/commands/clipboard.rs`：2 个命令
- [ ] 创建 `src-tauri/src/commands/notify.rs`：1 个命令
- [ ] 创建 `src-tauri/src/commands/logs.rs`：1 个命令 + 日志清理（参考 [frontend/main/logger.ts](file:///d:/java/agentprojects/agentx/frontend/main/logger.ts)）
- [ ] 创建 `src-tauri/src/commands/app.rs`：7 个命令
  - `app_get_version` / `app_quit` / `app_restart` / `app_restart_backend` / `app_reload_backend_config` / `app_init_agents_md` / `app_get_home_workspace_dir`

**Phase 5 验证**：每个 command 端到端测试，覆盖错误路径（如取消对话框、文件不存在等）

---

## Phase 6: Git 命令（8 个，git2 crate）

- [ ] 创建 `src-tauri/src/git/mod.rs`：定义 `GitStatus`, `GitCommit`, `GitBranch`, `GitOpResult` 类型（对照 [frontend/shared/api-types.ts](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts)）
- [ ] 创建 `src-tauri/src/git/status.rs`：实现 `git_get_status`
  - 使用 `repo.statuses(None)` + `repo.head()` 解析
  - 对照 [frontend/main/index.ts:566-681](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L566-L681) 的解析逻辑（branch/ahead/behind/entries）
- [ ] 创建 `src-tauri/src/git/log.rs`：实现 `git_get_log`（`repo.revwalk()` + `commit.message()`）
- [ ] 创建 `src-tauri/src/git/branches.rs`：实现 `git_get_branches`（`repo.branches(None)`）
- [ ] 创建 `src-tauri/src/git/checkout.rs`：实现 `git_checkout`（`repo.checkout_tree()` + `repo.set_head()`）
- [ ] 创建 `src-tauri/src/git/stage.rs`：实现 `git_stage` / `git_unstage` / `git_discard_changes`
- [ ] 创建 `src-tauri/src/git/commit.rs`：实现 `git_commit`（`repo.commit()`）
- [ ] 创建 `src-tauri/src/git/diff.rs`：实现 `git_get_diff`（`repo.diff(...).to_buf()`）
- [ ] 创建 `src-tauri/src/commands/git.rs`：8 个 #[tauri::command] 包装

**Phase 6 验证**：
- 单元测试：用 fixture repo（git init + sample commits）覆盖每个命令
- 集成测试：手动 e2e 跑 GitPanel（前端 + 后端联动）

---

## Phase 7: 前端 IPC 调用点改造（125 处 / 27 个文件）

- [ ] 删除 `frontend/preload/index.ts`（514 行）
- [ ] 修改 `frontend/shared/api-types.ts`：删除 `window` 全局声明 + `ElectronAPI` 接口（保留其他类型导出）
- [ ] 在 `frontend/renderer/lib/api-constants.ts` 定义 `export const API_BASE = "http://127.0.0.1:8123"` 常量
- [ ] 改造 preload IPC 代理调用（56 处）→ renderer 直接 `invoke()`：
  - `frontend/renderer/components/chat/ChatView.tsx`（11 处）
  - `frontend/renderer/components/chat/ChatComposer.tsx`（4 处）
  - `frontend/renderer/components/chat/ApprovalDialog.tsx`（3 处）
  - `frontend/renderer/components/chat/ContextUsage.tsx`（1 处）
  - `frontend/renderer/components/workspace/GitPanel.tsx`（7 处）
  - `frontend/renderer/components/workspace/FileTree.tsx`（3 处）
  - `frontend/renderer/components/settings/ApprovalSettings.tsx`（3 处）
  - `frontend/renderer/components/settings/McpSettings.tsx`（9 处）
  - `frontend/renderer/components/settings/MilvusCredentialsForm.tsx`（4 处）
  - `frontend/renderer/components/settings/ModelProviderSettings.tsx`（9 处）
  - `frontend/renderer/components/settings/SandboxSettings.tsx`（2 处）
  - `frontend/renderer/components/settings/SubagentsSettings.tsx`（12 处）
  - `frontend/renderer/components/settings/SystemPromptSettings.tsx`（3 处）
  - `frontend/renderer/components/settings/ToolsSettings.tsx`（6 处）
  - `frontend/renderer/components/settings/LogViewer.tsx`（1 处）
  - `frontend/renderer/components/settings/memory/PreferenceManager.tsx`（7 处）
  - `frontend/renderer/components/settings/memory/ProfileManager.tsx`（5 处）
  - `frontend/renderer/components/settings/memory/SessionManager.tsx`（2 处）
  - `frontend/renderer/components/settings/memory/SkillsManager.tsx`（4 处）
  - `frontend/renderer/components/settings/memory/ProjectMemoryManager.tsx`（4 处）
  - `frontend/renderer/stores/chat.ts`（3 处）
  - `frontend/renderer/stores/git.ts`（3 处）
  - `frontend/renderer/stores/model.ts`（6 处）
  - `frontend/renderer/stores/skills.ts`（1 处）
  - `frontend/renderer/hooks/useChatStream.ts`（2 处）
  - `frontend/renderer/components/ErrorBoundary.tsx`（1 处）
  - `frontend/renderer/App.tsx`（9 处）
- [ ] 改造 preload HTTP 代理调用（19 处）→ renderer 直接 `fetch()`：
  - `chat.send/abort/compact` → renderer 直接 fetch + SSE stream 解析（迁移 `streamChat()` 逻辑到 `useChatStream`）
  - `sandbox.*` / `skills.*` / `workspace.*` / `approve.*` / `health.*` / `mcp.*` / `memory.*` → renderer 直接 fetch
- [ ] 改造 preload 事件代理 → renderer 直接 `listen()`：
  - `python.onStatus` → `listen<string>("python:status", ...)`
  - `window.onMaximizedChange` → `listen<boolean>("window:maximized-change", ...)`
- [ ] 全局 grep `window.api.` → 应为空（验证 100% 迁移）
- [ ] 全局 grep `from "electron"` → 应为空（验证无 Electron 残留）

**Phase 7 验证**：
- `npm run typecheck` 通过
- `npm test` 全部 vitest 通过
- `cargo check` + 前端 dev server 启动正常

---

## Phase 8: 凭证数据迁移脚本

- [ ] 创建 `src-tauri/src/migration/mod.rs`：实现 `migrate_electron_store()` 函数
  ```rust
  pub async fn migrate_electron_store(app: &AppHandle) -> Result<MigrationReport, String> {
      let config_dir = app.path().app_config_dir()?;
      // electron-store new Store() 默认文件名 config.json（不是 agentx-config.json）
      let legacy_path = config_dir.join("config.json");
      if !legacy_path.exists() {
          return Ok(MigrationReport::default());
      }
      let content = fs::read_to_string(&legacy_path)?;
      let data: serde_json::Map<String, serde_json::Value> = serde_json::from_str(&content)?;
      let mut report = MigrationReport::default();
      for (key, value) in data {
          let raw = match value {
              serde_json::Value::String(s) => s,
              _ => continue, // 跳过非字符串值
          };
          // 区分明文 / enc: / plain: 前缀
          if let Some(stripped) = raw.strip_prefix("enc:") {
              // enc: 值无法自动解密 → 记录需重输
              report.requires_reinput.push(key.clone());
          } else {
              let plain = raw.strip_prefix("plain:").unwrap_or(&raw);
              store::set(app, &key, plain).await?;
              report.migrated.push(key);
          }
      }
      // 备份原文件
      fs::rename(&legacy_path, legacy_path.with_extension("json.migrated"))?;
      Ok(report)
  }
  ```
- [ ] 实现 `migrate_legacy_llm_config()`：复现 [frontend/main/store.ts](file:///d:/java/agentprojects/agentx/frontend/main/store.ts) `migrateLegacyLLMConfig()` 逻辑
  - 若 `models.entries` 为空但 `llm.defaultModel` + `apikey.*` 存在 → 种子一条默认 model entry
  - 推断 provider（deepseek/openai/minimax/custom）
  - 写入 `models.entries` + `models.activeId`
- [ ] 在 `setup()` hook 中按顺序调用 `migrate_electron_store()` → `migrate_legacy_llm_config()` 并 emit 结果事件
- [ ] 前端 settings 页监听迁移结果，对 `requires_reinput` 的 key 显示提示

**Phase 8 验证**：
- 单元测试：构造伪造的 `config.json`（明文 + enc: + plain: 混合）→ 跑迁移 → 验证 tauri-plugin-store 数据正确
- 单元测试：构造 legacy `llm.defaultModel` + `apikey.openai` → 跑 `migrate_legacy_llm_config` → 验证 model entries 种子正确
- 集成测试：手动从 Electron 版本生成配置 → 用 Tauri 启动 → 验证迁移报告

---

## Phase 9: 打包与自动更新（updater 仅配置骨架）

- [ ] 配置 `tauri.conf.json` 的 `bundle` 段：
  ```json
  {
    "bundle": {
      "active": true,
      "targets": ["nsis"],
      "icon": ["build/icon.ico"],
      "publisher": "AgentX",
      "shortDescription": "本地优先的个人助理桌面应用",
      "longDescription": "..."
    }
  }
  ```
- [ ] 配置 `tauri.conf.json::plugins.updater`（**仅骨架，pubkey 占位**）：
  ```json
  {
    "endpoints": ["https://releases.agentx.com/{{target}}/{{arch}}/{{current_version}}"],
    "pubkey": "<占位公钥，后续 PR 生成真实密钥后替换>",
    "windows": { "installMode": "passive" }
  }
  ```
- [ ] **不做**（后续 PR 处理）：
  - 生成 updater 签名密钥对 `tauri signer generate`
  - CI 配置 `.github/workflows/release.yml`
  - 搭建 releases.agentx.com 端点
  - staging 环境验证更新链
- [ ] 更新 `package.json` scripts（清理 Electron 专用脚本）：
  ```json
  {
    "dev": "tauri dev",
    "build": "tauri build",
    "dist:win": "tauri build --target nsis",
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck:node": "tsc --noEmit -p tsconfig.node.json",
    "typecheck:web": "tsc --noEmit -p tsconfig.web.json",
    "typecheck": "npm run typecheck:node && npm run typecheck:web"
  }
  ```
  删除：`preview` / `icons` / `patch-icon` / `predev` / `pack` / `dist` / `dist:mac` / `dist:linux`
- [ ] 删除 `scripts/patch-electron-icon.cjs`（Tauri 不需要 patch electron.exe）
- [ ] 删除 `package.json` 的 `build` 段（electron-builder 配置）

**Phase 9 验证**：
- `npm run tauri build` 生成 `AgentX_0.2.0_x64-setup.exe` ≤ 20MB
- `tauri.conf.json::plugins.updater` 配置存在且 pubkey 为占位值
- `package.json` 中无 `electron` / `electron-vite` / `electron-builder` / `electron-updater` / `electron-store` / `dugite` / `rcedit` 依赖

---

## Phase 10: 全量回归冒烟

- [ ] 创建 `scripts/smoke-tauri.sh`（Windows 改 .ps1）：
  ```bash
  #!/bin/bash
  set -e
  echo "=== Tauri Migration Smoke Test ==="
  
  # 1. 启动应用
  echo "1. 启动 Tauri 应用..."
  npm run tauri dev &
  APP_PID=$!
  sleep 15
  
  # 2. 验证后端健康
  echo "2. 验证后端健康..."
  curl -fsS http://127.0.0.1:8123/ || (kill $APP_PID && exit 1)
  
  # 3. 验证 settings 命令
  echo "3. 验证 settings 命令..."
  # (手动触发或编写 invoke 测试)
  
  # 4. 验证 Git 命令（fixture repo）
  echo "4. 验证 Git 命令..."
  mkdir /tmp/agentx-fixture && cd /tmp/agentx-fixture && git init && echo test > a.txt
  # (触发 git_get_status 验证)
  
  # 5. 验证打包
  echo "5. 验证打包..."
  npm run tauri build
  
  # 6. 清理
  kill $APP_PID
  echo "=== All smoke tests passed ==="
  ```
- [ ] 执行冒烟脚本，确认 25+ 关键场景通过
- [ ] 备份现有 Electron 配置，测试凭证迁移（明文 + enc: 混合）
- [ ] 手动测试所有 25+ 处 `window.api.*` 调用点，确认替换后功能不变

**Phase 10 验证**：冒烟脚本全绿

---

## Phase 11: 文档更新

- [ ] 更新 [AGENTS.md](file:///d:/java/agentprojects/agentx/AGENTS.md)：
  - §10 技术栈表格：Electron 行替换为 Tauri 2.x + Rust
  - §11 文件地图：删除 `frontend/main/` + `frontend/preload/`，新增 `src-tauri/`
  - §14.2 Electron ↔ 后端进程：替换为 Tauri spawn 章节
  - §14.7 重启前后端 SOP：清理 tauri.exe + uv + python 父子链
- [ ] 更新 [README.md](file:///d:/java/agentprojects/agentx/README.md)：补充 Tauri + WebView2 环境要求
- [ ] 更新 [.env.example](file:///d:/java/agentprojects/agentx/.env.example)：补充 `TAURI_SIGNING_PRIVATE_KEY` 等
- [ ] 归档 OpenSpec：`openspec archive 2026-07-06-tauri-migration`

**Phase 11 验证**：grep `electron` 在 AGENTS.md 中应仅在历史章节出现，无遗留实现指引

---

## Phase 12: 清理 + 合并

- [ ] 删除文件：
  - `frontend/main/` 整个目录
  - `frontend/preload/` 整个目录
  - `electron.vite.config.ts`
  - `scripts/patch-electron-icon.cjs`
  - `scripts/_verify_taskbar/`
  - `scripts/_tmp_check_taskbar.py`
  - `scripts/register-appuser.ps1`（Tauri 自动处理 AppUserModelID）
- [ ] 修改 `package.json`：
  - 删除依赖：`electron` / `electron-vite` / `electron-builder` / `electron-updater` / `electron-store` / `dugite` / `rcedit`
  - 添加依赖：`@tauri-apps/api` / `@tauri-apps/plugin-store` / `@tauri-apps/plugin-stronghold` / `@tauri-apps/plugin-dialog` / `@tauri-apps/plugin-fs` / `@tauri-apps/plugin-shell` / `@tauri-apps/plugin-notification` / `@tauri-apps/plugin-clipboard-manager` / `@tauri-apps/plugin-os` / `@tauri-apps/plugin-process` / `@tauri-apps/plugin-updater`
  - 添加 devDependency：`@tauri-apps/cli`
  - scripts 中 `dev` / `build` 改为 `tauri` 形式（详见 Phase 9）
- [ ] 删除 `package.json` 中的 `build` 段（electron-builder 配置）
- [ ] 跑 `npm install` 清理 node_modules
- [ ] 全量 grep 验证：`grep -r "electron" frontend/ src-tauri/` 应为空
- [ ] 全量 grep 验证：`grep -r "window\.api" frontend/renderer/` 应为空
- [ ] 跑全量测试：`cargo test` + `npm test` + `npm run tauri build`
- [ ] 提交 worktree 分支：`git add -A && git commit -m "feat(desktop): migrate from Electron to Tauri 2.x"`
- [ ] 合并到 main 分支：`git checkout main && git merge feature/tauri-migration`
- [ ] 删除 worktree：`git worktree remove .worktrees/tauri-migration`
- [ ] 推送：`git push origin main`

**Phase 12 验证**：
- `git log --oneline -5` 显示迁移 commit
- `git worktree list` 不再有 tauri-migration
- main 分支 `npm run tauri build` 仍能正常工作

---

## 风险检查清单（贯穿所有 Phase）

- [ ] **Phase 1-2**：Rust 编译时间 < 5 分钟（首次）；CI 用 cache
- [ ] **Phase 3-6**：所有 Tauri command 在 `capabilities/default.json` 中声明
- [ ] **Phase 7**：125 处 `window.api.*` 调用点全部改造 + 19 处 HTTP 代理改为直调 fetch
- [ ] **Phase 8**：迁移脚本幂等（重复执行不会丢失数据）+ `migrate_legacy_llm_config` 逻辑保留
- [ ] **Phase 9**：updater 仅骨架配置，pubkey 为占位值（不生成真实密钥）
- [ ] **Phase 10**：冒烟脚本覆盖所有 61 个 command + 19 个 HTTP 端点
- [ ] **Phase 11**：文档无遗留 Electron 实现指引
- [ ] **Phase 12**：main 分支 0 个 Electron 残留 + 0 个 `window.api` 引用

---

## 验收总览

| 指标 | 目标 | 验证方式 |
|---|---|---|
| 安装包体积 | ≤ 20MB | `ls -la src-tauri/target/release/bundle/nsis/*.exe` |
| 主进程内存 | ≤ 80MB | Task Manager 观测 |
| 冷启动时间 | ≤ 1s | Stopwatch 测启动到 ready-to-show |
| 61 个 IPC handler | 100% 迁移 | grep `ipcMain.handle` = 0 处 |
| 125 处 `window.api.*` 调用点 | 100% 迁移 | grep `window.api.` in frontend/renderer/ = 0 处 |
| 19 处 preload HTTP 代理 | 100% 改为 renderer 直调 fetch | grep `API_BASE` in frontend/preload/ = 0 处（preload 已删除） |
| Electron 依赖 | 0 个 | grep `"electron` in package.json = 0 处 |
| dugite 依赖 | 0 个 | grep `dugite` = 0 处 |
| safeStorage | 0 个引用 | grep `safeStorage` = 0 处 |
| migrateLegacyLLMConfig 逻辑 | 已保留 | grep `migrate_legacy_llm_config` in src-tauri/ = 1+ 处 |
| updater pubkey | 占位值 | tauri.conf.json::plugins.updater.pubkey 不为真实密钥 |
| 单元测试 | 全绿 | `cargo test && npm test` |
| 冒烟脚本 | 全绿 | `bash scripts/smoke-tauri.sh` |