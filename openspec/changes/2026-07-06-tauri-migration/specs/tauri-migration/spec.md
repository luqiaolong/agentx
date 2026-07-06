## ADDED Requirements

### Requirement: 桌面壳技术栈

桌面壳 SHALL 基于 Tauri 2.x + Rust 主进程构建，替代原 Electron 32 + electron-vite 2 + electron-builder 25 栈。

#### Scenario: 安装包体积

- **GIVEN** 迁移完成后的 AgentX 安装包
- **WHEN** 在 Windows x64 上执行 `npm run tauri build`
- **THEN** NSIS 安装包大小 ≤ 20MB（原 Electron 安装包 ~180MB）

#### Scenario: 启动内存占用

- **GIVEN** 启动 AgentX 应用并打开主窗口
- **WHEN** 测量进程驻留内存
- **THEN** 主进程内存占用 ≤ 80MB（原 Electron 进程 200-250MB）

---

### Requirement: Main 进程 IPC 等价性

Tauri 命令 SHALL 在语义、参数、返回值上与原 Electron IPC handler 完全等价，前端调用方无需感知底层切换。

#### Scenario: 61 个 IPC handler 全部迁移

- **WHEN** 迁移完成
- **THEN** `frontend/main/index.ts` 中的 61 个 `ipcMain.handle` 全部对应到 `src-tauri/src/commands/` 的 Tauri command
- **AND** 所有 command 在 `capabilities/default.json` 中显式声明

#### Scenario: 2 个推送事件迁移

- **WHEN** 迁移完成
- **THEN** 原 Electron `webContents.send` 的 2 个事件（`python:status`、`window:maximized-change`）改为 Tauri `app.emit` / `window.emit`

---

### Requirement: 凭证存储与迁移

凭证 SHALL 使用 `tauri-plugin-stronghold`（OS keychain 抽象）加密存储，启动时自动迁移原 `electron-store` 数据。

#### Scenario: 明文凭证自动迁移

- **GIVEN** 原 `config.json`（electron-store 默认路径 `%APPDATA%/agentx/config.json`）存在且包含明文凭证（`plain:` 前缀或裸字符串）
- **WHEN** AgentX 首次以 Tauri 启动
- **THEN** 启动 hook 检测到旧文件 → 读取明文值 → 加密写入 stronghold → 备份原文件为 `config.json.migrated`

#### Scenario: 加密凭证提示重输

- **GIVEN** 原 `config.json` 包含 `enc:` 前缀加密值
- **WHEN** 迁移脚本检测到无法自动解密
- **THEN** 在 settings 页对应字段显示"请重新输入"提示

---

### Requirement: Git 命令迁移（dugite → git2）

Git 8 个命令 SHALL 迁移到 `git2` crate，输出格式与原 dugite 实现保持一致。

#### Scenario: GitPanel 功能不变

- **GIVEN** 工作区存在 git 仓库
- **WHEN** 用户打开 GitPanel 触发 status/log/branches/checkout/stage/unstage/commit/discard/diff 任一操作
- **THEN** 行为与迁移前完全一致（解析输出结构、错误信息、并发控制）

---

### Requirement: Python 后端 spawn

Tauri 主进程 SHALL 使用 `tokio::process::Command` 启动 Python 后端，凭证通过 `AGENTX_*` 环境变量注入。

#### Scenario: 后端启动流程

- **GIVEN** 用户点击启动 AgentX
- **WHEN** Tauri 主进程执行 `setup()` hook
- **THEN** 调 `Command::new("uv").args(["run", "python", "-m", "app.main"])` 启动后端
- **AND** uv 缺失时回退 `Command::new("python")`
- **AND** 凭证从 stronghold 读出后注入 env

#### Scenario: 启动握手

- **GIVEN** Python 进程已 spawn
- **WHEN** 主进程轮询 `http://127.0.0.1:8123/`
- **THEN** 30 秒内收到**连续 2 次** 200 响应 → 标记 ready
- **AND** 期间通过 `app.emit("python:status", status)` 推送状态给前端
- **AND** 崩溃时指数退避重试（1s/2s/4s）最多 3 次
- **AND** uv 缺失时回退 `python -m app.main`
- **AND** 进程清理：Windows `taskkill /T /F` 递归杀进程树，Unix 负 PID 杀进程组

---

### Requirement: 前端 IPC 调用改造

`frontend/renderer/` 中所有 `window.api.*` 调用 SHALL 替换为 `@tauri-apps/api/core::invoke()` 或直接 `fetch()` 调用后端 HTTP API。删除 preload，不做封装层。

#### Scenario: 调用点全部迁移

