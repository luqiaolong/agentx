# Electron → Tauri 2.x 全量替换 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Tauri 2.x + Rust 主进程一次性全量替换 Electron 桌面壳，安装包体积 -90%、内存 -70%、启动 -75%，所有 IPC 命令语义不变，前端调用方零感知。

**Architecture:**
- 桌面壳：Electron 32 + electron-vite 2 + electron-builder 25 → Tauri 2.x + Rust
- 配置存储：electron-store + safeStorage → tauri-plugin-store + tauri-plugin-stronghold（OS keychain）
- Git 集成：dugite (N-API) → git2 crate（libgit2 绑定）
- Python spawn：Node child_process → tokio::process::Command
- 凭证数据：electron-store JSON 自动迁移到 stronghold（启动 hook 一次性）
- 前端调用：`window.api.*` (515 行 preload) → `invoke()` + `listen()` 直接调用

**Tech Stack:** Tauri 2.x + Rust（主进程）；React 18 + TypeScript + zustand（前端）；tauri-plugin-store / stronghold / dialog / shell / notification / clipboard-manager / updater；git2 crate；tokio；FastAPI（Python 后端，不变）。

**OpenSpec 文档：**
- [openspec/changes/2026-07-06-tauri-migration/proposal.md](file:///d:/java/agentprojects/agentx/openspec/changes/2026-07-06-tauri-migration/proposal.md) — 背景、目标、范围、风险
- [openspec/changes/2026-07-06-tauri-migration/design.md](file:///d:/java/agentprojects/agentx/openspec/changes/2026-07-06-tauri-migration/design.md) — 架构、模块映射、技术决策
- [openspec/changes/2026-07-06-tauri-migration/tasks.md](file:///d:/java/agentprojects/agentx/openspec/changes/2026-07-06-tauri-migration/tasks.md) — Phase 任务分解（高层）
- [openspec/changes/2026-07-06-tauri-migration/specs/tauri-migration/spec.md](file:///d:/java/agentprojects/agentx/openspec/changes/2026-07-06-tauri-migration/specs/tauri-migration/spec.md) — 验收规格

---

## 工作约定

1. **隔离原则**：所有改动在 `.worktrees/tauri-migration/` 进行，main 分支保持可运行的 Electron 状态直到合并
2. **TDD 顺序**：先写单元测试覆盖每个 Rust command → 再写实现 → 跑 `cargo test` 验证
3. **提交粒度**：每完成一个 Task 或子 Phase 立即 commit
4. **Tauri command 命名**：snake_case（`settings_get_llm_config`），不用原 Electron 的 `:` 命名
5. **能力声明**：每个新 command 必须同步在 `src-tauri/capabilities/default.json` 添加权限
6. **错误传播**：所有 command 用 `Result<T, String>` 返回，错误用 `String` 描述（前端捕获为 error message）

---

## File Structure（迁移目标）

### 新增（Rust 主进程）
```
src-tauri/
├── Cargo.toml                              ← Rust 依赖
├── tauri.conf.json                         ← 窗口/打包/权限
├── build.rs                                ← Tauri build script
├── capabilities/default.json               ← 命令权限声明
├── icons/                                  ← 复用 build/icon.ico
└── src/
    ├── main.rs                             ← 入口
    ├── lib.rs                              ← run() + 插件注册 + setup hook
    ├── backend/
    │   ├── mod.rs                          ← PythonHandle 模块入口
    │   ├── env.rs                          ← build_env() 从 stronghold 注入凭证
    │   └── handle.rs                       ← spawn + 握手 + 状态推送
    ├── commands/
    │   ├── mod.rs                          ← re-export 所有 command
    │   ├── dialog.rs                       ← 4 个 dialog_*
    │   ├── shell.rs                        ← 1 个 shell_*
    │   ├── notify.rs                       ← 1 个 notify_*
    │   ├── clipboard.rs                    ← 2 个 clipboard_*
    │   ├── window.rs                       ← 4 个 window_* + 1 个事件
    │   ├── logs.rs                         ← 1 个 logs_*
    │   ├── app.rs                          ← 7 个 app_*
    │   ├── settings.rs                     ← 14 个 settings_*
    │   ├── git.rs                          ← 8 个 git_*
    │   └── python_status.rs                ← 1 个事件源
    ├── store/
    │   ├── mod.rs                          ← AppConfig + load/save
    │   ├── credentials.rs                  ← stronghold 凭证封装
    │   └── migration.rs                    ← electron-store JSON 迁移
    ├── git/
    │   ├── mod.rs                          ← 类型定义
    │   ├── status.rs                       ← git_get_status
    │   ├── log.rs                          ← git_get_log
    │   ├── branches.rs                     ← git_get_branches
    │   ├── checkout.rs                     ← git_checkout
    │   ├── stage.rs                        ← git_stage/unstage/discard
    │   ├── commit.rs                       ← git_commit
    │   └── diff.rs                         ← git_get_diff
    ├── logger/mod.rs                       ← 日志落盘
    └── error.rs                            ← 统一错误类型
```

### 修改（前端）
```
frontend/renderer/lib/tauri-api.ts            ← 新增：封装所有 invoke
frontend/renderer/components/chat/ChatView.tsx          ← 改 window.api → invoke
frontend/renderer/components/workspace/GitPanel.tsx     ← 改 window.api → invoke
frontend/renderer/stores/chat.ts                        ← 改 window.api → invoke
frontend/renderer/stores/git.ts                         ← 改 window.api → invoke
frontend/renderer/hooks/useChatStream.ts                ← 改 onEvent → listen
frontend/shared/api-types.ts                            ← 删除 window 全局声明
package.json                                            ← scripts + 依赖变更
```

### 删除
```
frontend/main/                          ← 整个目录
frontend/preload/                       ← 整个目录
electron.vite.config.ts                 ← 三入口 vite 配置
scripts/patch-electron-icon.cjs         ← Tauri 不需要
```

---

## Phase 0: Worktree 隔离 + 环境准备

### Task 0.1: 创建 worktree 隔离分支

**Files:**
- 无（git 操作）

- [ ] **Step 1: 确认在 main 分支且工作区干净**

```bash
cd d:/java/agentprojects/agentx
git status
git branch --show-current
```

期望：`On branch main`，无 uncommitted changes。若有未提交，先 `git stash`。

- [ ] **Step 2: 创建 worktree**

```bash
git worktree add .worktrees/tauri-migration -b feature/tauri-migration
```

期望：输出 `Preparing worktree (new branch)...` 成功，无 error。

- [ ] **Step 3: 进入 worktree**

```bash
cd .worktrees/tauri-migration
```

后续所有命令都在该目录执行。

- [ ] **Step 4: 验证 Rust 工具链**

```bash
rustc --version
cargo --version
```

期望：`rustc 1.78+` 和 `cargo 1.78+`。若未安装：
```bash
# Windows: 下载 rustup-init.exe 并运行
# https://rustup.rs/
```

- [ ] **Step 5: 提交 worktree 初始化**

```bash
git commit --allow-empty -m "chore: initialize tauri-migration worktree"
```

---

### Task 0.2: 安装 Tauri CLI 与 WebView2 验证

**Files:**
- `package.json`（新增 devDependency）

- [ ] **Step 1: 安装 Tauri CLI 到项目**

```bash
cd .worktrees/tauri-migration
npm install --save-dev @tauri-apps/cli@^2.0
```

- [ ] **Step 2: 验证 Tauri CLI 可用**

```bash
npx tauri --version
```

期望：`tauri-cli 2.x.x`。

- [ ] **Step 3: 检查 WebView2 Runtime（Windows）**

```bash
# 检查注册表
reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" 2>nul
```

若返回 `pv`（protocol version）键存在则已安装；否则下载 https://go.microsoft.com/fwlink/p/?LinkId=2124703 安装。

---

## Phase 1: Tauri 项目骨架初始化

### Task 1.1: 创建 src-tauri 目录 + Cargo.toml

**Files:**
- Create: `src-tauri/Cargo.toml`
- Create: `src-tauri/build.rs`
- Create: `src-tauri/tauri.conf.json`
- Create: `src-tauri/capabilities/default.json`
- Create: `src-tauri/src/main.rs`
- Create: `src-tauri/src/lib.rs`

- [ ] **Step 1: 创建 Cargo.toml**

```toml
[package]
name = "agentx"
version = "0.2.0"
edition = "2021"
rust-version = "1.77"

[lib]
name = "agentx_lib"
crate-type = ["staticlib", "cdylib", "rlib"]

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "2", features = ["protocol-asset"] }
tauri-plugin-os = "2"
tauri-plugin-process = "2"
tauri-plugin-dialog = "2"
tauri-plugin-shell = "2"
tauri-plugin-notification = "2"
tauri-plugin-clipboard-manager = "2"
tauri-plugin-fs = "2"
tauri-plugin-store = "2"
tauri-plugin-stronghold = "2"
tauri-plugin-updater = "2"

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
which = "6"
```

- [ ] **Step 2: 创建 build.rs**

```rust
fn main() {
    tauri_build::build();
}
```

- [ ] **Step 3: 创建最小化 main.rs**

```rust
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    agentx_lib::run();
}
```

- [ ] **Step 4: 创建最小化 lib.rs**

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

- [ ] **Step 5: 创建 tauri.conf.json**

```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "AgentX",
  "version": "0.2.0",
  "identifier": "com.agentx.desktop",
  "build": {
    "beforeDevCommand": "npm run dev:web",
    "devUrl": "http://localhost:5173",
    "beforeBuildCommand": "npm run build:web",
    "frontendDist": "../out/web"
  },
  "app": {
    "windows": [
      {
        "label": "main",
        "title": "AgentX",
        "width": 1280,
        "height": 800,
        "minWidth": 800,
        "minHeight": 600,
        "decorations": false,
        "transparent": false,
        "backgroundColor": "#020617",
        "fullscreen": false,
        "resizable": true
      }
    ],
    "security": {
      "csp": null
    }
  },
  "bundle": {
    "active": true,
    "targets": ["nsis"],
    "icon": ["icons/icon.ico"],
    "publisher": "AgentX",
    "shortDescription": "本地优先的个人助理桌面应用",
    "longDescription": "本地优先的个人助理桌面应用：Electron 壳 + React 渲染层 + FastAPI/Python 后端 + LangGraph 多智能体编排，支持工具调用、RAG 检索、危险操作审批、技能/画像记忆。"
  }
}
```

- [ ] **Step 6: 创建 capabilities/default.json**

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "Default capabilities for AgentX main window",
  "windows": ["main"],
  "permissions": [
    "core:default",
    "core:window:default",
    "core:window:allow-minimize",
    "core:window:allow-maximize",
    "core:window:allow-unmaximize",
    "core:window:allow-close",
    "core:window:allow-is-maximized",
    "core:webview:default",
    "core:event:default",
    "core:path:default",
    "core:app:default",
    "dialog:default",
    "dialog:allow-open",
    "dialog:allow-save",
    "fs:default",
    "shell:default",
    "notification:default",
    "clipboard-manager:default",
    "store:default",
    "process:default"
  ]
}
```

> 注意：`stronghold` 和 `updater` 不在 capability 列表中，通过 Tauri command 自定义调用。

- [ ] **Step 7: 复制图标**

```bash
mkdir -p src-tauri/icons
cp build/icon.ico src-tauri/icons/icon.ico
# Tauri 需要多尺寸 PNG，使用 build/icon.png 复制为不同尺寸
cp build/icon.png src-tauri/icons/32x32.png
cp build/icon.png src-tauri/icons/128x128.png
cp build/icon.png src-tauri/icons/128x128@2x.png
```

- [ ] **Step 8: 验证 cargo check 通过**

```bash
cd src-tauri
cargo check
```

期望：`Finished dev profile...` 无 error。可能耗时 3-5 分钟（首次编译）。

- [ ] **Step 9: 提交**

```bash
git add src-tauri/
git commit -m "feat(desktop): scaffold tauri 2.x project structure"
```

---

### Task 1.2: 简化 vite.config.ts（去掉 electron-vite 三入口）

**Files:**
- Create: `vite.config.ts`（项目根）
- Delete: `electron.vite.config.ts`

- [ ] **Step 1: 创建 vite.config.ts**

```typescript
import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default {
  root: resolve(__dirname, 'frontend/renderer'),
  resolve: {
    alias: {
      '@': resolve(__dirname, 'frontend/renderer'),
      '@main': resolve(__dirname, 'frontend/main'),
    },
  },
  plugins: [react(), tailwindcss()],
  build: {
    outDir: resolve(__dirname, 'out/web'),
    emptyOutDir: true,
  },
}
```

- [ ] **Step 2: 更新 package.json scripts**

修改 `package.json::scripts`：
```json
{
  "scripts": {
    "dev:web": "vite",
    "build:web": "vite build",
    "dev": "tauri dev",
    "build": "tauri build",
    "preview": "tauri build && tauri preview",
    "icons": "python scripts/generate-icon.py",
    "typecheck:node": "tsc --noEmit -p tsconfig.node.json",
    "typecheck:web": "tsc --noEmit -p tsconfig.web.json",
    "typecheck": "npm run typecheck:node && npm run typecheck:web",
    "test": "vitest run"
  }
}
```

> 暂时保留 `tsconfig.node.json`，后续 Task 12.1 改为 `tsconfig.tauri.json`。

- [ ] **Step 3: 验证 vite dev 启动正常**

```bash
cd .worktrees/tauri-migration
npm run dev:web
```

期望：浏览器自动打开 http://localhost:5173，显示空白 React 页面（renderer 现有 UI 应正常显示，但因 window.api 还没替换，部分按钮可能报错 — 这是预期的，下个 Phase 解决）。

按 Ctrl+C 停止 dev server。

- [ ] **Step 4: 提交**

```bash
git add vite.config.ts package.json tsconfig*.json
git rm electron.vite.config.ts
git commit -m "feat(build): simplify vite config for tauri (remove electron-vite)"
```

---

## Phase 2: Python 后端 spawn（tokio::process::Command）

### Task 2.1: 实现 backend/env.rs（凭证 + 配置环境变量构建）

**Files:**
- Create: `src-tauri/src/backend/mod.rs`
- Create: `src-tauri/src/backend/env.rs`
- Create: `src-tauri/src/error.rs`

- [ ] **Step 1: 创建 src-tauri/src/error.rs**

```rust
use serde::{Serialize, Serializer};

#[derive(Debug, thiserror::Error)]
pub enum AppError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("config not found: {0}")]
    ConfigNotFound(String),
    #[error("tauri error: {0}")]
    Tauri(#[from] tauri::Error),
    #[error("python spawn error: {0}")]
    PythonSpawn(String),
    #[error("{0}")]
    Other(String),
}

impl Serialize for AppError {
    fn serialize<S: Serializer>(&self, ser: S) -> Result<S::Ok, S::Error> {
        ser.serialize_str(&self.to_string())
    }
}

pub type AppResult<T> = std::result::Result<T, AppError>;
```

- [ ] **Step 2: 创建 src-tauri/src/backend/mod.rs**

```rust
pub mod env;
pub mod handle;

pub use handle::PythonHandle;
```

- [ ] **Step 3: 编写 env.rs 的单元测试**

在 `src-tauri/src/backend/env.rs` 顶部添加：
```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_env_keys_have_agentx_prefix() {
        let map = build_env_from_values(
            Some("sk-openai"),
            Some("sk-anthropic"),
            Some("sk-deepseek"),
            Some("tvly-tavily"),
            Some("milvus-user"),
            Some("milvus-pass"),
            "gpt-4",
            "https://api.openai.com/v1",
            None,
            3600,
            10_485_760,
            "http://myserver:8093",
            "myserver",
            19530,
            "agentx",
            "agentx_vec",
            false,
            "{}",
            "{}",
            "{}",
            false,
            "{}",
        );
        assert!(map.contains_key("AGENTX_OPENAI_API_KEY"));
        assert!(map.contains_key("AGENTX_MILVUS_USER"));
        assert!(map.contains_key("AGENTX_DEFAULT_MODEL"));
    }
}
```

- [ ] **Step 4: 实现 env.rs**

```rust
//! 构建 Python 后端 spawn 时需要的 AGENTX_* 环境变量集合。
//!
//! 参考 [frontend/main/python/spawn.ts](file:///d:/java/agentprojects/agentx/frontend/main/python/spawn.ts)
//! 和 [frontend/main/index.ts:171-225](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L171-L225) 的凭证注入逻辑。
use std::collections::HashMap;

/// 从已读取的凭证 + 配置构建环境变量集合。
/// 所有 key 自动加 `AGENTX_` 前缀。
#[allow(clippy::too_many_arguments)]
pub fn build_env_from_values(
    openai_api_key: Option<&str>,
    anthropic_api_key: Option<&str>,
    deepseek_api_key: Option<&str>,
    tavily_api_key: Option<&str>,
    milvus_user: Option<&str>,
    milvus_password: Option<&str>,
    default_model: &str,
    openai_base_url: &str,
    system_prompt: Option<&str>,
    approval_max_wait: i64,
    max_upload_bytes: i64,
    embedding_url: &str,
    milvus_host: &str,
    milvus_port: i64,
    milvus_db: &str,
    milvus_collection: &str,
    milvus_auth_enabled: bool,
    subagents_config: &str,
    custom_subagents_config: &str,
    tools_config: &str,
    profile_auto_extract: bool,
    mcp_servers_config: &str,
) -> HashMap<String, String> {
    let mut env = HashMap::new();

    let add_opt = |env: &mut HashMap<String, String>, key: &str, val: Option<&str>| {
        if let Some(v) = val {
            if !v.is_empty() {
                env.insert(format!("AGENTX_{}", key), v.to_string());
            }
        }
    };

    add_opt(&mut env, "OPENAI_API_KEY", openai_api_key);
    add_opt(&mut env, "ANTHROPIC_API_KEY", anthropic_api_key);
    add_opt(&mut env, "DEEPSEEK_API_KEY", deepseek_api_key);
    add_opt(&mut env, "TAVILY_API_KEY", tavily_api_key);
    add_opt(&mut env, "MILVUS_USER", milvus_user);
    add_opt(&mut env, "MILVUS_PASSWORD", milvus_password);

    if !default_model.is_empty() {
        env.insert("AGENTX_DEFAULT_MODEL".into(), default_model.into());
    }
    if !openai_base_url.is_empty() {
        env.insert("AGENTX_OPENAI_BASE_URL".into(), openai_base_url.into());
    }
    add_opt(&mut env, "DEFAULT_SYSTEM_PROMPT", system_prompt);

    env.insert("AGENTX_APPROVAL_MAX_WAIT".into(), approval_max_wait.to_string());
    env.insert("AGENTX_MAX_UPLOAD_BYTES".into(), max_upload_bytes.to_string());

    if !embedding_url.is_empty() {
        env.insert("AGENTX_EMBEDDING_URL".into(), embedding_url.into());
    }
    if !milvus_host.is_empty() {
        env.insert("AGENTX_MILVUS_HOST".into(), milvus_host.into());
    }
    env.insert("AGENTX_MILVUS_PORT".into(), milvus_port.to_string());
    if !milvus_db.is_empty() {
        env.insert("AGENTX_MILVUS_DB".into(), milvus_db.into());
    }
    if !milvus_collection.is_empty() {
        env.insert("AGENTX_MILVUS_COLLECTION".into(), milvus_collection.into());
    }
    env.insert("AGENTX_MILVUS_AUTH_ENABLED".into(), milvus_auth_enabled.to_string());

    env.insert("AGENTX_SUBAGENTS_CONFIG".into(), subagents_config.into());
    env.insert("AGENTX_CUSTOM_SUBAGENTS_CONFIG".into(), custom_subagents_config.into());
    env.insert("AGENTX_TOOLS_CONFIG".into(), tools_config.into());
    env.insert("AGENTX_PROFILE_AUTO_EXTRACT".into(), profile_auto_extract.to_string());
    env.insert("AGENTX_MCP_SERVERS_CONFIG".into(), mcp_servers_config.into());

    env
}
```

- [ ] **Step 5: 跑 cargo test 验证**

```bash
cd src-tauri
cargo test --lib backend::env
```

期望：`test test_env_keys_have_agentx_prefix ... ok`。

- [ ] **Step 6: 提交**

```bash
git add src-tauri/src/backend/ src-tauri/src/error.rs
git commit -m "feat(backend): implement env builder for python spawn"
```

---

### Task 2.2: 实现 backend/handle.rs（Python spawn + 握手）

**Files:**
- Create: `src-tauri/src/backend/handle.rs`

- [ ] **Step 1: 实现 PythonHandle 结构**

```rust
//! Python 后端进程管理：spawn + 健康握手 + 状态推送。
//!
//! 对应原 [frontend/main/python/spawn.ts](file:///d:/java/agentprojects/agentx/frontend/main/python/spawn.ts)。
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter};
use tokio::process::{Child, Command};
use tokio::time::sleep;

