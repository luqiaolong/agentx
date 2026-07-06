//! AgentX Tauri 主进程入口
//!
//! Phase 1 骨架：仅注册 10 个官方插件 + 日志，verify `cargo check` 通过。
//! 后续 Phase 将在 setup() 中注入 Python spawn、commands 注册等逻辑。

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
            // Phase 3 将替换为从 OS keychain 读取的密钥派生函数
            tauri_plugin_stronghold::Builder::new(|_salt| {
                b"agentx-desktop-vault-key-phase1-placeholder".to_vec()
            })
            .build(),
        )
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            // Phase 2 将在此启动 Python 后端
            log::info!(
                "AgentX Tauri shell started (version: {})",
                app.package_info().version
            );
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running AgentX tauri application");
}
