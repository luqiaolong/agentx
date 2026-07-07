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

use tauri::{AppHandle, Emitter, Manager};
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
        let pid = {
            let guard = self.inner.lock().await;
            guard.current_pid
        };
        if let Some(pid) = pid {
            kill_tree(pid);
        }
        // dev_mode 下日志窗口由用户手动关闭，不需要在这里处理
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
/// dev_mode=true 时：
/// - 打开一个独立的日志窗口（WebviewWindow），实时展示 Python 后端 stdout/stderr
/// - 仍然走标准 tokio spawn（pipe stdout/stderr），但 supervisor 不 wait()，
///   而是 fire-and-forget：启动 pipe 任务读取日志并 emit 到日志窗口后，
///   supervisor 直接退出。readiness 由 `wait_for_ready()` 单独检测端口。
/// - 关闭日志窗口不会杀 Python 进程；Python 进程由 stop() 通过 PID 管理。
///
/// dev_mode=false 时：标准 supervise 流程（spawn → pipe → wait → 退避重试）。
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
        Some(child) => {
            let child_pid_opt = child.id();
            {
                let mut guard = inner.lock().await;
                if let Some(pid) = child_pid_opt {
                    guard.current_pid = Some(pid);
                }
            }

            // 无论 dev_mode 都启动 pipe 任务读取 stdout/stderr
            let mut child = child;
            let stdout_app = app.clone();
            if let Some(stdout) = child.stdout.take() {
                tauri::async_runtime::spawn(pipe_to_log(stdout, "python", stdout_app));
            }
            let stderr_app = app.clone();
            if let Some(stderr) = child.stderr.take() {
                tauri::async_runtime::spawn(pipe_to_log(stderr, "python:err", stderr_app));
            }

            if dev_mode {
                // ============ dev_mode：打开日志窗口 + fire-and-forget ============
                open_log_window(&app);
                log_info(
                    "dev_mode: log window opened, supervisor released (wait_for_ready owns ready detection)".into(),
                );
                // 监督任务直接退出——pipe 任务在后台继续读取日志并 emit 到日志窗口。
                // 端口 ready 状态由调用方 `wait_for_ready()` 单独检测。
                return;
            }

            // ============ 生产模式：标准 supervise 流程 ============
            // 标准 supervise 循环：spawn → wait → 退避重试。
            let mut attempt: u32 = 0;
            loop {
                let exit_result = child.wait().await;
                {
                    let mut guard = inner.lock().await;
                    guard.current_pid = None;
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

                // 重试
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
            log_warn("python spawn failed, uv may be missing".into());
            let _ = app.emit("python:status", PythonStatus::GivingUp);
        }
    }
}

/// spawn 子进程，uv 失败时回退到 python。
///
/// 返回 `Some(child)` 表示 spawn 成功，`None` 表示 spawn 失败（uv 不存在且 python 也失败）。
/// uv spawn 失败时会自动切换到 python 命令并记录到 inner.current_command。
async fn spawn_child(
    inner: &Arc<Mutex<PythonInner>>,
    cwd: &PathBuf,
    env: &HashMap<String, String>,
    app: &AppHandle,
    _dev_mode: bool,
) -> Option<Child> {
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
        Ok(child) => Some(child),
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

/// 打开开发模式日志窗口（WebviewWindow）。
///
/// 日志窗口加载 `log-window.html`，实时展示 Python 后端 stdout/stderr。
/// 窗口特性：无边框（自定义标题栏）、可调整大小、暗色主题。
fn open_log_window(app: &AppHandle) {
    // 若已存在则直接显示（避免 close→rebuild 的 label 冲突）
    if let Some(existing) = app.get_webview_window("log") {
        let _ = existing.show();
        let _ = existing.set_focus();
        return;
    }

    let window_result = tauri::WebviewWindowBuilder::new(
        app,
        "log",
        tauri::WebviewUrl::App("log-window.html".into()),
    )
    .title("AgentX Logs")
    .inner_size(1200.0, 700.0)
    .min_inner_size(600.0, 300.0)
    .decorations(false)
    .shadow(false)
    .transparent(false)
    .always_on_top(false)
    .resizable(true)
    .visible(false)
    .build();

    match window_result {
        Ok(window) => {
            let _ = window.show();
            let _ = window.set_focus();
        }
        Err(e) => {
            log::warn!("failed to open log window: {}", e);
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

/// 将子进程 stdout/stderr 管道内容写入 log，同时 emit 到日志窗口。
///
/// 同时写入 `logger::append_log` 落盘的日志文件（`{app_data_dir}/logs/agentx-YYYYMMDD.log`），
/// 并通过 `app.emit("log:append", trimmed)` 推送给日志窗口（WebviewWindow label="log"）。
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
                    // 同时 emit 到日志窗口（开发模式下日志窗口会监听此事件）
                    // 使用全局 emit_str，避免 webview-specific 事件的时序问题
                    if let Err(e) = app.emit_str("log:append", trimmed.to_string()) {
                        log::warn!("failed to emit log:append to log window: {}", e);
                    }
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
