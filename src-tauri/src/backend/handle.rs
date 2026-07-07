//! Python 后端进程 handle
//!
//! 对应 spawn.ts 的 `spawnPython()` + `PythonHandle`：
//! - spawn `uv run python -m app.main`，uv 缺失时回退 `python -m app.main`
//! - 崩溃退避重试：非零退出指数退避（1s/2s/4s）最多 3 次
//! - 启动握手：轮询 `http://127.0.0.1:{port}/`，连续 2 次 200 视为 ready，30s 超时
//! - 进程清理：Windows `taskkill /T /F` 递归杀进程树，Unix 负 PID 杀进程组
//! - 状态推送：`app.emit("python:status", status)` → 前端 `listen("python:status")`

use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use tauri::{AppHandle, Emitter};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;

use super::PythonStatus;
use crate::logger;
use crate::store;

const MAX_RETRIES: u32 = 3;
const HEALTH_INTERVAL_MS: u64 = 200;
const HEALTH_TIMEOUT_MS: u64 = 30_000;
const REQUIRED_CONSECUTIVE_OK: u32 = 2;

/// Python 后端进程 handle，对应 spawn.ts 的 `PythonHandle`。
pub struct PythonHandle {
    inner: Arc<Mutex<PythonInner>>,
    supervisor: Option<tauri::async_runtime::JoinHandle<()>>,
}

struct PythonInner {
    /// 当前子进程 PID，供 `stop()` 杀进程树使用。
    current_pid: Option<u32>,
    /// 是否已主动停止（停止后不再重试）。
    stopped: bool,
    /// 当前使用的命令（uv / python），uv 失败后切换到 python。
    current_command: CommandKind,
    /// dev 模式下由 powershell.exe 启动，需要把 powershell PID 也记下来，
    /// 关闭时同时杀掉 powershell 进程树（避免 8123 端口残留）。
    dev_mode_powershell_pid: Option<u32>,
}

#[derive(Clone, Copy)]
enum CommandKind {
    Uv,
    Python,
}

impl PythonHandle {
    /// 启动 Python 后端 supervisor 任务。
    ///
    /// 立即返回 handle，spawn + 重试 + 握手在后台 tokio task 中进行。
    /// 状态变化通过 `app.emit("python:status", ...)` 推送。
    pub fn start(app: AppHandle, cwd: PathBuf, _port: u16, env: HashMap<String, String>) -> Self {
        let dev_mode = store::get_dev_mode(&app);
        let inner = Arc::new(Mutex::new(PythonInner {
            current_pid: None,
            stopped: false,
            current_command: CommandKind::Uv,
            dev_mode_powershell_pid: None,
        }));

        let inner_clone = inner.clone();
        let supervisor = tauri::async_runtime::spawn(supervise(
            inner_clone,
            app,
            cwd,
            env,
            dev_mode,
        ));

        Self {
            inner,
            supervisor: Some(supervisor),
        }
    }

    /// 轮询 `http://127.0.0.1:{port}/`，连续 2 次 200 视为 ready，30s 超时返回 false。
    pub async fn wait_for_ready(&self, app: &AppHandle, port: u16) -> bool {
        let deadline = tokio::time::Instant::now() + Duration::from_millis(HEALTH_TIMEOUT_MS);
        let mut consecutive = 0u32;
        let url = format!("http://127.0.0.1:{}/", port);

        loop {
            {
                let guard = self.inner.lock().await;
                if guard.stopped {
                    return false;
                }
            }

            if tokio::time::Instant::now() > deadline {
                log::warn!(
                    "python wait_for_ready timed out after {}ms",
                    HEALTH_TIMEOUT_MS
                );
                let _ = app.emit("python:status", PythonStatus::GivingUp);
                return false;
            }

            match reqwest::get(&url).await {
                Ok(resp) if resp.status().is_success() => {
                    consecutive += 1;
                    if consecutive >= REQUIRED_CONSECUTIVE_OK {
                        let _ = app.emit("python:status", PythonStatus::Ready);
                        return true;
                    }
                }
                _ => consecutive = 0,
            }

            tokio::time::sleep(Duration::from_millis(HEALTH_INTERVAL_MS)).await;
        }
    }