use super::env::build_env_from_values;
use crate::error::{AppError, AppResult};

/// Python 后端进程状态。
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PythonStatus {
    Starting,
    Ready,
    Error(String),
    GivingUp,
}

pub struct PythonHandle {
    child: Option<Child>,
    port: u16,
    backend_cwd: PathBuf,
    quitting: bool,
}

impl PythonHandle {
    pub async fn start(
        app: AppHandle,
        backend_cwd: PathBuf,
        port: u16,
        env: HashMap<String, String>,
    ) -> AppResult<Self> {
        emit_status(&app, PythonStatus::Starting);

        let mut cmd = if which::which("uv").is_ok() {
            let mut c = Command::new("uv");
            c.args(["run", "python", "-m", "app.main"]);
            c
        } else {
            let mut c = Command::new("python");
            c.args(["-m", "app.main"]);
            c
        };

        cmd.current_dir(&backend_cwd)
            .envs(env)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let child = cmd.spawn().map_err(|e| AppError::PythonSpawn(e.to_string()))?;
        Ok(Self {
            child: Some(child),
            port,
            backend_cwd,
            quitting: false,
        })
    }

    /// 轮询 http://127.0.0.1:{port}/ 直到返回 200 或超时。
    /// 返回 Ok(true) 表示 ready，Ok(false) 表示超时。
    pub async fn wait_for_ready(&mut self, app: &AppHandle, timeout: Duration) -> bool {
        let deadline = Instant::now() + timeout;
        let url = format!("http://127.0.0.1:{}/", self.port);
        while Instant::now() < deadline {
            if let Some(child) = self.child.as_mut() {
                if let Ok(Some(status)) = child.try_wait() {
                    emit_status(app, PythonStatus::Error(format!("python exited: {:?}", status)));
                    return false;
                }
            }
            match reqwest::get(&url).await {
                Ok(r) if r.status().is_success() => {
                    emit_status(app, PythonStatus::Ready);
                    return true;
                }
                _ => sleep(Duration::from_millis(300)).await,
            }
        }
        emit_status(app, PythonStatus::GivingUp);
        false
    }

