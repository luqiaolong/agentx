//! AgentX Tauri 主进程入口
//!
//! 注册 10 个官方插件 + 日志，在 `setup()` 中启动 Python 后端，
//! 注册全部 46 个 Tauri 命令（settings/system/app）。

pub mod backend;
pub mod commands;
pub mod git;
pub mod logger;
pub mod migration;
pub mod store;

use std::sync::Mutex;

use tauri::{image::Image, Emitter, Manager};

const PYTHON_PORT: u16 = 8123;

/// 应用图标 PNG 字节（嵌入编译时，运行时直接读取）。
///
/// `tauri.conf.json::bundle.icon` 只控制 **打包后** 的 EXE 安装程序图标，
/// 运行时窗口图标需要在 `WebviewWindowBuilder::icon()` 显式设置（尤其 dev 模式下
/// target/debug/agentx.exe 不会嵌入图标资源）。这里用 `include_bytes!` 把
/// `icon.ico` 内嵌到二进制，确保 `WindowBuilder::icon` 有可用的 Image 数据。
const APP_ICON_BYTES: &[u8] = include_bytes!("../icons/icon.ico");

/// 从嵌入的 ICO 数据构造 `tauri::image::Image`。
///
/// `Image::from_bytes` 会自动解析 ICO 文件并选取最佳尺寸帧；多尺寸 ICO 会被
/// 全部加载到 `Image::rgba()` 中，运行时由 `WebviewWindowBuilder::icon` 自动
/// 按窗口 DPI 选择合适帧。
fn build_app_icon() -> Image<'static> {
    // ICO 文件可能包含多张帧；从 256x256 主帧构建 Image。
    // `Image::from_bytes` 在 Windows 上能直接解码 ICO 多帧。
    Image::from_bytes(APP_ICON_BYTES).unwrap_or_else(|e| {
        log::warn!("failed to decode embedded ICO icon: {e}; falling back to 1x1 transparent");
        // 1x1 transparent RGBA fallback（永远不会触发，因为 ICO 验证通过）
        Image::new_owned(vec![0u8; 4], 1, 1)
    })
}