    /// 主动停止 Python 后端：设置 stopped 标志 + killTree 杀进程树。
    pub async fn stop(&self) {
        {
            let mut guard = self.inner.lock().await;
            guard.stopped = true;
        }
        let (pid, ps_pid) = {
            let guard = self.inner.lock().await;
            (guard.current_pid, guard.dev_mode_powershell_pid)
        };
        if let Some(pid) = pid {
            kill_tree(pid);
        }
        // dev 模式：杀掉 powershell 宿主进程树，避免 8123 端口残留。
        if let Some(ps_pid) = ps_pid {
            kill_tree(ps_pid);
        }
    }
}

impl Drop for PythonHandle {
    fn drop(&mut self) {
        // best-effort：drop 时若 supervisor 仍在运行则 abort
        if let Some(handle) = self.supervisor.take() {
            handle.abort();
        }
    }
}

/// supervisor 循环：spawn → 等待退出 → 退避重试。
///
/// dev_mode=true 走的是 **fire-and-forget** 路径：spawn PowerShell / Terminal.app / xterm
/// 一次后立即返回，**绝不调用 `child.wait()`**。原因：dev_mode 下的宿主进程
/// （PowerShell `-NoExit` / macOS Terminal / Linux xterm）会驻留到用户手动关闭，
/// `wait()` 会**永远阻塞**，无法触发任何状态事件，违背"切换开发模式立即生效"的契约。
/// readiness 由独立的 `wait_for_ready()` 检测端口来驱动，supervisor 不参与。
async fn supervise(
    inner: Arc<Mutex<PythonInner>>,
    app: AppHandle,
    cwd: PathBuf,
    env: HashMap<String, String>,
    dev_mode: bool,
) {
    // 辅助：终端 + 落盘双写，确保前端日志面板能读到 supervisor 关键事件。
    let log_info = |msg: String| {
        log::info!("{}", msg);
        logger::append_log(&app, &msg);
    };
    let log_warn = |msg: String| {
        log::warn!("{}", msg);
        logger::append_log(&app, &msg);
    };

    // 端口占用检测：若 8123 被旧 Python 进程占着（孤儿/上轮未清理），
    // 先 kill 整棵进程树，避免 spawn 后 bind 失败。
    ensure_port_free_or_kill();

    let _ = app.emit("python:status", PythonStatus::Starting);
    log_info(format!("python launching (dev_mode={})", dev_mode));

    let spawn_result = spawn_child(&inner, &cwd, &env, &app, dev_mode).await;

    match spawn_result {
        Some((child, ps_pid)) => {
            // 记录 PID 供 stop() 使用：dev_mode 下 powershell 是独立进程，
            // 但 stop() 时仍要杀 powershell 进程树（防止用户切回 false 后残留）。
            let child_pid_opt = child.id();
            {
                let mut guard = inner.lock().await;
                if let Some(pid) = child_pid_opt {
                    guard.current_pid = Some(pid);
                }
                guard.dev_mode_powershell_pid = ps_pid;
            }

            if dev_mode {
                // ============ dev_mode：fire-and-forget ============
                // 关键：释放 child，让 powershell 进程独立存在。
                // 这避免了 `child.wait().await` 因 `-NoExit` 永远阻塞的问题。
                // 端口 ready 状态由调用方 `wait_for_ready()` 单独检测。
                // 我们不调用 `drop` 或 `wait`：child handle drop 不会杀 powershell
                // （它是 powershell 启动的孙进程，spurious kill 也是发生在孙进程上）。
                drop(child);
                log_info(
                    "dev_mode python spawned via console; supervisor released (wait_for_ready owns ready detection)".into(),
                );
                // 监督任务直接退出——把控制权交给 wait_for_ready。
                // 如果用户后续显式 stop()，我们的 stop() 实现会:
                // 1) 通过 current_pid + dev_mode_powershell_pid 杀整棵树;
                // 2) 设置 stopped=true，让正在跑的 wait_for_ready 检测到并退出。
                return;
            }

            // ============ 生产模式：标准 supervise 流程 ============
            let mut child = child;
            // pipe stdout/stderr 到日志（同时落盘到 {app_data_dir}/logs/agentx-YYYYMMDD.log，
            // 供前端日志面板读取）
            let stdout_app = app.clone();
            if let Some(stdout) = child.stdout.take() {
                tauri::async_runtime::spawn(pipe_to_log(stdout, "python", stdout_app));
            }
            let stderr_app = app.clone();
            if let Some(stderr) = child.stderr.take() {
                tauri::async_runtime::spawn(pipe_to_log(stderr, "python:err", stderr_app));
            }

            // 标准 supervise 循环：spawn → wait → 退避重试。
            let mut attempt: u32 = 0;
            loop {
                let exit_result = child.wait().await;
                {
                    let mut guard = inner.lock().await;
                    guard.current_pid = None;
                    guard.dev_mode_powershell_pid = None;
                    if guard.stopped {
                        return;
                    }
                }
                match exit_result {
                    Ok(status) if status.success() || status.code() == Some(0) => {
                        log_info("python process exited cleanly (code=0)".into());
                        return;
                    }
                    Ok(status) => {
                        log_warn(format!("python process exited with code={:?}", status.code()));
                    }
                    Err(e) => {
                        log_warn(format!("python wait error: {}", e));
                    }
                }

                attempt += 1;
                if attempt > MAX_RETRIES {
                    log_warn(format!("python giving up after {} retries", MAX_RETRIES));
                    let _ = app.emit("python:status", PythonStatus::GivingUp);
                    return;
                }

                let _ = app.emit("python:status", PythonStatus::Crashed);
                let delay = Duration::from_secs(1 << (attempt - 1));
                log_info(format!("python retrying in {:?} (attempt {})", delay, attempt + 1));
                tokio::time::sleep(delay).await;

                // 重试前再次确保端口空闲
                ensure_port_free_or_kill();
                let _ = app.emit("python:status", PythonStatus::Starting);
                log_info(format!(
                    "python launching (attempt {}/{})",
                    attempt + 1,
                    MAX_RETRIES + 1
                ));

                // 重试：必须直接 spawn tokio 路径（重试仍按 dev_mode 决策；
                // 但 dev_mode 分支已经 fire-and-forget 不会再到这，下面只走 tokio 命令）。
                let command_kind = {
                    let guard = inner.lock().await;
                    guard.current_command
                };
                let (program, args) = match command_kind {
                    CommandKind::Uv => ("uv", vec!["run", "python", "-m", "app.main"]),
                    CommandKind::Python => ("python", vec!["-m", "app.main"]),
                };
                let mut cmd = Command::new(program);
                cmd.args(&args)
                    .current_dir(&cwd)
                    .envs(&env)
                    .stdin(Stdio::null())
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped());
                #[cfg(unix)]
                {
                    use std::os::unix::process::CommandExt;
                    cmd.process_group(0);
                }
                child = match cmd.spawn() {
                    Ok(mut c) => {
                        if let Some(pid) = c.id() {
                            let mut guard = inner.lock().await;
                            guard.current_pid = Some(pid);
                        }
                        let sa = app.clone();
                        if let Some(stdout) = c.stdout.take() {
                            tauri::async_runtime::spawn(pipe_to_log(stdout, "python", sa));
                        }
                        let sa2 = app.clone();
                        if let Some(stderr) = c.stderr.take() {
                            tauri::async_runtime::spawn(pipe_to_log(stderr, "python:err", sa2));
                        }
                        c
                    }
                    Err(e) => {
                        let msg = format!("retry spawn error for {}: {}", program, e);
                        log_warn(msg);
                        if matches!(command_kind, CommandKind::Uv) {
                            let switch_msg = "retry: switching to python -m app.main";
                            log_info(switch_msg.into());
                            let mut guard = inner.lock().await;
                            guard.current_command = CommandKind::Python;
                        }
                        // 直接重试外层循环（sleep + re-spawn）
                        continue;
                    }
                };
            }
        }
        None => {
            // spawn 失败（uv 不存在），已切换到 python。
            // dev_mode 下不会到这里（dev_mode 走 console 路径，失败已降级 tokio）。
            log_warn("python spawn failed, uv may be missing".into());
            let _ = app.emit("python:status", PythonStatus::GivingUp);
        }
    }
}