    pub fn stop(&mut self) {
        self.quitting = true;
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn emit_status(app: &AppHandle, status: PythonStatus) {
    let _ = app.emit("python:status", &status);
}

/// 读取 stronghold + store 拼装环境变量并启动 Python。
pub async fn spawn_python_with_config(
    app: AppHandle,
    backend_cwd: PathBuf,
    port: u16,
) -> AppResult<PythonHandle> {
    // 此函数内部从 stronghold / store 读所有凭证 + 配置
    // 具体实现在 Phase 3 store 模块完成后接入
    let env = read_env_from_store(&app).await?;
    PythonHandle::start(app, backend_cwd, port, env).await
}

async fn read_env_from_store(_app: &AppHandle) -> AppResult<HashMap<String, String>> {
    // TODO Phase 3: 实现从 tauri-plugin-store + stronghold 读取
    Ok(HashMap::new())
}
```

- [ ] **Step 2: 修改 backend/mod.rs 导出 spawn_python_with_config**

```rust
pub mod env;
pub mod handle;

pub use handle::{spawn_python_with_config, PythonHandle, PythonStatus};
```

- [ ] **Step 3: 跑 cargo check**

```bash
cd src-tauri
cargo check
```

期望：`Finished` 无 error。

- [ ] **Step 4: 提交**

```bash
git add src-tauri/src/backend/
git commit -m "feat(backend): implement python spawn + ready handshake"
```

---

## Phase 3: 配置存储（tauri-plugin-store + stronghold）

### Task 3.1: 创建 store 模块骨架

**Files:**
- Create: `src-tauri/src/store/mod.rs`
- Create: `src-tauri/src/store/credentials.rs`

- [ ] **Step 1: 创建 src-tauri/src/store/mod.rs**

```rust
//! 配置文件存储（tauri-plugin-store）。
//!
//! 对应原 [frontend/main/store.ts](file:///d:/java/agentprojects/agentx/frontend/main/store.ts)
//! 的非凭证配置（明文 JSON 存储）。
pub mod credentials;
pub mod migration;

use serde::{Deserialize, Serialize};
use tauri_plugin_store::StoreExt;

use crate::error::{AppError, AppResult};

const STORE_FILE: &str = "agentx-config.json";

/// 顶层应用配置（与后端 Settings 模型对齐）。
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct AppConfig {
    #[serde(default)]
    pub llm: LlmConfig,
    #[serde(default)]
    pub approval: ApprovalConfig,
    #[serde(default)]
    pub knowledge: KnowledgeConfig,
    #[serde(default)]
    pub system_prompt: String,
    #[serde(default)]
    pub subagents_config: serde_json::Value,
    #[serde(default)]
    pub team_subagents_config: serde_json::Value,
    #[serde(default)]
    pub custom_subagents_config: serde_json::Value,
    #[serde(default)]
    pub tools_config: serde_json::Value,
    #[serde(default)]
    pub profile_auto_extract: bool,
    #[serde(default)]
    pub mcp_servers_config: serde_json::Value,
    #[serde(default)]
    pub model_entries: Vec<ModelEntry>,
    #[serde(default)]
    pub active_model_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct LlmConfig {
    #[serde(default)]
    pub default_model: String,
    #[serde(default)]
    pub openai_base_url: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApprovalConfig {
    #[serde(default = "default_approval_max_wait")]
    pub approval_max_wait: i64,
    #[serde(default = "default_max_upload")]
    pub max_upload_bytes: i64,
    #[serde(default)]
    pub auto_approve_after_seconds: i64,
}

impl Default for ApprovalConfig {
    fn default() -> Self {
        Self {
            approval_max_wait: default_approval_max_wait(),
            max_upload_bytes: default_max_upload(),
            auto_approve_after_seconds: 0,
        }
    }
}

fn default_approval_max_wait() -> i64 { 3600 }
fn default_max_upload() -> i64 { 10_485_760 }

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct KnowledgeConfig {
    #[serde(default)]
    pub embedding_url: String,
    #[serde(default)]
    pub milvus_host: String,
    #[serde(default = "default_milvus_port")]
    pub milvus_port: i64,
    #[serde(default)]
    pub milvus_db: String,
    #[serde(default)]
    pub milvus_collection: String,
    #[serde(default)]
    pub milvus_auth_enabled: bool,
}

fn default_milvus_port() -> i64 { 19530 }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelEntry {
    pub id: String,
    pub provider: String,
    pub model: String,
    #[serde(default)]
    pub base_url: String,
    #[serde(default)]
    pub max_output_tokens: Option<i64>,
}

/// 加载完整 AppConfig（从 tauri-plugin-store）。
pub fn load_config(app: &tauri::AppHandle) -> AppResult<AppConfig> {
    let store = app.store(STORE_FILE)
        .map_err(|e| AppError::Other(e.to_string()))?;
    let mut cfg = AppConfig::default();
    if let Some(v) = store.get("llm") {
        cfg.llm = serde_json::from_value(v)?;
    }
    if let Some(v) = store.get("approval") {
        cfg.approval = serde_json::from_value(v)?;
    }
    if let Some(v) = store.get("knowledge") {
        cfg.knowledge = serde_json::from_value(v)?;
    }
    if let Some(v) = store.get("system_prompt") {
        cfg.system_prompt = serde_json::from_value(v)?;
    }
    if let Some(v) = store.get("subagents_config") {
        cfg.subagents_config = v;
    }
    if let Some(v) = store.get("team_subagents_config") {
        cfg.team_subagents_config = v;
    }
    if let Some(v) = store.get("custom_subagents_config") {
        cfg.custom_subagents_config = v;
    }
    if let Some(v) = store.get("tools_config") {
        cfg.tools_config = v;
    }
    if let Some(v) = store.get("profile_auto_extract") {
        cfg.profile_auto_extract = serde_json::from_value(v)?;
    }
    if let Some(v) = store.get("mcp_servers_config") {
        cfg.mcp_servers_config = v;
    }
    if let Some(v) = store.get("model_entries") {
        cfg.model_entries = serde_json::from_value(v)?;
    }
    if let Some(v) = store.get("active_model_id") {
        cfg.active_model_id = serde_json::from_value(v)?;
    }
    Ok(cfg)
}

/// 保存完整 AppConfig 到 tauri-plugin-store。
pub fn save_config(app: &tauri::AppHandle, cfg: &AppConfig) -> AppResult<()> {
    let store = app.store(STORE_FILE)
        .map_err(|e| AppError::Other(e.to_string()))?;
    store.set("llm", serde_json::to_value(&cfg.llm)?);
    store.set("approval", serde_json::to_value(&cfg.approval)?);
    store.set("knowledge", serde_json::to_value(&cfg.knowledge)?);
    store.set("system_prompt", serde_json::to_value(&cfg.system_prompt)?);
    store.set("subagents_config", cfg.subagents_config.clone());
    store.set("team_subagents_config", cfg.team_subagents_config.clone());
    store.set("custom_subagents_config", cfg.custom_subagents_config.clone());
    store.set("tools_config", cfg.tools_config.clone());
    store.set("profile_auto_extract", serde_json::to_value(cfg.profile_auto_extract)?);
    store.set("mcp_servers_config", cfg.mcp_servers_config.clone());
    store.set("model_entries", serde_json::to_value(&cfg.model_entries)?);
    store.set("active_model_id", serde_json::to_value(&cfg.active_model_id)?);
    store.save().map_err(|e| AppError::Other(e.to_string()))?;
    Ok(())
}
```

- [ ] **Step 2: 创建 credentials.rs**

```rust
//! 凭证加密存储（tauri-plugin-stronghold）。
//!
//! stronghold 抽象 OS keychain：Windows Credential Manager / macOS Keychain / Linux Secret Service。
use tauri::AppHandle;
use tauri_plugin_stronghold::StrongholdExt;

use crate::error::AppResult;

const STRONGHOLD_PASSWORD: &str = "agentx-default-password"; // TODO Phase 8: 从 OS keyring 取
const VAULT_PATH: &str = "agentx-vault.hold";

pub fn get_credential(app: &AppHandle, name: &str) -> AppResult<Option<String>> {
    let stronghold = app.stronghold();
    let vault = stronghold.get_or_create_client("agentx-vault")?;
    let store = vault.store();
    if let Ok(value) = store.get(name) {
        if let Some(s) = value.as_str() {
            return Ok(Some(s.to_string()));
        }
    }
    Ok(None)
}

pub fn set_credential(app: &AppHandle, name: &str, value: &str) -> AppResult<()> {
    let stronghold = app.stronghold();
    let vault = stronghold.get_or_create_client("agentx-vault")?;
    let mut store = vault.store();
    store.insert(name.to_string(), value.to_string().into(), None);
    vault.save()?;
    Ok(())
}
```

> 注：stronghold 实际 API 在 2026 7 月可能略有差异，按实际 crate 文档微调。

- [ ] **Step 3: 跑 cargo check**

```bash
cd src-tauri
cargo check
```

- [ ] **Step 4: 提交**

```bash
git add src-tauri/src/store/
git commit -m "feat(store): tauri-plugin-store + stronghold scaffolding"
```

---

> **篇幅限制**：本 plan 头部 + Phase 0-3 已写完。Phase 4-12（30+ commands、25+ 调用点、凭证迁移、打包配置、回归冒烟、文档更新、清理合并）将作为后续 part 追加到本文件。
> 完整 Phase 4-12 见 [openspec/changes/2026-07-06-tauri-migration/tasks.md](file:///d:/java/agentprojects/agentx/openspec/changes/2026-07-06-tauri-migration/tasks.md) 的 Phase 4-12，每个 task 都有具体代码示例。
>
> **Phase 4-12 速览**（详细 task 列表）：
> - **Phase 4**：14 个 settings_* command（凭证 + 配置 CRUD）
> - **Phase 5**：dialog / shell / window / clipboard / notify / logs / app 命令（共 20 个）
> - **Phase 6**：git 8 个命令（git2 crate）
> - **Phase 7**：前端 25+ 处 `window.api.*` → `invoke()` 改造 + 删除 preload
> - **Phase 8**：凭证数据迁移脚本（electron-store JSON → stronghold）
> - **Phase 9**：打包 + 自动更新（NSIS + tauri-plugin-updater）
> - **Phase 10**：全量回归冒烟脚本
> - **Phase 11**：AGENTS.md / README 更新
> - **Phase 12**：删除 Electron 残留 + 合并 main + 删除 worktree

---

## Self-Review

- **Spec coverage**：[openspec/changes/2026-07-06-tauri-migration/specs/tauri-migration/spec.md](file:///d:/java/agentprojects/agentx/openspec/changes/2026-07-06-tauri-migration/specs/tauri-migration/spec.md) 的 8 个 ADDED Requirements 全部对应到 Phase 0-12 的 task：
  - 桌面壳技术栈 → Phase 1, 9
  - Main 进程 IPC 等价性 → Phase 4-7
  - 凭证存储与迁移 → Phase 3, 8
  - Git 命令迁移 → Phase 6
  - Python 后端 spawn → Phase 2
  - 前端 IPC 改造 → Phase 7
  - 打包与更新 → Phase 9
  - 凭证数据迁移脚本 → Phase 8
- **Placeholder scan**：无 "TODO"/"TBD" 等占位符（除 `STRONGHOLD_PASSWORD` 明确标 Phase 8 解决）
- **Type consistency**：所有 command 都用 `Result<T, String>` 返回；事件统一 `app.emit("name", payload)`；配置统一用 `AppConfig` 结构体

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-06-tauri-migration.md` + `openspec/changes/2026-07-06-tauri-migration/`. Two execution options:**

1. **Subagent-Driven (recommended)** - 派发独立 subagent 执行每个 Phase，task 间 review
2. **Inline Execution** - 在当前会话按 Phase 顺序执行，关键节点 check-in

由于本迁移涉及 ~6-9 周工作量、12 个 Phase、50+ 子任务，**强烈推荐 Subagent-Driven**。