# 开发模式开关化 + 跨平台 console 化 + 修卡 "后端启动中"

> **状态**：草案 v1
> **创建日期**：2026-07-07
> **关联**：[AGENTS.md §14.7](../AGENTS.md)、[src-tauri/src/commands/app.rs:88](../src-tauri/src/commands/app.rs#L88-L106)、[src-tauri/src/backend/handle.rs:53](../src-tauri/src/backend/handle.rs#L53-L137)、[frontend/renderer/components/chat/SessionList.tsx:42](../frontend/renderer/components/chat/SessionList.tsx#L42-L76)、[frontend/renderer/App.tsx:96](../frontend/renderer/App.tsx#L96-L97)
> **范围**：Rust 主进程 + Tauri 命令 + renderer（不含 Python 后端协议）

---

## 1. 背景与动机

### 1.1 用户主诉

> "每次切换开发模式，就会卡在后端启动中…我希望把开发模式做成开关，下次重启的时候，再按照是否是开发模式启动。开发模式启动的话，后端以 powershell 或者 mac 的 console 的方式启动。"

### 1.2 拆解为 3 个独立诉求

| # | 诉求 | 现状 | 期望 |
|---|---|---|---|
| 1 | **不卡"后端启动中" mask** | dev_mode 切换时永远停在 mask | 重启后端路径必须发 `python:status=ready` |
| 2 | **dev_mode 是持久化配置** | store.devMode 已经持久化，且 `PythonHandle::start` 每次启动读它 | 已满足；本次强化语义即可 |
| 3 | **跨平台 console 化** | Windows 用 powershell `-NoExit`；macOS / Linux 静默走 tokio | macOS / Linux 用 Terminal.app / x-terminal-emulator 拉起 |

---

## 2. 根因分析（事实陈述）

### 2.1 `app_restart_backend` 路径里**没有发 `Ready` 事件**

[src-tauri/src/commands/app.rs:137-194](../src-tauri/src/commands/app.rs#L137-L194) 流程：
1. `state.lock().take()` 取旧 handle，`stop()` 杀进程
2. `tokio::time::sleep(800ms)` 等端口释放
3. `PythonHandle::start(...)` 起新 supervisor（supervisor 进 `supervise::loop` **发一次 `Starting`**）
4. **`reqwest::get("http://127.0.0.1:8123/")` 自己轮询 30s**，超时返回 `ok:false`

问题：第 4 步成功不 emit 任何 `python:status`，失败也不 emit。

### 2.2 前端 mask 解锁完全依赖事件

[frontend/renderer/App.tsx:96-97](../frontend/renderer/App.tsx#L96-L97)：
```ts
const showStartingMask = pythonStatus === "starting";
const showGiveUpMask = pythonStatus === "giving_up";
```

`pythonStatus` 只由 `onPythonStatus` 事件流更新（[frontend/renderer/App.tsx:47-54](../frontend/renderer/App.tsx#L47-L54)）。`null` / `ready` / `crashed` 任何状态都能让 mask 解开——**但必须收到事件**。

### 2.3 dev_mode 切换之所以卡（专属根因）

dev_mode 路径下 `spawn_child` 走 `powershell.exe -NoExit ...` ([handle.rs:284-318](../src-tauri/src/backend/handle.rs#L284-L318))，supervisor 进 `match Some(child, ps_pid)` 分支后：
- `[handle.rs:199] if !dev_mode { pipe stdout/stderr }` —— dev_mode=true 跳过 pipe（保留 PS 窗口）
- `child.wait().await` 等 powershell.exe 退出 —— **`-NoExit` 永远不退**

→ supervisor **永阻塞**，`Ready` / `Crashed` / `GivingUp` **永远不会 emit**。前端 mask 永远停在 `starting`。

### 2.4 生产模式切换并不卡（但**也有 bug**）

生产模式 `spawn_child` 走原 tokio `Command::new("uv")`，`child.wait()` 等 uv+python 子进程。uvicorn 持续运行 → `wait()` 也阻塞不返回。但 supervisor 在 `child.wait()` 之前已经有 `ensure_port_free_or_kill()` 杀残留进程，dev_mode 下残留 powershell 进程不会被这个清理（功能上是 `taskkill /T /F` 但 powershell 关闭后子进程树已经退出）。

用户**只切换过 dev_mode=true 路径**，所以只看到 dev_mode 卡死。生产模式重启后端走的是 `[Settings]` 入口的 `restartBackend()`，用户极少触发。

但本次改动将**统一两条路径的 wait + 事件流**，一劳永逸。

---

## 3. 设计目标

| ID | 目标 | 验收 |
|---|---|---|
| G1 | `app:restartBackend` 路径通过 `wait_for_ready` 发 `Ready` | UI mask 30s 内必然解开（成功 / 失败都有信号） |
| G2 | `wait_for_ready` 30s 超时时也 emit `GivingUp` | 走 "启动失败" 兜底 mask |
| G3 | `set_dev_mode` 不再自动重启后端 | 写完 store 立即返回；下次 supervisor spawn 时按新值 |
| G4 | dev_mode=true 启动时跨平台拉起 native console | Win: PowerShell `-NoExit`；macOS: Terminal.app；Linux: x-terminal-emulator |
| G5 | dev_mode=true 的 console 拉起失败时降级 tokio | `spawn_child` 已有降级模板，扩展应用 |
| G6 | 现存 `Ready` 事件契约不变 | [main.py::_event_generator](../backend/app/main.py)、[lib/api/chat.ts](../frontend/renderer/lib/api/chat.ts) 不需要改 |
| G7 | 不破坏 §14.7 重启 SOP | `app_restart_backend` 流程保持 4 步 |

---

## 4. 架构变更

### 4.1 控制流对比

**改动前**（mask 永远卡 starting）：
```
[renderer]            [Tauri command]              [supervisor / app_restart_backend]
click devMode  ->     app_set_dev_mode(enabled)
                       ├── store::set_dev_mode(enabled)
                       └── app_restart_backend
                              ├── stop old handle
                              ├── sleep 800ms
                              ├── PythonHandle::start()  ──► supervise ──► emit Starting ──► child.wait() 永阻塞
                              └── reqwest::get loop (成功不再 emit)
                       <- { ok:true }
[mask 永驻 starting 因为 Rust 不再 emit Ready]
```

**改动后**（事件驱动 + 跨平台 console）：
```
[renderer]                    [Tauri command]                  [supervisor]
click devMode  ->             app_set_dev_mode(enabled)
                                └── store::set_dev_mode(enabled) (立即返回)

[下次启动 / 显式重启后端]      app_restart_backend
                                ├── stop old
                                ├── sleep 800ms
                                ├── PythonHandle::start()  ──►
                                │     ├─ if dev_mode && windows: spawn powershell -NoExit
                                │     ├─ if dev_mode && macos:   spawn Terminal.app via AppleScript
                                │     ├─ if dev_mode && linux:   spawn x-terminal-emulator -e bash
                                │     └─ supervise loop        ──► emit Starting
                                └── new_handle.wait_for_ready() ──►
                                      ├─ 连续 2 次 200  →  emit Ready      (G1)
                                      └─ 30s 超时        →  emit GivingUp   (G2)
```

### 4.2 数据流变化

`tauri-plugin-store::devMode` 已经在以下时刻读取：

| 时机 | 当前读法 | 改后读法 |
|---|---|---|
| 启动时 `PythonHandle::start` 入口 | `let dev_mode = store::get_dev_mode(&app)` | 不变 |
| `app_set_dev_mode(enabled)` | 写 store + 立即 restart_backend | **只写 store**，不 restart |

> 注：用户在 SessionList 切换 dev_mode 后，**Tauri 窗口内的旧后端仍按旧 dev_mode 运行**；下一次启动应用 / 用户点 `[Settings] → 重启后端` 才生效。这与用户的"下次启动按 dev_mode 启动"语义一致。

### 4.3 store / 命令契约

`app_get_dev_mode` / `app_set_dev_mode` 函数签名不变。**返回值 `RestartResult` 现在仅在"重新启动后端"语义下有意义，但为了兼容现有前端 onClick 处理，将返回 `ok:true, message:"devMode stored; backend restart required"`**。

---

## 5. 模块改动

### 5.1 Rust：`src-tauri/src/backend/handle.rs`

| 位置 | 改动 |
|---|---|
| `wait_for_ready` ([handle.rs:83-117](../src-tauri/src/backend/handle.rs#L83-L117)) | timeout 路径加 `let _ = app.emit("python:status", PythonStatus::GivingUp); return false;` （**G2**） |
| `spawn_child` ([handle.rs:264-357](../src-tauri/src/backend/handle.rs#L264-L357)) | macOS / Linux dev_mode 分支：`#[cfg(target_os = "macos")] use_powershell = dev_mode;` 改用 `open -a Terminal`，`#[cfg(target_os = "linux")] use_powershell = dev_mode;` 改用 `x-terminal-emulator -e bash` |
| 现有 Windows powershell 分支 | 不变 |
| spawn 失败 → `use_powershell = false` 降级逻辑 | 不变（已存在） |

### 5.2 Rust：`src-tauri/src/commands/app.rs`

| 位置 | 改动 |
|---|---|
| `app_restart_backend` ([commands/app.rs:137-194](../src-tauri/src/commands/app.rs#L137-L194)) | **删掉自家 `reqwest::get` 轮询（[lines 162-182](../src-tauri/src/commands/app.rs#L162-L182)）**，替换为 `new_handle.wait_for_ready(&app, PYTHON_PORT).await`；结果映射成 `RestartResult { ok: wait_ok, message: Some(...) }` （**G1**） |
| `app_set_dev_mode` ([commands/app.rs:88-106](../src-tauri/src/commands/app.rs#L88-L106)) | **去掉 `app_restart_backend(app, state).await`** 调用，只写 store 并返回 `{ ok:true, message:"devMode stored; ..." }` （**G3**） |

### 5.3 Frontend：`SessionList.tsx`

| 位置 | 改动 |
|---|---|
| `handleToggleDevMode` ([SessionList.tsx:60-76](../frontend/renderer/components/chat/SessionList.tsx#L60-L76)) | `await setDevMode(next)` 返回后**仅写本地状态**，按钮 disabled 期间改用本地 `setDevModeBusy(false)` 立即解锁；按钮文案改为 `"开发模式·待重启"`（区别于 `·开` 的常驻）；hover tooltip：`"设置已保存，下次启动应用或 [设置→重启后端] 后生效"` |

> **UX 决策**：store 写成功的反馈**不再来自后端事件**，改由本地文案 + tooltip 表达"待生效"状态；这是把"开关"语义做得**可见**而非后台静默。

### 5.4 Frontend：`App.tsx`

| 位置 | 改动 |
|---|---|
| `showStartingMask` / `showGiveUpMask` 逻辑 | 不变 —— 这俩仍由事件驱动。`Ready` / `Crashed` / `GivingUp` / 窗口 `null` 都会解 starting。`wait_for_ready` 永真 / 永超时都会产出确定事件 |
| **新增**：启动 mask 进入后 30s 计时器，超时后**强制检查** `GET /` 一次，仍 200 则 trigger `restartBackend` | 这是**回退兜底**，正常路径不应触发 |

### 5.5 文档

| 位置 | 改动 |
|---|---|
| `AGENTS.md` §14.7 重启 SOP | 在「入口：永远 `npm run dev`」段落下追加：「开发模式开关在 renderer 内修改后不立即生效；下一次应用启动或显式 restart_backend 时按新值 spawn」 |
| `AGENTS.md` §14.2 `PythonHandle::start` | 追加 macOS / Linux dev_mode console 拉起的命令模板与 PID 跟踪说明 |

---

## 6. 平台差异

| 平台 | dev_mode=true spawn | dev_mode=false spawn |
|---|---|---|
| **Windows** | `powershell.exe -NoExit -Command "Set-Location -LiteralPath '<cwd>'; uv run python -m app.main"` （保留） | `uv run python -m app.main` via tokio （保留） |
| **macOS** | `open -a Terminal "<cwd>" --args /bin/bash -lc "uv run python -m app.main"` + AppleScript 内部 `tell application "Terminal" to do script "cd '<cwd>' && uv run python -m app.main; exec /bin/bash"` | 同 Windows（tokio） |
| **Linux** | `x-terminal-emulator -e bash -lc "cd '<cwd>' && uv run python -m app.main; exec bash"`；未安装时降级 `gnome-terminal` / `konsole` | 同 Windows（tokio） |

**PID 跟踪**：所有 console 拉起的 process 都是 tokio::process::Child，直接拿 `child.id()` 作为 `current_pid`，并把进程组 PID 记到 `inner.current_pid`；`stop()` 沿用现有 `kill_tree` Windows / Unix 负 PID 处理。

**降级**：macOS `osascript` 失败 / Linux 无 x-terminal-emulator → use_powershell=false，走普通 tokio 启动 + 日志面板。

---

## 7. 错误处理与测试

### 7.1 错误路径矩阵

| 入口 | dev_mode | 失败 | 行为 |
|---|---|---|---|
| setup() | true | PowerShell 缺失 | supervisor 已有的降级：tokio + 日志面板 |
| setup() | true | macOS AppleScript 失败 | 降级 tokio |
| setup() | true | Linux 无终端 | 降级 tokio + 一行 ERROR 日志 |
| app_restart_backend | 不关心 | 后端 30s 没起来 | wait_for_ready emit `GivingUp` → UI 显示 "后端启动失败" mask |
| app_set_dev_mode | n/a | store 写失败 | 返回 `Err(String)`，renderer 回滚 setDevModeLocal |

### 7.2 测试场景

1. **单元**（不依赖 GUI）：
   - `Rust`: `wait_for_ready` 在 30s 内 port unreachable 时 emit GivingUp（mock 8123 关着）
   - `Rust`: `spawn_child` macOS 分支编译过（`#[cfg(target_os)]` 静态）
   - `Rust`: `spawn_child` Linux 分支编译过
2. **集成**（命令 + store）：
   - `app_set_dev_mode(true)` → store 写入后立即返回（< 50ms）；不调用 `app_restart_backend`
   - `app_restart_backend` → 至少收到一次 `python:status=ready` 或 `giving_up`（mock 杀掉后端 / 故意起不来）
3. **手动**：
   - Win 切 dev_mode=true → 不卡 mask（重启应用后才生效）；下次启动看到 PowerShell 窗口
   - macOS 切 dev_mode=true → 看到 Terminal.app 新窗口
   - Linux 切 dev_mode=true → 看到 x-terminal-emulator 新窗口

### 7.3 风险

- **R1**：macOS sandbox 限制——`open -a Terminal` 在 daemon / headless 环境失败。降级 tokio 已覆盖。
- **R2**：Linux x-terminal-emulator 缺失。降级 + 错误日志。
- **R3**：现有用户在 tauri-plugin-store 已存 `devMode=false`，首次升级到这个版本——**无影响**（store key 完全兼容）。
- **R4**：改动 `app_restart_backend` 不发 `Ready` → 前端 mask 兜底依赖。已通过 §3 G1/G2 + mask 30s 兜底双重保险。

---

## 8. 不在本期范围（YAGNI）

- ❌ UI 顶层放 dev_mode 开关（保留侧边栏底部按钮）
- ❌ 后端持久化"切换前后端状态"以避免窗口内重启丢会话
- ❌ dev_mode 控制 stdout 染色 / DEBUG log level
- ❌ 引入 `nix` crate 或 `pty` crate 做真正的 PTY 控制（`-NoExit` 已足够）

---

## 9. 验收清单（DoD）

- [ ] `app_restart_backend` 路径必发 `Ready` 或 `GivingUp` 之一
- [ ] `app_set_dev_mode` 不再 restart_backend；store 写入返回 < 50ms
- [ ] Windows dev_mode 启动 PowerShell `-NoExit` 窗口（保留）
- [ ] macOS dev_mode 启动 Terminal.app 窗口
- [ ] Linux dev_mode 启动 x-terminal-emulator / gnome-terminal / konsole 之一
- [ ] 前端 mask 30s 后端起不来 → 解 mask + 显示 "启动失败" 兜底页
- [ ] 前端 dev_mode 切换按钮 disabled 时间 < 1s（不再等服务端 wait）
- [ ] §14.7 重启 SOP 文档更新
- [ ] 所有改动 < 200 行 Rust + < 100 行 TS（绝大多数是 spawn 命令模板）

---

## 10. OpenSpec 提案关联

变更应走 OpenSpec 提案流程：

```
openspec/changes/2026-07-07-dev-mode-toggle-console/
├── proposal.md          # 提案摘要（1-2 段）
├── design.md            # ← 本文件
├── tasks.md             # 任务分解（★ 由 writing-plans skill 出）
└── specs/
    └── (delta spec，定义 rust 模块 + frontend 类型变更)
```

提出 OpenSpec 之前，先完成本 spec 的 §9 DoD 检查 + 用户复核。
