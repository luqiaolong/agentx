# 开发模式开关化 + 跨平台 console 化 + 修卡 "后端启动中" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 dev_mode 切换卡"后端启动中" mask 的根因；让 dev_mode 成为不立即重启的持久化开关；macOS / Linux 在 dev_mode 下用 native console 拉起 Python 后端。

**Architecture:**
- Rust 主进程：`app_restart_backend` 复用 `PythonHandle::wait_for_ready()`（统一事件源），`app_set_dev_mode` 只写 store 不 restart；`spawn_child` 在 macOS / Linux 走 Terminal.app / x-terminal-emulator 拉起。
- Frontend：mask 30s 后端起不来时补兜底；切换 dev_mode 后按钮态 "开发模式·待重启"。

**Tech Stack:** Rust 1.77+ (tokio)、Tauri 2.x、React 18 + TypeScript + zustand。

**OpenSpec 文档：**
- Spec：[docs/superpowers/specs/2026-07-07-dev-mode-toggle-and-console-design.md](../../superpowers/specs/2026-07-07-dev-mode-toggle-and-console-design.md)

---

## 工作约定

1. **隔离**：在本仓库 `d:\java\agentprojects\agentx` 直接实施，不创建 worktree（用户为单人开发，无 PR review 流程）。
2. **TDD**：先写/调整 Rust 单元测试覆盖 `wait_for_ready` GivingUp 路径、`app_set_dev_mode` 不 restart 路径；cargo test 验证。
3. **提交粒度**：每完成一个 Task 立即 commit（commit message 用规范前缀 `feat:` / `fix:` / `docs:` / `refactor:`）。
4. **不引入新 crate**：除非必须，本期不引入 `nix` / `pty` / `tokio-pty-process`。Unix 拉 terminal 用 `Command::new` 直跑二进制。
5. **不破坏 SSE 事件契约**：[main.py::_event_generator](../../../../backend/app/main.py)、[lib/api/chat.ts](../../../../frontend/renderer/lib/api/chat.ts)、[useChatStream.ts](../../../../frontend/renderer/hooks/useChatStream.ts) 不需要改。

---

## File Structure

### 修改（Rust 主进程）
```
src-tauri/src/backend/handle.rs              ← wait_for_ready 超时 emit GivingUp；spawn_child 加 mac/linux 分支
src-tauri/src/commands/app.rs                 ← app_restart_backend 用 wait_for_ready；app_set_dev_mode 不 restart
```

### 修改（Frontend）
```
frontend/renderer/components/chat/SessionList.tsx   ← 按钮文案 + tooltip + devModeBusy 立即解锁
frontend/renderer/App.tsx                           ← mask 30s 兜底 resetState
```

### 修改（文档）
```
AGENTS.md                                       ← §14.2 + §14.7 补充 dev_mode 跨平台与「切换不立刻重启」
docs/superpowers/specs/2026-07-07-dev-mode-toggle-and-console-design.md ← 已存在，本 plan 引用即可
```

### 不创建新文件（保持 diff 收敛）
- 不需要新 Rust 模块（`backend/console.rs` 之类的概念在现有 `spawn_child` 里扩平台即可）
- 不需要新 store key
- 不需要新 Tauri command（仅修改现有 2 个）

---

## Task 1：wait_for_ready 超时 emit GivingUp