/// Python 后端 handle 的全局状态包装。
///
/// 用 `Mutex<Option<PythonHandle>>` 而非直接 `manage(PythonHandle)`，
/// 因为 `app_restart_backend` 命令需要取出旧 handle 停止后替换为新 handle。
/// `Mutex` 不跨 await 点持有，`stop().await` 在锁外执行。
pub type PythonState = Mutex<Option<backend::handle::PythonHandle>>;

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
        .invoke_handler(tauri::generate_handler![
            // === Settings 命令（30 个）===
            commands::settings::settings_get_milvus_credentials,
            commands::settings::settings_set_milvus_credentials,
            commands::settings::settings_get_api_key,
            commands::settings::settings_set_api_key,
            commands::settings::settings_get_llm_config,
            commands::settings::settings_set_llm_config,
            commands::settings::settings_get_system_prompt,
            commands::settings::settings_set_system_prompt,
            commands::settings::settings_get_approval_config,
            commands::settings::settings_set_approval_config,
            commands::settings::settings_get_sandbox_config,
            commands::settings::settings_set_sandbox_config,
            commands::settings::settings_get_knowledge_config,
            commands::settings::settings_set_knowledge_config,
            commands::settings::settings_get_subagents_config,
            commands::settings::settings_set_subagents_config,
            commands::settings::settings_get_team_subagents_config,
            commands::settings::settings_set_team_subagents_config,
            commands::settings::settings_get_custom_subagents,
            commands::settings::settings_set_custom_subagents,
            commands::settings::settings_add_custom_subagent,
            commands::settings::settings_remove_custom_subagent,
            commands::settings::settings_get_tools_config,
            commands::settings::settings_set_tools_config,
            commands::settings::settings_get_profile_auto_extract,
            commands::settings::settings_set_profile_auto_extract,
            commands::settings::settings_get_dream_enabled,
            commands::settings::settings_set_dream_enabled,
            commands::settings::settings_get_mcp_servers_config,
            commands::settings::settings_set_mcp_servers_config,
            commands::settings::settings_get_model_entries,
            commands::settings::settings_set_model_entries,
            commands::settings::settings_get_active_model_id,
            commands::settings::settings_activate_model,
            commands::settings::settings_reveal_api_key,
            // === Dialog 命令（4 个）===
            commands::dialog::dialog_open_file,
            commands::dialog::dialog_open_folder,
            commands::dialog::dialog_save_file,
            commands::dialog::dialog_save_dropped_file,
            // === Analysis 命令（1 个）===
            commands::analysis::analysis_launch_powershell,
            // === Shell 命令（3 个）===
            commands::shell::shell_reveal_in_folder,
            commands::shell::shell_open_in_editor,
            commands::shell::shell_open_external,
            // === Window 命令（6 个）===
            commands::window::window_minimize,
            commands::window::window_maximize,
            commands::window::window_close,
            commands::window::window_is_maximized,
            commands::window::window_minimize_by_label,
            commands::window::window_close_by_label,
            // === Clipboard 命令（2 个）===
            commands::clipboard::clipboard_read,
            commands::clipboard::clipboard_write,
            // === Notify 命令（1 个）===
            commands::notify::notify_show,
            // === Logs 命令（1 个）===
            commands::logs::logs_read,
            // === App 命令（9 个）===
            commands::app::app_get_version,
            commands::app::app_quit,
            commands::app::app_restart,
            commands::app::app_restart_backend,
            commands::app::app_reload_backend_config,
            commands::app::app_init_agents_md,
            commands::app::app_get_home_workspace_dir,
            commands::app::app_get_dev_mode,
            commands::app::app_set_dev_mode,
            // === Git 命令（9 个）===
            commands::git::git_get_status,
            commands::git::git_get_log,
            commands::git::git_get_branches,
            commands::git::git_checkout,
            commands::git::git_stage,
            commands::git::git_unstage,
            commands::git::git_commit,
            commands::git::git_discard_changes,
            commands::git::git_get_diff,
        ])
        .setup(|app| {
            log::info!(
                "AgentX Tauri shell started (version: {})",
                app.package_info().version
            );

            // 迁移 electron-store 数据到 tauri-plugin-store（enc: 值记录到 requires_reinput）
            // 必须在 migrate_legacy_llm_config 之前执行，确保旧 config.json 已处理
            match migration::migrate_electron_store(app.handle()) {
                Ok(report) => {
                    if !report.requires_reinput.is_empty() {
                        log::warn!(
                            "迁移完成，以下凭证需重新输入: {:?}",
                            report.requires_reinput
                        );
                    }
                }
                Err(e) => {
                    log::warn!("electron-store 迁移失败（继续启动）: {}", e);
                }
            }

            // 迁移旧版 LLM 配置（若需要）
            store::migrate_legacy_llm_config(app.handle());

            // 初始化 PythonHandle 状态（None，setup 后台任务填充）
            app.manage(PythonState::new(None));

            // 注册窗口事件（maximized-change 推送）
            commands::window::register_window_events(app.handle());

            // 显式为所有已创建的窗口设置应用图标
            // （tauri.conf.json::bundle.icon 只控制打包后的 EXE 安装图标，
            // 运行时窗口图标需要通过 WebviewWindow::set_icon 显式设置）
            let app_icon = build_app_icon();
            for (label, window) in app.webview_windows() {
                if let Err(e) = window.set_icon(app_icon.clone()) {
                    log::warn!("failed to set icon for window '{label}': {e}");
                } else {
                    log::info!("set icon for window '{label}'");
                }
            }

            // 启动 Python 后端
            let handle = app.handle().clone();
            let cwd = backend::resolve_backend_cwd(app.handle());
            tauri::async_runtime::spawn(async move {
                let env = backend::env::build_env(&handle, PYTHON_PORT);
                let py =
                    backend::handle::PythonHandle::start(handle.clone(), cwd, PYTHON_PORT, env);
                // 启动握手：轮询健康端点
                let ok = py.wait_for_ready(&handle, PYTHON_PORT).await;
                if !ok {
                    log::warn!("python waitForReady returned false (timeout or stopped)");
                }
                // 将 handle 存入 app state，后续 stop/restart 时使用
                if let Some(state) = handle.try_state::<PythonState>() {
                    if let Ok(mut guard) = state.lock() {
                        *guard = Some(py);
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            match event {
                // 窗口创建时设置图标（setup 中窗口尚未创建）
                tauri::WindowEvent::Focused(_) => {
                    static ONCE: std::sync::Once = std::sync::Once::new();
                    ONCE.call_once(|| {
                        let app_icon = build_app_icon();
                        if let Err(e) = window.set_icon(app_icon) {
                            log::warn!("failed to set icon on window focus: {e}");
                        } else {
                            log::info!("set icon on window focus");
                        }
                    });
                }
                // 主窗口关闭请求：拦截，走 cleanup 链清理 Python + vite/node 子进程后
                // 再真正销毁窗口。幂等（AtomicBool 守卫）。
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    if window.label() == "main" {
                        api.prevent_close();
                        let _ = window.emit("app:closing", serde_json::json!({}));
                        log::info!("main window close requested, dispatching cleanup");
                        let app_handle = window.app_handle().clone();
                        let win = window.clone();
                        tauri::async_runtime::spawn(async move {
                            backend::cleanup::cleanup_all(app_handle.clone()).await;
                            // 清理完后真正销毁窗口（幂等 cleanup 不会二次走这条路径）
                            win.destroy().ok();
                        });
                    }
                }
                _ => {}
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running AgentX tauri application");
}
