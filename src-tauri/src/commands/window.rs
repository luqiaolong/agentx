//! 窗口控制命令（4 个）+ 最大化状态事件
//!
//! 对应原 Electron IPC handler `window:*`：
//! - `window:minimize` → `window_minimize`
//! - `window:maximize` → `window_maximize`（toggle 语义：最大化/还原切换）
//! - `window:close` → `window_close`
//! - `window:isMaximized` → `window_is_maximized`
//!
//! `window:maximized-change` 事件由 `on_window_event` 自动 emit 给前端。

use tauri::{AppHandle, Emitter, Manager, WindowEvent};

/// `window:minimize` → 最小化主窗口。
#[tauri::command]
pub fn window_minimize(app: AppHandle) -> Result<(), String> {
    let main = app
        .get_webview_window("main")
        .ok_or("main window not found")?;
    main.minimize().map_err(|e| e.to_string())
}

/// `window:maximize` → 最大化/还原切换（toggle 语义，与 Electron 实现一致）。
#[tauri::command]
pub fn window_maximize(app: AppHandle) -> Result<(), String> {
    let main = app
        .get_webview_window("main")
        .ok_or("main window not found")?;
    if main.is_maximized().unwrap_or(false) {
        main.unmaximize().map_err(|e| e.to_string())
    } else {
        main.maximize().map_err(|e| e.to_string())
    }
}

/// `window:close` → 关闭主窗口（应用退出）。
#[tauri::command]
pub fn window_close(app: AppHandle) -> Result<(), String> {
    let main = app
        .get_webview_window("main")
        .ok_or("main window not found")?;
    main.close().map_err(|e| e.to_string())
}

/// `window:isMaximized` → 查询主窗口是否最大化。
#[tauri::command]
pub fn window_is_maximized(app: AppHandle) -> bool {
    app.get_webview_window("main")
        .and_then(|w| w.is_maximized().ok())
        .unwrap_or(false)
}

/// 注册窗口事件监听：窗口大小变化时 emit `window:maximized-change` 事件。
///
/// 应在 `setup()` 中调用，对应 Electron `mainWindow.on("maximize"/"unmaximize")`。
pub fn register_window_events(app: &AppHandle) {
    if let Some(main) = app.get_webview_window("main") {
        let main_clone = main.clone();
        let app_clone = app.clone();
        main.on_window_event(move |event| {
            if matches!(event, WindowEvent::Resized(_)) {
                let is_max = main_clone.is_maximized().unwrap_or(false);
                let _ = app_clone.emit("window:maximized-change", is_max);
            }
        });
    }
}
