//! Python 后端进程管理
//!
//! 对应 `frontend/main/python/spawn.ts`，负责：
//! - 拼装 Python 子进程环境变量（`build_env`）
//! - spawn `uv run python -m app.main`（uv 缺失时回退 `python -m app.main`）
//! - 启动握手：轮询 `http://127.0.0.1:{port}/`，连续 2 次 200 视为 ready
//! - 崩溃退避重试：非零退出指数退避（1s/2s/4s）最多 3 次
//! - 进程清理：Windows `taskkill /T /F`，Unix 负 PID 杀进程组

pub mod env;
pub mod handle;

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

/// Python 后端进程状态，对应 spawn.ts 的 `PythonStatus`。
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PythonStatus {
    /// 正在启动（spawn 已调用，尚未握手成功）
    Starting,
    /// 健康检查连续 2 次 200，后端就绪
    Ready,
    /// 进程非零退出，正在退避重试
    Crashed,
    /// 重试次数耗尽，放弃
    GivingUp,
}

/// 解析 Python 后端工作目录。
///
/// 生产模式优先使用 `{resource_dir}/backend`（打包后 backend 随资源一起分发）。
/// 开发模式下 `CARGO_MANIFEST_DIR` 指向 `src-tauri`，项目根目录为其父目录，
/// 因此回退到 `{项目根}/backend`，避免在 `src-tauri` 子目录中找不到 `backend/`。
pub fn resolve_backend_cwd(app: &AppHandle) -> PathBuf {
    if let Ok(resource_dir) = app.path().resource_dir() {
        let backend = resource_dir.join("backend");
        if backend.exists() {
            return backend;
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("CARGO_MANIFEST_DIR should have a parent directory")
        .join("backend")
}
