//! 日志命令（1 个）
//!
//! 对应原 Electron IPC handler `logs:read`，读取指定日期日志的最后 N 行。

use tauri::AppHandle;

use crate::logger;

/// `logs:read` → 读取指定日期日志的最后 N 行（默认 200）。
///
/// - `date`：YYYYMMDD 格式，None 时返回当天日志
/// - `max_lines`：返回最后 N 行，默认 200
#[tauri::command]
pub fn logs_read(
    app: AppHandle,
    date: Option<String>,
    max_lines: Option<usize>,
) -> Vec<String> {
    let max = max_lines.unwrap_or(200);
    logger::read_logs(&app, date.as_deref(), max)
}
