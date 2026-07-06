//! 剪贴板命令（2 个）
//!
//! 对应原 Electron IPC handler `clipboard:*`：
//! - `clipboard:read` → `clipboard_read`
//! - `clipboard:write` → `clipboard_write`

use tauri::AppHandle;
use tauri_plugin_clipboard_manager::ClipboardExt;

/// `clipboard:read` → 读取剪贴板文本。
#[tauri::command]
pub fn clipboard_read(app: AppHandle) -> Result<String, String> {
    app.clipboard()
        .read_text()
        .map_err(|e| e.to_string())
}

/// `clipboard:write` → 写入剪贴板文本。
#[tauri::command]
pub fn clipboard_write(app: AppHandle, text: String) -> Result<(), String> {
    app.clipboard()
        .write_text(text)
        .map_err(|e| e.to_string())
}
