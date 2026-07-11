# 启动 / 重启前后端 SOP

> 原 `AGENTS.md` §14.7 拆分。这套流程是 2026-07-04 / 2026-07-09 反复实战出来的。
> 阅读时机：首次启动、端口冲突、Errno 10048、dev 进程残留、Tauri 桌面窗口不出现。

---

## §14.7.0 强约束：脚本化启停

> ⚠️ **本项目所有 dev session 操作一律走 [`scripts/`](file:///d:/java/agentprojects/agentx/scripts/) 下的脚本**。
>
> - ✅ **推荐**：项目根目录执行 `agentx-start` / `agentx-stop` / `agentx-restart` / `agentx-health`
> - ✅ **等价**：`pwsh scripts/start.ps1` 等 PowerShell 原生调用
> - ❌ **禁止**：裸 `pnpm tauri dev` / `Stop-Process -Name python` / `netstat` / `taskkill`
>
> 设计原因：进程精准筛选必须按 CommandLine（详见 §14.7.8），
> 否则会误杀同机的其他项目（Hermes、Qoder IDE 的 python extension 等）。
>
> 完整的脚本设计与错误处理表见 §14.7.9。

## §14.7.1 启动入口

> **首选**：项目根目录 `agentx-start`（详见 §14.7.9）。
> 下文说明的"内部启动命令"仅供 agentx-start 内部调用，人工排查时也可参考。

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
| 后端 `agent stuck in repeating tool-call loop` | LLM 陷入重复工具调用循环 | 已修复：见 `backend/app/deepagent/approval_runner.py` 重复检测 + `asyncio.sleep(0.05)` |

## §14.7.7 dev 进程长存规范

- **dev 是长进程**，启动后用 `CheckCommandStatus` / `GetTerminalOutput` 轮询日志观察
  `AgentX Tauri shell started` + uvicorn 监听即可，**不要等进程结束**。
- 重启前必须先关闭上一次 dev 进程（Ctrl+C 或上文的 Stop-Process），否则会端口冲突。

## §14.7.8 进程筛选规范（精准而非全杀）

⚠️ **禁止** `Get-Process -Name python | Stop-Process -Force`——会误杀同机的其他项目（如 Hermes）。

> **首选**：直接执行 `agentx-stop`（见 §14.7.9）。脚本内部已实现下面这套白名单 + 黑名单逻辑。
> 下文的手动诊断命令仅供排查"为什么 stop 没杀掉某进程"时使用，不要直接 Stop-Process。

**白名单 + 黑名单策略**（脚本内部实现细节）：

| 类别 | 关键字 |
|---|---|
| 白名单（AgentX dev session 启动参数） | `tauri dev` / `pnpm tauri` / `vite` / `uvicorn` / `app.main` / `backend.app` / `src-tauri` / `target\debug\agentx.exe` / `target\release\agentx.exe` |
| 黑名单（即使白名单命中也排除） | `.qoder` / `qoder` / `ide\plugins` |

**手动诊断命令**（仅排查用，不要直接 Stop-Process）：

```powershell
# 查看候选进程（含 CommandLine 完整字段）
Get-Process -Name python,node,uv,agentx -ErrorAction SilentlyContinue |
    Select-Object Id, ProcessName, StartTime, CommandLine |
    Format-Table -AutoSize -Wrap

# 仅看 CommandLine 是否包含 agentx 相关关键字（白名单 + 黑名单粗筛）
Get-Process -Name python,node,uv -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*tauri dev*' -or $_.CommandLine -like '*vite*' -or $_.CommandLine -like '*uvicorn*' -or $_.CommandLine -like '*app.main*' } |
    Select-Object Id, ProcessName, CommandLine
```

> 此规范可沉淀为 [learned_skill_experience] "Windows下精准筛选并重启指定项目进程技能"。

---

## §14.7.9 启停脚本设计（scripts/）

> 创建于 2026-07-11。把 §14.7.3 / §14.7.4 / §14.7.5 / §14.7.8 的手工操作封装为幂等脚本。

### 脚本清单

| 脚本 | 命令别名 | 职责 | 关键行为 |
|---|---|---|---|
| [`scripts/start.ps1`](file:///d:/java/agentprojects/agentx/scripts/start.ps1) | `agentx-start` | 启动 dev session | 前台阻塞（或 `-NoWait` 后台）；等 8123 listen；写入 `data/logs/tauri-dev.{log,err}` |
| [`scripts/stop.ps1`](file:///d:/java/agentprojects/agentx/scripts/stop.ps1) | `agentx-stop` | 停止 dev session | 白名单 + 黑名单精准清理；最多 6 轮 × 2s；端口复检 |
| [`scripts/restart.ps1`](file:///d:/java/agentprojects/agentx/scripts/restart.ps1) | `agentx-restart` | 重启 dev session | `stop` + `start` 组合；参数透传 |
| [`scripts/health-check.ps1`](file:///d:/java/agentprojects/agentx/scripts/health-check.ps1) | `agentx-health` | 健康探测 | 端口 + 关键端点 + 进程家族探测；`-Wait` 延迟探测 |

`.cmd` 文件是 PowerShell 脚本的薄封装，方便 Windows cmd / PowerShell 直接输入别名调用。

### start.ps1 退出码约定

| 退出码 | 含义 |
|---|---|
| 0 | 启动成功（或后台模式已 fork） |
| 2 | 预检失败（找不到 `package.json`，非项目根目录） |
| 3 | 端口被占用且未指定 `-Clean` |
| 4 | 工具链缺失（pnpm/node/cargo） |
| 5 | 启动异常（捕获到 throw） |

### stop.ps1 退出码约定

| 退出码 | 含义 |
|---|---|
| 0 | 端口空闲 / 清理成功 |
| 1 | 部分端口仍占用（TimeWait 或非 AgentX 占用） |

### 进程筛选算法（stop.ps1 / health-check.ps1 共用）

```powershell
# 伪代码：白名单 + 黑名单双重校验
function Test-ProjectProcess($proc) {
    # 1. 项目二进制名兜底
    if ($proc.ProcessName -in 'agentx', 'AgentX') { return $true }

    # 2. 无 CommandLine（如 System Idle）直接跳过
    $cl = $proc.CommandLine
    if (-not $cl) { return $false }

    # 3. 黑名单优先（Qoder IDE 等并行项目）
    foreach ($kw in $EXCLUDE_KEYWORDS) {     # .qoder / qoder / ide\plugins
        if ($cl.ToLowerInvariant().Contains($kw)) { return $false }
    }

    # 4. 白名单：必须命中 AgentX dev 启动参数
    foreach ($kw in $PROJECT_KEYWORDS) {    # tauri dev / pnpm tauri / vite / uvicorn / app.main / backend.app / src-tauri / target\debug\agentx.exe
        if ($cl.ToLowerInvariant().Contains($kw)) { return $true }
    }

    return $false
}
```

### 常见使用模式

```powershell
# 1) 日常开发（前台运行，Ctrl+C 中断）
agentx-start

# 2) 写代码时后台运行（编辑器内联终端腾出来）
agentx-start -NoWait
# 之后查看日志：
Get-Content data/logs/tauri-dev.log -Wait

# 3) 端口冲突 / dev 残留
agentx-start -Clean        # 启动前自动 stop
agentx-stop -Force         # 或手动强制清

# 4) 调试 agent 配置后无需重启整个 Tauri
#    Tauri 主进程会监听 tauri-plugin-store 配置变化并自动 reload 后端
Invoke-RestMethod -Method POST -Uri 'http://127.0.0.1:8123/api/config/reload'

# 5) CI / 自动化场景
agentx-stop                # 确保干净状态
agentx-start -NoWait       # 后台启动
Start-Sleep -Seconds 30    # 等 Rust 编译 + uvicorn listen
agentx-health              # 验证就绪
```

---

## §14.7.10 脚本错误处理表

| 现象 | 原因 | 解决方法 |
|---|---|---|
| `agentx-start` 退出码 3 | 8123/5173 已被占用 | `agentx-stop` 或 `agentx-start -Clean` |
| `agentx-start` 退出码 4 | 缺 pnpm/node/cargo | 按提示安装（pnpm: `npm i -g pnpm`） |
| `agentx-start` 等 90s 仍未 listen | Rust 首次编译超过 90s | 改用 `agentx-start -HealthTimeoutSec 240`，或先手动 `pnpm tauri dev` 触发编译 |
| `agentx-stop` 退出码 1 仍有 LISTEN | TimeWait（1-2 分钟）或非 AgentX 进程占用 | 用 `agentx-health` 查 pid，再 `Stop-Process -Id <pid> -Force` 手动清 |
| `agentx-stop` 把 Qoder IDE python 进程也杀了 | 旧版本误杀（关键词含 `agentx`） | 已修复：白名单改用启动参数 + 黑名单排除 `.qoder`；升级到 2026-07-11+ 的脚本 |
| `agentx-restart` 卡住 | start 阶段 dev session 未退出 | 另一终端执行 `agentx-stop -Force` 兜底 |
| `agentx-health` 报 DEGRADED 但端口 LISTEN | 某个端点超时（TEI/Milvus 慢） | 用 `agentx-health` 输出看具体哪条 FAIL，单独 `/` 或 `/api/skills` 仍 OK 即视为活 |
| `data/logs/tauri-dev.log` 没有输出 | 后台模式 `-NoWait` 后日志缓冲未 flush | `Get-Content data/logs/tauri-dev.log -Wait` 实时跟；或前台模式 `agentx-start` |