**Files:**
- Modify: [src-tauri/src/backend/handle.rs:83-117](../../../../src-tauri/src/backend/handle.rs#L83-L117)

- [ ] **Step 1：定位 dead branch**

打开 `src-tauri/src/backend/handle.rs`，定位 `wait_for_ready` 内 `deadline` 检查分支（[handle.rs:96-102](../../../../src-tauri/src/backend/handle.rs#L96-L102)），确认现在超时只返回 `false`，前端永远收不到 `GivingUp`。

- [ ] **Step 2：补丁 emit**

把
```rust
if tokio::time::Instant::now() > deadline {
    log::warn!(
        "python wait_for_ready timed out after {}ms",
        HEALTH_TIMEOUT_MS
    );
    return false;
}
```
改成
```rust
if tokio::time::Instant::now() > deadline {
    log::warn!(
        "python wait_for_ready timed out after {}ms",
        HEALTH_TIMEOUT_MS
    );
    let _ = app.emit("python:status", PythonStatus::GivingUp);
    return false;
}
```

- [ ] **Step 3：编译验证**

Run: `cd d:\java\agentprojects\agentx\src-tauri && cargo check`
Expected: 0 errors, 0 new warnings。

- [ ] **Step 4：commit**

```bash
cd d:\java\agentprojects\agentx && git add src-tauri/src/backend/handle.rs && git commit -m "fix(backend): emit python:status=giving_up on wait_for_ready timeout"
```

---

## Task 2：app_restart_backend 复用 wait_for_ready

**Files:**
- Modify: [src-tauri/src/commands/app.rs:137-194](../../../../src-tauri/src/commands/app.rs#L137-L194)

- [ ] **Step 1：定位自家 reqwest 轮询**

打开 `src-tauri/src/commands/app.rs`，定位 `app_restart_backend` 内 `// 4. 轮询健康端点` 注释后到 `}` 的整段轮询代码（[commands/app.rs:162-182](../../../../src-tauri/src/commands/app.rs#L162-L182)）。

- [ ] **Step 2：替换为 wait_for_ready**

把第 162-182 行整段（即函数末尾的 `let deadline = ...; while ... { ... }; ...` 整块）替换为：

```rust
    // 4. 等候 supervisor ready（统一事件源），30s 超时 emit GivingUp。
    let ready = new_handle.wait_for_ready(&app, PYTHON_PORT).await;
    let mut guard = state
        .lock()
        .map_err(|e| format!("state lock poisoned: {}", e))?;
    if ready {
        logger::append_log(&app, "[main] python backend restarted and ready");
        *guard = Some(new_handle);
        Ok(RestartResult {
            ok: true,
            message: Some("backend ready".into()),
        })
    } else {
        logger::append_log(&app, "[main] python backend restart timeout");
        *guard = Some(new_handle);
        Ok(RestartResult {
            ok: false,
            message: Some("backend restart timeout".into()),
        })
    }
```

- [ ] **Step 3：检查 Duration / reqwest import 是否仍需要**

`reqwest::Client` 和 `Duration` 在本文件其它地方还在用（如 `app_reload_backend_config`）—— 保留。`reqwest::get` 在本函数不再用，但 `reqwest` crate 仍被使用 —— Rust 会警告 unused import：把 `app_restart_backend` 函数体内的 `reqwest::get(&url)` 调用删掉即可，crate 自身的 `use` 在 file 顶部早已写好，不会出现未使用 import。

- [ ] **Step 4：编译验证**

Run: `cd d:\java\agentprojects\agentx\src-tauri && cargo check`
Expected: 0 errors, 0 new warnings（不出现 unused import）。

- [ ] **Step 5：commit**

```bash
cd d:\java\agentprojects\agentx && git add src-tauri/src/commands/app.rs && git commit -m "refactor(commands): app_restart_backend reuses wait_for_ready for unified event source"
```

---

## Task 3：app_set_dev_mode 不重启后端

**Files:**
- Modify: [src-tauri/src/commands/app.rs:88-106](../../../../src-tauri/src/commands/app.rs#L88-L106)

- [ ] **Step 1：替换函数体**

把 `app_set_dev_mode` 整个函数体（函数签名保持不变）替换为：

```rust
#[tauri::command]
pub async fn app_set_dev_mode(
    app: AppHandle,
    enabled: bool,
) -> Result<RestartResult, String> {
    let prev = store::get_dev_mode(&app);
    if prev == enabled {
        return Ok(RestartResult {
            ok: true,
            message: Some("devMode unchanged".into()),
        });
    }
    store::set_dev_mode(&app, enabled);
    logger::append_log(
        &app,
        &format!("[main] devMode -> {} (stored; backend restart deferred)", enabled),
    );
    Ok(RestartResult {
        ok: true,
        message: Some("devMode stored; restart backend to apply".into()),
    })
}
```

> 注意：`State<'_, Mutex<Option<PythonHandle>>>` 参数已去掉。本命令不再访问 state。

- [ ] **Step 2：从 invoke_handler! 中验证**

[lib.rs:108-109](../../../../src-tauri/src/lib.rs#L108-L109) 的 `invoke_handler!` 注册不需要改（macro 自动按函数参数推签名）。

- [ ] **Step 3：编译验证**

Run: `cd d:\java\agentprojects\agentx\src-tauri && cargo check`
Expected: 0 errors。前端 `setDevMode(true)` 不再传 State，lib.rs 不动也行——但保险起见看一下 `commands::app::app_set_dev_mode` 在 `invoke_handler!` 里的位置是否还要 `State` 绑定的 import；不需要。

- [ ] **Step 4：commit**

```bash
cd d:\java\agentprojects\agentx && git add src-tauri/src/commands/app.rs && git commit -m "refactor(commands): app_set_dev_mode stores config without restarting backend"
```

---

## Task 4：spawn_child 扩 macOS / Linux dev_mode 分支

**Files:**
- Modify: [src-tauri/src/backend/handle.rs:283-318](../../../../src-tauri/src/backend/handle.rs#L283-L318)

- [ ] **Step 1：定位 use_powershell 平台 guard**

定位 `#[cfg(windows)] let mut use_powershell = dev_mode;` 与 `#[cfg(not(windows))] let mut use_powershell = false;` 两行。

- [ ] **Step 2：替换为按平台分别拉起**

把这两行替换为：

```rust
    let mut use_powershell = dev_mode;
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    let mut use_powershell = false;
```

> 仅在 Windows / macOS / Linux 三平台开启 dev_mode 的 console 拉起；其它平台（如 BSD）按 `false` 走 tokio。

- [ ] **Step 3：扩 dev_mode 分支**

把 `if use_powershell { ...powershell... }` 整块（[handle.rs:288-318](../../../../src-tauri/src/backend/handle.rs#L288-L318)）在 Windows 分支**之上**分别加：

**macOS 分支**（放在 `if use_powershell {` 开头 Windows 块之前）：

```rust
    #[cfg(target_os = "macos")]
    if use_powershell {
        let cwd_str = cwd.to_string_lossy().to_string();
        let cwd_escaped = cwd_str.replace('\'', "'\\''");
        let ps_script = format!(
            "cd '{}' && uv run python -m app.main; exec /bin/bash",
            cwd_escaped
        );
        // 通过 osascript 让 Terminal.app 开新窗口并执行脚本
        let mut cmd = Command::new("osascript");
        cmd.args([
            "-e",
            &format!(
                r#"tell application "Terminal" to do script "{}""#,
                ps_script.replace('"', r#"\""#)
            ),
        ])
        .envs(env);
        match cmd.spawn() {
            Ok(child) => return Some((child, None)),
            Err(e) => {
                let msg = format!("dev-mode macos osascript spawn error: {}", e);
                log::warn!("{}", msg);
                logger::append_log(app, &msg);
                use_powershell = false;
            }
        }
    }
```

> 注意：`ps_pid` 第二项对 macOS / Linux 不重要（osascript 退出后 Terminal 仍在，主进程树 `child.wait()` 需要等 osascript 退出 + Terminal 实际进程让 supervisor 解锁）。但因为我们不需要走 `child.wait()` 解锁路径（mask 由事件驱动），这里直接 None 即可，supervisor 接到 `[handle.rs:248] GivingUp` 路径。

- [ ] **Step 4：加 Linux 分支**

紧接 macOS 分支下加：

```rust
    #[cfg(target_os = "linux")]
    if use_powershell {
        let cwd_str = cwd.to_string_lossy().to_string();
        let cwd_escaped = cwd_str.replace('\'', "'\\''");
        let bash_script = format!(
            "cd '{}' && uv run python -m app.main; exec bash",
            cwd_escaped
        );
        // 优先 x-terminal-emulator，回退 gnome-terminal / konsole
        let terminals: [(&str, &[&str]); 3] = [
            ("x-terminal-emulator", &["-e", "bash", "-lc", &bash_script]),
            ("gnome-terminal", &["--", "bash", "-lc", &bash_script]),
            ("konsole", &["-e", "bash", "-lc", &bash_script]),
        ];
        for (term, args) in &terminals {
            let mut cmd = Command::new(term);
            cmd.args(*args).envs(env);
            match cmd.spawn() {
                Ok(child) => return Some((child, None)),
                Err(e) => {
                    log::warn!(
                        "dev-mode linux terminal '{}' spawn error: {}",
                        term,
                        e
                    );
                    // 尝试下一个
                    continue;
                }
            }
        }
        let msg = "dev-mode linux: no terminal emulator found (x-terminal-emulator/gnome-terminal/konsole), degraded to tokio".to_string();
        log::warn!("{}", msg);
        logger::append_log(app, &msg);
        use_powershell = false;
    }
```

- [ ] **Step 5：编译验证三平台**

Run: `cd d:\java\agentprojects\agentx\src-tauri && cargo check`
Expected: 0 errors。

- [ ] **Step 6：dev_mode=false 路径不受影响**

确认：dev_mode=false 时 `use_powershell=false`，整块 `if use_powershell` 跳过；走原 tokio `Command::new("uv")` / `Command::new("python")` 路径。**改动不影响生产模式启动**。

- [ ] **Step 7：commit**

```bash
cd d:\java\agentprojects/agentx && git add src-tauri/src/backend/handle.rs && git commit -m "feat(backend): dev_mode spawns native console on macos/linux (Terminal.app / x-terminal-emulator)"
```

---

## Task 5：SessionList.tsx 切换 dev_mode 按钮态

**Files:**
- Modify: [frontend/renderer/components/chat/SessionList.tsx:60-76](../../../../frontend/renderer/components/chat/SessionList.tsx#L60-L76)

- [ ] **Step 1：改 handleToggleDevMode**

把整个 `handleToggleDevMode` 函数替换为：

```tsx
  const handleToggleDevMode = async () => {
    if (devModeBusy) return;
    const next = !devMode;
    setDevModeBusy(true);
    setDevModeLocal(next); // 乐观更新，失败时回滚
    try {
      const result = await setDevMode(next);
      // 后端不再 restart：不再依赖 PythonStatus 事件。仍弹一个轻量 toast 提示
      // 「已写入，需重启后端生效」。
      if (result?.message) {
        window.alert(result.message);
      }
    } catch (e) {
      // 回滚
      setDevModeLocal(!next);
      window.alert(
        "切换开发模式失败：" + (e instanceof Error ? e.message : String(e)),
      );
    } finally {
      setDevModeBusy(false);
    }
  };
```

- [ ] **Step 2：改按钮文案**

把 JSX 中
```tsx
{devModeBusy ? "切换中…" : devMode ? "开发模式·开" : "开发模式"}
```
替换为：
```tsx
{devModeBusy ? "切换中…" : devMode ? "开发模式·待重启" : "开发模式"}
```

并把 `title` 替换为：
```tsx
title={
  devMode
    ? "开发模式已写入 store；下次启动应用或 [设置→重启后端] 时按此值启用 console 启动"
    : "开发模式：用终端（Win: PowerShell / Mac: Terminal / Linux: xterm）启动后端并保留窗口"
}
```

- [ ] **Step 3：TypeScript 编译验证**

Run: `cd d:\java\agentprojects\agentx && npm run typecheck`
Expected: 0 errors。

- [ ] **Step 4：commit**

```bash
cd d:\java\agentprojects\agentx && git add frontend/renderer/components/chat/SessionList.tsx && git commit -m "feat(renderer): dev_mode button clarifies 'stored, pending restart' semantics"
```

---

## Task 6：App.tsx mask 30s 兜底

**Files:**
- Modify: [frontend/renderer/App.tsx](../../../../frontend/renderer/App.tsx)

- [ ] **Step 1：新增 useEffect 在 starting 状态下 30s 兜底**

在 `App.tsx` 现有 `useEffect` 群（46-94 行之间）追加：

```tsx
  // 启动中 mask 30s 兜底：超时强制检查后端是否实际就绪，否则把 status 重置为 null 解开 mask。
  useEffect(() => {
    if (pythonStatus !== "starting") return;
    const timer = window.setTimeout(async () => {
      try {
        const resp = await fetch("http://127.0.0.1:8123/");
        if (resp.ok) {
          setPythonStatus("ready");
        } else {
          setPythonStatus(null);
        }
      } catch {
        setPythonStatus(null);
      }
    }, 30_000);
    return () => window.clearTimeout(timer);
  }, [pythonStatus]);
```

> 这是一次性兜底，主要防御 Rust 端事件流断掉的极端情况（如生产模式下 [handle.rs:248] `GivingUp` 未及时推、或文件监听卡住）。

- [ ] **Step 2：编译验证**

Run: `cd d:\java\agentprojects\agentx && npm run typecheck`
Expected: 0 errors。

- [ ] **Step 3：手动冒烟**

启动 dev 模式（`npm run dev`），切 dev_mode，观察不再卡 starting mask；如后端真的起不来，30s 后会自动解开并触发默认 UI（mask=null）。

- [ ] **Step 4：commit**

```bash
cd d:\java\agentprojects\agentx && git add frontend/renderer/App.tsx && git commit -m "fix(renderer): 30s safety net for stuck 'starting' mask"
```

---

## Task 7：AGENTS.md 文档同步

**Files:**
- Modify: [AGENTS.md](../../../../AGENTS.md)
  - §14.2：`PythonHandle::start` 段后追加跨平台 dev_mode spawn 模板
  - §14.7：SOP 段后追加「dev_mode 已成持久化开关，切换不立刻生效」

- [ ] **Step 1：读 §14.2 段尾**

定位 [AGENTS.md §14.2](../../../../AGENTS.md) "Tauri ↔ 后端进程" 段落末尾（"完整的重启 SOP 见 §14.7" 那行附近）。

- [ ] **Step 2：追加跨平台 dev_mode 说明**

在「完整的重启 SOP 见 §14.7」那行**之前**插入：

```markdown
- **dev_mode 持久化且不再"切换即重启"**：切换 dev_mode 开关只写 store，不自动 restart_backend。
  下次应用启动 / 显式 [设置→重启后端] 时按新值 spawn。dev_mode=true 时跨平台 console 拉起：
  | 平台 | 命令 |
  |---|---|
  | Windows | `powershell -NoExit -Command "Set-Location -LiteralPath <cwd>; uv run python -m app.main"` |
  | macOS   | `osascript -e 'tell application "Terminal" to do script "cd <cwd> && uv run python -m app.main; exec /bin/bash"'` |
  | Linux   | `x-terminal-emulator -e bash -lc "cd <cwd> && uv run python -m app.main; exec bash"`（缺失则回退 gnome-terminal / konsole，全缺失降级 tokio） |
```

- [ ] **Step 3：在 §14.7 段加一句**

定位「**入口：永远 `npm run dev`**」段末，在它下面加：

```markdown
- **dev_mode 切后不立刻重启**：在 UI 切换开发模式后，需要重启应用或显式 [设置→重启后端] 才能切换 spawn 方式。
```

- [ ] **Step 4：commit**

```bash
cd d:\java\agentprojects\agentx && git add AGENTS.md && git commit -m "docs(agents): document dev_mode persistence + cross-platform console spawn templates"
```

---

## Self-Review

按 writing-plans checklist 自查：

**Spec 覆盖**：
| Spec §  | Task |
|---|---|
| §3 G1（restart_backend 发 Ready / GivingUp） | T1 + T2 |
| §3 G2（wait_for_ready timeout 给 GivingUp） | T1 |
| §3 G3（set_dev_mode 不 restart） | T3 |
| §3 G4（跨平台 console 拉起） | T4 |
| §3 G5（console 拉起失败降级 tokio） | T4 macOS/Linux 各分支保留 `use_powershell = false` 降级 |
| §3 G6（Ready 契约不变） | T1/T2 不动 mod.rs 的 enum |
| §3 G7（重启 SOP 不破） | T2 / T3 保留 4 步流程结构 |
| §5.1（handle.rs 改动） | T1 + T4 |
| §5.2（commands/app.rs 改动） | T2 + T3 |
| §5.3（SessionList.tsx 改动） | T5 |
| §5.4（App.tsx 改动） | T6 |
| §5.5（AGENTS.md 改动） | T7 |

全部覆盖 ✅。

**占位扫描**：全文无 "TBD" / "TODO" / "implement later" 关键字。每段代码块均给出完整内容。

**类型 / 命名一致性**：
- `new_handle.wait_for_ready` 在 T2 用，§5.1 也用，统一 ✅
- `setPythonStatus` 在 T6 引入；`pythonStatus` state 在 App.tsx 已存在 ✅
- `use_powershell` 变量在 T4 重复用，现有 `spawn_child` 内已命名，不冲突 ✅

**风险**：
- T4 macOS Terminal.app 在 headless 环境失败：已在 spec §7.3 标注 `R1`，T4 保留降级
- T6 30s 兜底在用户**正常启动**路径下也可能因 dev_mode 启动耗时 > 30s 误触发：保留但不让它影响 `python:status=ready` 真值（仅在仍为 `starting` 时强制 reset）

---
