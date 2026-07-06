//! 日志落盘
//!
//! 对应 `frontend/main/logger.ts`：按日滚动写入 `app_data_dir/logs/agentx-{YYYYMMDD}.log`。
//! `read_logs` 供 `logs:read` 命令调用，`append_log` 供主进程关键路径落盘。

use std::fs;
use std::path::PathBuf;

use chrono::NaiveDate;
use tauri::AppHandle;
use tauri::Manager;

/// 返回日志目录：`{app_data_dir}/logs/`。
fn log_dir(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_data_dir().ok().map(|d| d.join("logs"))
}

/// 格式化日期为 `YYYYMMDD`。
fn format_date(date: chrono::NaiveDate) -> String {
    date.format("%Y%m%d").to_string()
}

/// 返回指定日期的日志文件路径（date 为 None 时用当天）。
fn log_file_path(app: &AppHandle, date: Option<&str>) -> Option<PathBuf> {
    let dir = log_dir(app)?;
    let date_str = match date {
        Some(d) if !d.is_empty() => d.to_string(),
        _ => format_date(chrono::Local::now().date_naive()),
    };
    Some(dir.join(format!("agentx-{}.log", date_str)))
}

/// 确保日志目录存在。
fn ensure_log_dir(app: &AppHandle) {
    if let Some(dir) = log_dir(app) {
        let _ = fs::create_dir_all(&dir);
    }
}

/// 追加一行日志（自动加 `[HH:MM:SS]` 前缀）。
pub fn append_log(app: &AppHandle, line: &str) {
    ensure_log_dir(app);
    let path = match log_file_path(app, None) {
        Some(p) => p,
        None => return,
    };
    let now = chrono::Local::now();
    let entry = format!("[{}] {}\n", now.format("%H:%M:%S"), line);
    let _ = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .and_then(|mut f| {
            use std::io::Write;
            f.write_all(entry.as_bytes())
        });
}

/// 读取指定日期日志的最后 N 行（默认 200）。
pub fn read_logs(app: &AppHandle, date: Option<&str>, max_lines: usize) -> Vec<String> {
    let path = match log_file_path(app, date) {
        Some(p) if p.exists() => p,
        _ => return Vec::new(),
    };
    let content = match fs::read_to_string(&path) {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    let lines: Vec<&str> = content.lines().filter(|l| !l.is_empty()).collect();
    let start = if lines.len() > max_lines {
        lines.len() - max_lines
    } else {
        0
    };
    lines[start..].iter().map(|s| s.to_string()).collect()
}

/// 删除超过 maxDays 天的日志文件（按文件名中的日期判定）。
pub fn clean_old_logs(app: &AppHandle, max_days: u64) {
    let dir = match log_dir(app) {
        Some(d) if d.exists() => d,
        _ => return,
    };
    let entries = match fs::read_dir(&dir) {
        Ok(e) => e,
        Err(_) => return,
    };
    let today = chrono::Local::now().date_naive();
    let cutoff = chrono::Duration::days(max_days as i64);
    for entry in entries.flatten() {
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        // 文件名格式：agentx-YYYYMMDD.log
        if let Some(date_str) = name_str
            .strip_prefix("agentx-")
            .and_then(|s| s.strip_suffix(".log"))
        {
            if let Ok(date) = NaiveDate::parse_from_str(date_str, "%Y%m%d") {
                if today - date > cutoff {
                    let _ = fs::remove_file(entry.path());
                }
            }
        }
    }
}
