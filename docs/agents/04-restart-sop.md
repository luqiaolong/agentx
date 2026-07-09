# 启动 / 重启前后端 SOP

> 原 `AGENTS.md` §14.7 拆分。这套流程是 2026-07-04 / 2026-07-09 反复实战出来的。
> 阅读时机：首次启动、端口冲突、Errno 10048、dev 进程残留、Tauri 桌面窗口不出现。

---

## §14.7.1 启动入口

- **入口：永远 `pnpm tauri dev`**（即 `npm run tauri dev`），不要直接 `uv run python -m app.main`——
  后端依赖的 `AGENTX_*` 凭证 + 配置由 Rust 主进程通过
  [src-tauri/src/backend/env.rs](file:///d:/java/agentprojects/agentx/src-tauri/src/backend/env.rs) 注入，
  直接起 uvicorn 会缺 key、缺 Milvus 密码、缺 tools / subagents config。
- `pnpm tauri dev` 启动顺序：vite renderer 构建 → Tauri 主进程编译启动 →
  `setup()` hook → migration → `PythonHandle::start` 拉 uv → uvicorn 监听 8123 →
  Tauri 桌面窗口出现。
- **首次启动 Rust 编译**约 1-3 分钟（增量编译约 5-15s），看到
  `Finished dev profile target(s) in ...` 表示 Rust 编译完成。
- 看到 `AgentX Tauri shell started` 日志后再等 **8-10s** 再探测 8123。

## §14.7.2 单独启动场景（仅调试用）

| 场景 | 命令 | 用途 |
|---|---|---|
| 仅调试前端 | `pnpm dev` 或 `npx vite --config vite.config.mjs --host 127.0.0.1` | 浏览器调试 UI（绕过 Tauri） |
| 仅调试后端 | `.venv\Scripts\python.exe -m uvicorn backend.app.main:app --port 8123 --reload` | 跳过 Tauri 直接调试 Python |
| 仅重启后端 | Ctrl+C 当前后端 → 重启上述 uvicorn | 不影响 Tauri 桌面窗口 |

> ⚠️ 单独启动的后端需要自己注入环境变量（`AGENTX_*` 密钥），推荐还是用 `pnpm tauri dev`。

## §14.7.3 重启流程（标准 SOP）

### 步骤 1：清理两棵进程树

只 `Stop-Process -Id <pid>` 不够——uv→python 的父子链不杀干净会导致 Errno 10048。
**必须两棵树并行端**（PowerShell 原生命令，禁止用 `taskkill`、`netstat`）：

```powershell
# Tauri 主进程 + WebView2 子进程（按 CommandLine 精准筛选，避免误杀其他项目的 python/node）
Get-Process -Name python,node -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*agentx*' -or $_.CommandLine -like '*tauri*' -or $_.CommandLine -like '*vite*' } |
    Stop-Process -Force

# 也可按项目名/包名筛选（如 Hermes 等其他项目并行时）
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -in @('agentx','AgentX') } | Stop-Process -Force

# 验证端口已释放（注意 TimeWait 状态需等待 1-2 分钟）
Get-NetTCPConnection -LocalPort 8123,5173,5174 -ErrorAction SilentlyContinue |
    Where-Object { $_.State -ne 'TimeWait' }
# 返回空才算彻底清干净
```

### 步骤 2：重新启动

```powershell
cd d:/java/agentprojects/agentx
pnpm tauri dev          # 完整启动（Tauri + Vite + Python）
```

或者分步启动（仅排查时）：

```powershell
# 1. 后端（端口 8123）
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8123 --reload

# 2. 前端（默认端口 5173，被占用时自动找下一个空闲端口 5174+）
npx vite --config vite.config.mjs --host 127.0.0.1
```

### 步骤 3：健康探测

```powershell
# 探测 8123 后端
Invoke-RestMethod -Method GET -Uri 'http://127.0.0.1:8123/' -TimeoutSec 5
Invoke-RestMethod -Method GET -Uri 'http://127.0.0.1:8123/api/health' -TimeoutSec 5

# 探测前端端口
Get-NetTCPConnection -LocalPort 5173,5174 -ErrorAction SilentlyContinue | Select-Object LocalPort, State
```

## §14.7.4 Windows 端口占用诊断与解决

### 症状 1：`[WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试`

- 原因：端口 8123 / 5173 被其他进程占用
- 诊断：
  ```powershell
  Get-NetTCPConnection -LocalPort 8123 -ErrorAction SilentlyContinue |
      Select-Object LocalPort, OwningProcess, State
  ```
- 解决：
  ```powershell
  # 方法 A：精准杀掉占用进程（推荐）
  Stop-Process -Id <OwningProcess> -Force

  # 方法 B：等待 TimeWait 释放（1-2 分钟）
  # 方法 C：换端口启动（仅限临时调试）
  ```

### 症状 2：端口 5173 启动后 `Port 5173 is already in use`

- 原因：上次 `tauri dev` 残留 Vite watcher 进程
- 解决：按 §14.7.3 步骤 1 清理所有相关 node 进程，或换端口 `npx vite --port 5174`

### 症状 3：Tauri 自动启动 Python 但端口冲突

- 现象：Tauri 主进程拉起 Python 时打印 `port 8123 被 PID xxx 占用，先行 kill`
- 处理：Tauri 已自动 kill 占用进程，无需手动干预；如持续冲突，先按 §14.7.3 完全清理

## §14.7.5 健康探测规范

- **`/api/health` 不是存活探针**。该端点同步串行调 TEI（myserver:8093）+ Milvus
  （myserver:19530），外部不通就耗时 5s+ 看起来像超时，但它**永远 200 兜底**。
  要做进程存活检测，用下面 4 个**轻量**端点任意一个：
  | 端点 | 用法 |
  |---|---|
  | `GET /` | 返回 `{app, version, status}`，零依赖，< 50ms |
  | `GET /api/skills` | 验证技能文件加载链路 |
  | `GET /api/memory/checkpointer` | 验证 SQLite checkpoint |
  | `POST /api/sandbox/authorize` | 顺手验证沙箱授权链路 |

## §14.7.6 重启常见错误

| 错误 | 原因 | 解决 |
|---|---|---|
| `Errno 10048` | 上次端口未释放（uv→python 父子链残留） | 按 §14.7.3 步骤 1 完整清理 |
| `[WinError 10013]` | 端口被其他应用占用 | `Get-NetTCPConnection` 诊断，`Stop-Process` |
| `[WinError 10048]` | Tauri 内部 Socket 复用冲突 | 完全重启 Tauri |
| Vite `@/` 路径解析失败 | 在 `frontend/renderer` 子目录启动而非项目根目录 | `cd d:/java/agentprojects/agentx` 后启动 |
| Tauri 桌面窗口不出现 | Rust 首次编译未完成 / WebView2 缺失 | 等编译完成 / 安装 WebView2 Runtime |
| 后端 `agent stuck in repeating tool-call loop` | LLM 陷入重复工具调用循环 | 已修复：见 `backend/app/deep/execution.py` 重复检测 + `asyncio.sleep(0.05)` |

## §14.7.7 dev 进程长存规范

- **dev 是长进程**，启动后用 `CheckCommandStatus` / `GetTerminalOutput` 轮询日志观察
  `AgentX Tauri shell started` + uvicorn 监听即可，**不要等进程结束**。
- 重启前必须先关闭上一次 dev 进程（Ctrl+C 或上文的 Stop-Process），否则会端口冲突。

## §14.7.8 进程筛选规范（精准而非全杀）

⚠️ **禁止** `Get-Process -Name python | Stop-Process -Force`——会误杀同机的其他项目（如 Hermes）。

**推荐做法**（按 CommandLine 精准筛选）：

```powershell
# agentx 相关 python 进程
Get-Process -Name python -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*agentx*' } |
    Select-Object Id, ProcessName, CommandLine

# agentx 相关 node 进程（Vite）
Get-Process -Name node -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*vite*' -or $_.CommandLine -like '*agentx*' } |
    Select-Object Id, ProcessName, CommandLine
```

> 此规范可沉淀为 [learned_skill_experience] "Windows下精准筛选并重启指定项目进程技能"。