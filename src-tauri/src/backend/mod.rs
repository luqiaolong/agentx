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

use serde::{Deserialize, Serialize};

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
