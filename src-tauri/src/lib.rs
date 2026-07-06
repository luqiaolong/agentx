//! AgentX Tauri 主进程入口
//!
//! 注册 10 个官方插件 + 日志，在 `setup()` 中启动 Python 后端。

pub mod backend;
pub mod store;

use std::path::PathBuf;

use tauri::Manager;

const PYTHON_PORT: u16 = 8123;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_log::Builder::new().build())
        .plugin(tauri_plugin_os::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(
            // stronghold 仅注册，不用于凭证存储（v2.3.1 无公开 Rust runtime API）
            tauri_plugin_stronghold::Builder::new(|_salt| {
                b"agentx-desktop-vault-key-phase1-placeholder".to_vec()
            })
            .build(),
        )
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            log::info!(
                "AgentX Tauri shell started (version: {})",
                app.package_info().version
            );

            // 迁移旧版 LLM 配置（若需要）
            store::migrate_legacy_llm_config(app.handle());

            // 启动 Python 后端
            let handle = app.handle().clone();
            let cwd = resolve_backend_cwd(app.handle());
            tokio::spawn(async move {
                let env = backend::env::build_env(&handle, PYTHON_PORT);
                let py = backend::handle::PythonHandle::start(
                    handle.clone(),
                    cwd,
                    PYTHON_PORT,
                    env,
                );
                // 启动握手：轮询健康端点
                let ok = py.wait_for_ready(&handle, PYTHON_PORT).await;
                if !ok {
                    log::warn!("python waitForReady returned false (timeout or stopped)");
                }
                // 将 handle 存入 app state，后续 stop/restart 时使用
                handle.manage(py);
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running AgentX tauri application");
}

/// 解析 Python 后端工作目录。
///
/// 开发模式：`{项目根}/backend`（与 Electron 的 `app.getAppPath()/backend` 一致）。
/// 生产模式：`{resource_dir}/backend`（打包后 backend 随资源一起分发）。
fn resolve_backend_cwd(app: &tauri::AppHandle) -> PathBuf {
    // 优先尝试 resource_dir（生产模式）
    if let Ok(resource_dir) = app.path().resource_dir() {
        let backend = resource_dir.join("backend");
        if backend.exists() {
            return backend;
        }
    }
    // 开发模式回退：当前工作目录下的 backend/
    PathBuf::from("backend")
}
