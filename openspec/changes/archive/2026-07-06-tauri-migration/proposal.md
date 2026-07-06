# Proposal: Electron → Tauri 2.x 全量替换（一次性迁移）

## 背景

当前 AgentX 桌面壳基于 Electron 32 + electron-vite 2 + electron-builder 25 构建，
包含以下技术债务：

1. **包体积臃肿**：Windows NSIS 安装包 ~180MB，冷启动内存 200-250MB，
   用户首次下载/更新成本高
2. **Main 进程 TS 缺乏类型守护**：[frontend/main/index.ts](file:///d:/java/agentprojects/agentx/frontend/main/index.ts) 855 行 TS，
   包含 61 个 IPC handler、Python spawn、JumpList、AppUserModelID 等平台特定代码
3. **凭证加密链路复杂**：[frontend/main/store.ts](file:///d:/java/agentprojects/agentx/frontend/main/store.ts) 用
   `electron-store` + `safeStorage` 自实现 `enc:`/`plain:` 双格式，跨平台行为不一致
4. **Git 集成强依赖 dugite**：8 个 Git 命令（status/log/branches/checkout/stage/unstage/commit/discard/diff）通过 dugite 调系统 git，
   dugite 是 N-API 包，依赖 Electron ABI，重建困难
5. **Preload 上下文膨胀**：[frontend/preload/index.ts](file:///d:/java/agentprojects/agentx/frontend/preload/index.ts) 514 行
   contextBridge 暴露 `window.api.*`，包含 chat/sandbox/skills/workspace/python/logs/dialog/shell/approve/health/settings/mcp/memory/app/window/git 共 16 个命名空间
   （其中 8 个命名空间为 HTTP 代理调用后端 API，8 个为 IPC 转发）
6. **打包/签名/更新三套独立配置**：electron-builder NSIS/DMG/AppImage + electron-updater，
   每次升级需重新配

## 目标

用 **Tauri 2.x + Rust 主进程** 全量替换 Electron 桌面壳，**一次性迁移、不做兼容层**：

| 收益 | 量化 |
|---|---|
| 安装包体积 | -90%（~180MB → ~15MB） |
| 启动内存占用 | -70%（200-250MB → 50-80MB） |
| 冷启动时间 | -75%（2-4s → 0.5-1s） |
| Main 进程类型安全 | TS 860 行 → Rust 强类型 |
| 凭证加密 | safeStorage → tauri-plugin-store + enc:/plain: 前缀 |
| 跨平台打包 | electron-builder 3 套 → `tauri build` 1 套 |

## 范围

**纳入本次迁移**：
- `frontend/main/` → `src-tauri/src/`（Rust 主进程 + commands）
- `frontend/preload/` → 删除，renderer 直接调 `invoke()` + `event::listen()` + `fetch()`
- `electron.vite.config.ts` → `vite.config.ts`（纯前端构建）
- `electron-store` + `safeStorage` → `tauri-plugin-store`（凭证用 `enc:`/`plain:` 前缀格式，stronghold 因无公开 Rust runtime API 不用于凭证存储）
- `dugite` → `git2` crate 或 `std::process::Command` 调 git CLI
- `electron-builder` → `tauri build`（内置 NSIS/DMG/AppImage）
- `electron-updater` → `tauri-plugin-updater`（仅配置骨架，密钥/CI/releases 端点后续 PR）
- `package.json` scripts: `dev/build/dist*/pack/icons/patch-icon/predev` → `tauri dev/build`（清理 Electron 专用脚本）
- 所有 `window.api.*` 调用点（125 处分布在 27 个文件）改为 `invoke()` 或 `fetch()` 直调
- `electron-store` 凭证数据 → tauri-plugin-store（明文直接迁移，enc: 提示重输）

**不在本次范围**（后续 PR 处理）：
- macOS / Linux 平台同步打包签名（先保 Windows）
- WebView2 Runtime 自动引导
- Tauri updater 签名密钥生成 + CI 配置 + releases 端点搭建（本次仅配置骨架 + 公钥占位）
- macOS 公证（notarization）

## 预期收益

- 安装包从 180MB 降到 15MB，分发/更新成本下降一个数量级
- Main 进程崩溃率下降（Rust panic 边界 + tokio 异步）
- 凭证读写统一走 tauri-plugin-store（enc:/plain: 前缀格式），跨平台行为一致
- 减少一处运行时依赖（dugite N-API），避免 Electron 升级连带问题
- 跨平台打包脚本从 3 套 YAML 收敛为 1 个 `tauri.conf.json`

## 风险

| 风险 | 严重度 | 缓解 |
|---|---|---|
| Rust 学习曲线，团队 0 经验 | 高 | 本次迁移预留 1 周学习缓冲；关键命令有单元测试兜底 |
| Windows WebView2 Runtime 依赖 | 中 | 在 README + 安装包加说明；后续 PR 加 WebView2 bootstrapper |
| dugite 替换为 git CLI 行为差异（解析 porcelain 输出） | 中 | 复用现有 [frontend/main/index.ts:604-655](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L604-L655) 的解析逻辑迁移到 Rust，逐用例 e2e 测试 |
| tauri-plugin-store 跨平台差异 | 低 | store 基于 AppData 目录 + JSON，跨平台行为一致 |
| 一次性迁移无回退 | 高 | 用 git worktree 隔离（[AGENTS.md §14.7](file:///d:/java/agentprojects/agentx/AGENTS.md) 规范）；main 分支保持可运行的 Electron 状态直到合并前 |
| CSS 兼容性（WebView2 vs Chromium） | 低 | 现代 CSS 特性已稳定；出问题用 Tailwind v4 兜底 |
| 自动更新签名密钥迁移 | 中 | 重新生成密钥对 + 旧版本用户强制手动下载升级 |
| tauri-plugin-updater 配置错误导致全量用户更新失败 | 中 | 在 staging 环境先发一个版本验证更新链 |

## 决策记录

| # | 决策 | 理由 |
|---|---|---|
| D1 | Tauri 2.x（不用 1.x） | 2.x 是 stable，移动 + 桌面统一 API |
| D2 | 用 `git2` crate 替代 dugite | 原生绑定 libgit2，性能更好，无 N-API 兼容问题 |
| D3 | tauri-plugin-store 持久化（凭证用 enc:/plain: 前缀） | stronghold v2.3.1 无公开 Rust runtime API，无法在 build_env() 中读取；复用 electron-store 前缀格式便于迁移 |
| D4 | 一次性全量替换，不做兼容层 | 用户已明确要求；分阶段会增加 30% 工作量 |
| D5 | git worktree 隔离（.worktrees/tauri-migration/） | 符合 AGENTS.md §14.7，主分支保持 Electron 可运行 |
| D6 | Windows 优先，macOS/Linux 后续 PR | 当前用户为 Windows 单平台，迁移风险最小化 |
| D7 | 凭证数据迁移脚本：读取 electron-store JSON → 写入 tauri-plugin-store（明文直接迁移，enc: 提示重输） | 老用户不需重配 LLM/Milvus key（enc: 值除外） |
| D8 | NSIS target + perMachine 安装 | 沿用现有 electron-builder NSIS 配置语义 |
| D9 | preload HTTP 代理调用全部直调 | 删除 preload，renderer 直接调 invoke + fetch；架构最干净，无中间层 |
| D10 | tauri-plugin-updater 仅配置骨架 | 本次不生成密钥/配 CI/搭 releases 端点；公钥占位，后续 PR 实现 |