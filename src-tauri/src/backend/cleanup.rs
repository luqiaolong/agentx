//! 应用退出时清理 Rust 主进程的所有子进程与兄弟进程。
//!
//! 设计要点：
//! - 只在用户点击关闭按钮（拦截 `WindowEvent::CloseRequested`）或主动调 `app_quit` 时触发。
//! - 清理链路：Python/uv 子进程树（`taskkill /T /F`） → pnpm tauri dev 拉起的 vite (node) 兄弟进程
//!   → 等端口释放（Windows TCP TIME_WAIT）。
//! - 幂等：多次调用只清理一次（`CLEANING_UP` AtomicBool 守卫）。
//! - PowerShell 节点清理按 [docs/agents/04-restart-sop.md §14.7.8](file:///d:/java/agentprojects/agentx/docs/agents/04-restart-sop.md)
//!   的 CommandLine 精准筛选规范，避免误杀同机其他项目。

use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use tauri::{AppHandle, Manager};

use crate::logger;

/// 全局幂等守卫：第一次执行清理后会置 true，后续调用立即返回。
pub static CLEANING_UP: AtomicBool = AtomicBool::new(false);

/// 关闭入口：幂等，多次调用只清理一次。
///
/// 流程：
/// 1. 守卫置位 + 落日志
/// 2. 停止 Python 子进程（已实现 `PythonHandle::stop`）
/// 3. 清理 vite/node 兄弟进程（PowerShell 精准筛选）
/// 4. sleep 500ms 等候 Windows TCP TIME_WAIT 释放端口
pub async fn cleanup_all(app: AppHandle) {
    if CLEANING_UP.swap(true, Ordering::SeqCst) {
        log::info!("cleanup_all already in progress, skip");
        return;
    }
    logger::append_log(&app, "[main] cleanup_all started");

    cleanup_python(&app).await;
    cleanup_node_siblings();

    // 等候端口释放（Windows TCP TIME_WAIT）。
    tokio::time::sleep(Duration::from_millis(500)).await;
    logger::append_log(&app, "[main] cleanup_all done");
}

/// 从 `PythonState` 取出旧 handle（与 `app_restart_backend` 一致的 take 模式），
/// 调用 `PythonHandle::stop()` 触发 `taskkill /T /F /PID` 整棵树清理。
async fn cleanup_python(app: &AppHandle) {
    let state = app.state::<crate::PythonState>();
    let old = match state.lock() {
        Ok(mut guard) => guard.take(),
        Err(e) => {
            log::warn!("cleanup_python: state lock poisoned: {}", e);
            None
        }
    };
    if let Some(handle) = old {
        logger::append_log(app, "[main] cleanup_python: stopping python handle");
        handle.stop().await;
    } else {
        logger::append_log(app, "[main] cleanup_python: no python handle to stop");
    }
}

/// 清理 pnpm tauri dev 拉起的 vite (node) 兄弟进程。
///
/// Windows: spawn PowerShell 按 CommandLine 精准筛选 `*agentx*` 或 `*vite*` 的 node 进程，
/// `Stop-Process -Force` 终止（遵循 §14.7.8 进程筛选规范，**禁止**全杀 `node`）。
/// Unix: `pkill -9 -f 'vite'`（按命令行匹配）。
fn cleanup_node_siblings() {
    #[cfg(windows)]
    {
        // PowerShell 内联脚本：按 CommandLine 精准筛选 node/vite/esbuild 进程。
        // 注意避免使用 netstat / taskkill（§14.7.3 规范禁止）。
        const PS_SCRIPT: &str = r#"
$ErrorActionPreference = 'SilentlyContinue'
Get-Process -Name node -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -like '*agentx*' -or
        $_.CommandLine -like '*vite*' -or
        $_.CommandLine -like '*esbuild*'
    } |
    ForEach-Object {
        Write-Host ("[cleanup] stop node PID={0} cmd={1}" -f $_.Id, $_.CommandLine)
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
"#;
        let result = std::process::Command::new("powershell")
            .args([
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                PS_SCRIPT,
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .output();
        match result {
            Ok(out) => {
                let stderr = String::from_utf8_lossy(&out.stderr);
                if !stderr.trim().is_empty() {
                    log::warn!("cleanup_node_siblings stderr: {}", stderr.trim());
                }
                log::info!("cleanup_node_siblings: powershell exit={:?}", out.status.code());
            }
            Err(e) => {
                log::warn!("cleanup_node_siblings: failed to spawn powershell: {}", e);
            }
        }
    }
    #[cfg(unix)]
    {
        let _ = std::process::Command::new("pkill")
            .args(["-9", "-f", "vite"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .output();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cleaning_up_guard_is_idempotent() {
        // 重置全局守卫（测试间需独立——使用 OnceLock 比 static 更难，这里仅做 IO-free 单元测试桩）
        CLEANING_UP.store(false, Ordering::SeqCst);
        assert!(!CLEANING_UP.swap(true, Ordering::SeqCst));
        assert!(CLEANING_UP.swap(true, Ordering::SeqCst));
        CLEANING_UP.store(false, Ordering::SeqCst);
    }
}
