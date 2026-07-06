//! 系统通知命令（1 个）
//!
//! 对应原 Electron IPC handler `notify:show`。

use serde::Deserialize;
use tauri::AppHandle;
use tauri_plugin_notification::NotificationExt;

/// `notify:show` 参数（对应 Electron `Notification` 构造参数）。
#[derive(Debug, Deserialize)]
pub struct NotifyOptions {
    pub title: String,
    pub body: String,
}

/// `notify:show` → 显示系统通知。
#[tauri::command]
pub fn notify_show(app: AppHandle, opts: NotifyOptions) -> Result<(), String> {
    app.notification()
        .builder()
        .title(opts.title)
        .body(opts.body)
        .show()
        .map_err(|e| e.to_string())
}