- **WHEN** 迁移完成
- **THEN** 所有 125 处 `window.api.*` 调用点（分布在 27 个文件）改为 `invoke()` 或 `fetch()` 直调
- **AND** preload 中 56 处 `ipcRenderer.invoke` 代理 → renderer 直接 `invoke()`
- **AND** preload 中 19 处 `fetch` HTTP 代理 → renderer 直接 `fetch()`
- **AND** `frontend/preload/index.ts` 被删除
- **AND** `window.api` 在 TypeScript 类型中不再存在

#### Scenario: 事件订阅改造

- **GIVEN** 前端需要订阅 `python:status` 或 `window:maximized-change`
- **WHEN** 改造完成
- **THEN** 使用 `import { listen } from "@tauri-apps/api/event"` 替代 `window.api.python.onStatus`

---

### Requirement: 打包与更新

应用 SHALL 通过 `tauri build` 生成 NSIS 安装包，通过 `tauri-plugin-updater` 提供自动更新能力（本次仅配置骨架，密钥/CI/releases 端点后续 PR）。

#### Scenario: NSIS 打包

- **GIVEN** 迁移完成
- **WHEN** 执行 `npm run tauri build`
- **THEN** 在 `src-tauri/target/release/bundle/nsis/` 下生成 `AgentX_<version>_x64-setup.exe`
- **AND** 安装包 ≤ 20MB

#### Scenario: 自动更新骨架配置

- **GIVEN** `tauri.conf.json` 配置了 updater 端点 + 占位公钥
- **WHEN** 迁移完成
- **THEN** `tauri-plugin-updater` 已注册（`src-tauri/src/lib.rs`）
- **AND** `tauri.conf.json::plugins.updater.pubkey` 为占位值（非真实密钥）
- **AND** 未生成签名密钥对、未配置 CI、未搭建 releases 端点（后续 PR 处理）

---

### Requirement: 凭证数据迁移脚本

应用 SHALL 在 `setup()` hook 中执行 electron-store → stronghold 数据迁移，老用户零感知。

#### Scenario: 首次启动迁移

- **GIVEN** 用户从 Electron 版本升级到 Tauri 版本（首次启动）
- **WHEN** `setup()` 执行
- **THEN** 检测 `%APPDATA%/agentx/config.json`（electron-store `new Store()` 默认路径）
- **AND** 存在 → 读取所有 key → 区分明文 / 加密值 → 加密写入 stronghold → 备份原文件为 `config.json.migrated` → 删除原文件

#### Scenario: legacy LLM 配置种子

- **GIVEN** 用户从 Electron 版本升级，`models.entries` 为空但 `llm.defaultModel` + `apikey.*` 已存在
- **WHEN** `migrate_legacy_llm_config()` 执行
- **THEN** 推断 provider（deepseek/openai/minimax/custom）
- **AND** 种子一条默认 model entry 到 `models.entries` + 设置 `models.activeId`
- **AND** 老用户升级后已配置的模型不丢失

---

## REMOVED Requirements

### Requirement: Electron 桌面壳

原 Electron 32 + electron-vite 2 + electron-builder 25 桌面壳 SHALL 完全移除，包括 `frontend/main/`、`frontend/preload/`、`electron.vite.config.ts`。

#### Scenario: 不存在 Electron 残留

- **WHEN** 迁移完成
- **THEN** `frontend/main/` 目录不存在
- **AND** `frontend/preload/` 目录不存在
- **AND** `electron.vite.config.ts` 文件不存在
- **AND** `package.json` 不包含 `electron` / `electron-vite` / `electron-builder` / `electron-updater` / `electron-store` / `dugite` 依赖
- **AND** `package.json` scripts 中的 `dev` / `build` / `dist*` / `pack` / `preview` / `icons` / `patch-icon` / `predev` 改为 `tauri dev` / `tauri build` 形式，删除 Electron 专用脚本

---

### Requirement: dugite Git 集成

原 dugite N-API 包 SHALL 完全移除，Git 命令改用 `git2` crate。

#### Scenario: 不存在 dugite

- **WHEN** 迁移完成
- **THEN** `package.json` 不包含 `dugite` 依赖
- **AND** 代码中不引用 `from "dugite"` 或 `import { exec } from "dugite"`
- **AND** 所有 8 个 Git 命令由 `src-tauri/src/git/` 模块提供

---

### Requirement: safeStorage 凭证加密

原 Electron `safeStorage` 凭证加密 SHALL 由 `tauri-plugin-stronghold` 替代。

#### Scenario: 不存在 safeStorage

- **WHEN** 迁移完成
- **THEN** 代码中不引用 `from "electron".safeStorage`
- **AND** `enc:<base64>` 格式凭证不再被新写入
- **AND** 所有凭证加密通过 stronghold 接口完成

---