/// spawn 子进程，uv 失败时回退到 python。
///
/// 返回 `Some((child, ps_pid))` 表示 spawn 成功，`None` 表示 spawn 失败（uv 不存在且 python 也失败）。
/// `ps_pid` 仅 dev 模式（PowerShell 宿主）下非空，供 `stop()` 杀整棵树避免端口残留。
/// uv spawn 失败时会自动切换到 python 命令并记录到 inner.current_command。
async fn spawn_child(
    inner: &Arc<Mutex<PythonInner>>,
    cwd: &PathBuf,
    env: &HashMap<String, String>,
    app: &AppHandle,
    dev_mode: bool,
) -> Option<(Child, Option<u32>)> {
    let command_kind = {
        let guard = inner.lock().await;
        guard.current_command
    };

    // dev 模式（仅 Windows）：用 powershell 拉起 uv，保持窗口不退出。
    // 命令形态：`powershell -NoExit -Command "cd <cwd>; uv run python -m app.main"`
    // 关键点：
    // - `-NoExit` 让 powershell 进程不被 uv 退出连带销毁，开发关掉窗口才会结束；
    // - 双引号转义：`"` 变成 `\"`，`$` 在 powershell 里有特殊含义用 `` ` `` 转义；
    // - 我们这里只在命令行里传 cwd + uv 命令，没有用户注入，安全。
    // 注意：Windows 用 PowerShell `-NoExit`；macOS 用 Terminal.app + AppleScript
    // `do script`；Linux 优先 `x-terminal-emulator`，回退 `gnome-terminal` /
    // `konsole`，全缺失降级 tokio。其它平台（非 Win/macOS/Linux）一律走 tokio。
    let mut use_powershell = dev_mode;
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    let mut use_powershell = false;

    if use_powershell {
        // ============ macOS：Terminal.app + AppleScript ============
        #[cfg(target_os = "macos")]
        {
            let cwd_str = cwd.to_string_lossy().to_string();
            let cwd_escaped = cwd_str.replace('\'', "'\\''");
            let ps_script = format!(
                "cd '{}' && uv run python -m app.main; exec /bin/bash",
                cwd_escaped
            );
            // 用 osascript 让 Terminal.app 新开窗口执行脚本
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

        // ============ Linux：x-terminal-emulator / gnome-terminal / konsole ============
        #[cfg(target_os = "linux")]
        {
            let cwd_str = cwd.to_string_lossy().to_string();
            let cwd_escaped = cwd_str.replace('\'', "'\\''");
            let bash_script = format!(
                "cd '{}' && uv run python -m app.main; exec bash",
                cwd_escaped
            );
            let terminals: [(&str, Vec<&str>); 3] = [
                ("x-terminal-emulator", vec!["-e", "bash", "-lc", &bash_script]),
                ("gnome-terminal", vec!["--", "bash", "-lc", &bash_script]),
                ("konsole", vec!["-e", "bash", "-lc", &bash_script]),
            ];
            let mut spawned = false;
            for (term, args) in &terminals {
                let mut cmd = Command::new(term);
                cmd.args(args).envs(env);
                match cmd.spawn() {
                    Ok(child) => {
                        spawned = true;
                        return Some((child, None));
                    }
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
            if !spawned {
                let msg = "dev-mode linux: no terminal emulator found (x-terminal-emulator/gnome-terminal/konsole), degraded to tokio".to_string();
                log::warn!("{}", msg);
                logger::append_log(app, &msg);
                use_powershell = false;
            }
        }

        // ============ Windows：PowerShell -NoExit ============
        #[cfg(windows)]
        {
            let cwd_str = cwd.to_string_lossy().to_string();
            // 把 cwd 路径里的双引号转义，避免 powershell 解析错位
            let cwd_escaped = cwd_str.replace('"', "`\"");
            let ps_script = format!(
                "Set-Location -LiteralPath \"{}\"; uv run python -m app.main",
                cwd_escaped
            );
            let mut cmd = Command::new("powershell.exe");
            cmd.args(["-NoExit", "-Command", &ps_script])
                .envs(env);
            // 关键：Tauri 2.x GUI 应用的子系统是 `windows`（无控制台），
            // 默认 spawn 出的 powershell 会**继承父进程的 0 控制台**而不显示窗口。
            // 用 CREATE_NEW_CONSOLE 强制给 powershell 开一个新 console 窗口。
            // 用 DETACHED_PROCESS 让 powershell 进程脱离 AgentX 进程组，
            // 即 AgentX 退出 / stop() 后不再通过 console 句柄串扰 powershell。
            {
                use windows::Win32::System::Threading::{
                    CREATE_NEW_CONSOLE, DETACHED_PROCESS,
                };
                cmd.creation_flags(CREATE_NEW_CONSOLE.0 | DETACHED_PROCESS.0);
            }
            // dev 模式：让 PowerShell 窗口直接显示 stdout/stderr（开发者要的就是看实时日志），
            // Rust 这边不接管 pipe。supervisor 已改造为 **fire-and-forget**，
            // 不再 wait()（因为 `-NoExit` 让 powershell 永驻，wait 会永远阻塞）。
            // 不设置 current_dir：powershell 会用 Set-Location 切到 backend 目录
            match cmd.spawn() {
                Ok(child) => {
                    let ps_pid = child.id();
                    let msg = format!(
                        "dev-mode powershell spawned: pid={:?}, cwd={}",
                        ps_pid, cwd_str
                    );
                    log::info!("{}", msg);
                    logger::append_log(app, &msg);
                    return Some((child, ps_pid));
                }
                Err(e) => {
                    let msg = format!("dev-mode powershell spawn error: {}", e);
                    log::warn!("{}", msg);
                    logger::append_log(app, &msg);
                    // powershell 拉起失败，降级走原 tokio 启动
                    #[allow(unused_assignments)]
                    {
                        use_powershell = false;
                    }
                }
            }
        }
    }

    let (program, args) = match command_kind {
        CommandKind::Uv => ("uv", vec!["run", "python", "-m", "app.main"]),
        CommandKind::Python => ("python", vec!["-m", "app.main"]),
    };

    let mut cmd = Command::new(program);
    cmd.args(&args)
        .current_dir(cwd)
        .envs(env)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    // Unix 下创建独立进程组，便于 stop() 用负 PID 杀整组
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }

    match cmd.spawn() {
        Ok(child) => Some((child, None)),
        Err(e) => {
            let msg = format!("python spawn error for {}: {}", program, e);
            log::warn!("{}", msg);
            logger::append_log(app, &msg);
            // uv 失败 → 切换到 python，后续重试沿用 python
            if matches!(command_kind, CommandKind::Uv) {
                let switch_msg = "python switching to python -m app.main due to uv spawn failure";
                log::info!("{}", switch_msg);
                logger::append_log(app, switch_msg);
                let mut guard = inner.lock().await;
                guard.current_command = CommandKind::Python;
            }
            None
        }
    }
}

/// 检查端口是否被占用；若被占用则 kill 占用进程（孤儿防御）。
///
/// 解决场景：上轮 GUI 异常退出时 Tauri 主进程已死，但 uv/python 子进程仍在，
/// 导致下轮 spawn 后 FastAPI bind 8123 失败（WinError 10048）。
///
/// 实现：
/// - 跨平台统一通过解析 `netstat -ano` 输出找到占用端口的 PID（Windows）或
///   使用 `lsof -ti`（Unix）。
/// - 对找到的 PID 执行 `taskkill /T /F` / `kill -TERM`。
fn ensure_port_free_or_kill() {
    const PORT: u16 = 8123;
    #[cfg(windows)]
    {
        let Ok(out) = std::process::Command::new("netstat")
            .args(["-ano", "-p", "TCP"])
            .output()
        else {
            return;
        };
        let stdout = String::from_utf8_lossy(&out.stdout);
        let needle = format!("127.0.0.1:{PORT}");
        let pids: Vec<u32> = stdout
            .lines()
            .filter(|l| l.contains(&needle) && l.contains("LISTENING"))
            .filter_map(|l| l.split_whitespace().last())
            .filter_map(|s| s.parse::<u32>().ok())
            .collect();
        for pid in pids {
            log::warn!("port {} 被 PID {} 占用，先行 kill", PORT, pid);
            let _ = std::process::Command::new("taskkill")
                .args(["/T", "/F", "/PID", &pid.to_string()])
                .output();
        }
    }
    #[cfg(unix)]
    {
        let Ok(out) = std::process::Command::new("lsof")
            .args(["-ti", &format!("tcp:{PORT}")])
            .output()
        else {
            return;
        };
        for pid in String::from_utf8_lossy(&out.stdout)
            .lines()
            .filter_map(|s| s.trim().parse::<i32>().ok())
        {
            log::warn!("port {} 被 PID {} 占用，先行 kill", PORT, pid);
            unsafe {
                libc::kill(pid, libc::SIGTERM);
            }
        }
    }
}

/// 杀掉指定进程及其全部子进程。
///
/// Windows 下 `child.kill()` 只杀直接子进程（uv），孙进程（python）会存活，
/// 导致 python 继续占用端口。用 `taskkill /T /F` 递归杀整棵进程树。
/// Unix 下用负 PID 杀整个进程组。
fn kill_tree(pid: u32) {
    #[cfg(windows)]
    {
        let pid_str = pid.to_string();
        let result = std::process::Command::new("taskkill")
            .args(["/pid", &pid_str, "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .output();
        if let Err(e) = result {
            log::warn!("kill_tree taskkill failed for pid={}: {}", pid, e);
        }
    }
    #[cfg(unix)]
    {
        // 负 PID 表示杀整个进程组
        let result = unsafe { libc::kill(-(pid as i32), libc::SIGTERM) };
        if result != 0 {
            log::warn!("kill_tree kill(-{}) failed", pid);
        }
    }
    #[cfg(not(any(windows, unix)))]
    {
        log::warn!("kill_tree not implemented for this platform (pid={})", pid);
        let _ = pid;
    }
}

/// 将子进程 stdout/stderr 管道内容写入 log。
///
/// 同时写入 `logger::append_log` 落盘的日志文件（`{app_data_dir}/logs/agentx-YYYYMMDD.log`），
/// 确保前端日志面板能读到 Python 后端输出。
async fn pipe_to_log<R: tokio::io::AsyncRead + Unpin>(
    reader: R,
    prefix: &'static str,
    app: AppHandle,
) {
    use tokio::io::{AsyncBufReadExt, BufReader};
    let mut reader = BufReader::new(reader);
    let mut line = String::new();
    loop {
        line.clear();
        match reader.read_line(&mut line).await {
            Ok(0) => break, // EOF
            Ok(_) => {
                let trimmed = line.trim_end();
                if !trimmed.is_empty() {
                    let entry = format!("[{}] {}", prefix, trimmed);
                    log::info!("{}", entry);
                    logger::append_log(&app, &entry);
                }
            }
            Err(e) => {
                let entry = format!("[{}] read error: {}", prefix, e);
                log::warn!("{}", entry);
                logger::append_log(&app, &entry);
                break;
            }
        }
    }
}
