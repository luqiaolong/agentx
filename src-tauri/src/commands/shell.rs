//! Shell 命令（3 个）
//!
//! 对应原 Electron IPC handler `shell:*`：
//! - `shell:revealInFolder` → `shell_reveal_in_folder`（在文件管理器中显示文件）
//! - `shell:openInEditor` → `shell_open_in_editor`（用系统默认编辑器打开）
//! - `shell:openExternal` → `shell_open_external`（用浏览器打开 URL）
//!
//! 使用 `tauri-plugin-shell` 的 `open` 方法 + 手动实现 `reveal_in_folder`。

use std::process::Command;
use tauri::AppHandle;
use tauri_plugin_shell::ShellExt;

/// `shell:revealInFolder` → 在文件管理器中显示文件。
///
/// 与 Electron `shell.showItemInFolder` 一致：
/// - Windows: `explorer.exe /select,<path>`
/// - macOS: `open -R <path>`
/// - Linux: `xdg-open <parent_dir>`（Linux 无 "reveal" 等价命令）
#[tauri::command]
pub fn shell_reveal_in_folder(_app: AppHandle, path: String) {
    #[cfg(windows)]
    {
        // explorer.exe 期望 `/select,<path>` 作为单个参数；拆成两个独立参数
        // 会导致 explorer 打开 Documents 而非选中目标文件。
        let select_arg = format!("/select,{}", path);
        let _ = Command::new("explorer.exe").arg(&select_arg).spawn();
    }
    #[cfg(target_os = "macos")]
    {
        let _ = Command::new("open").args(["-R", &path]).spawn();
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        // Linux 无 "reveal" 等价命令，打开父目录
        let p = std::path::Path::new(&path);
        let parent = p.parent().unwrap_or(p);
        let _ = Command::new("xdg-open").arg(parent).spawn();
    }
    #[cfg(not(any(windows, target_os = "macos", unix)))]
    {
        let _ = path;
        log::warn!("shell_reveal_in_folder not implemented for this platform");
    }
}

/// `shell:openInEditor` → 用系统默认编辑器打开文件。
///
/// 与 Electron `shell.openPath` 一致，调用系统关联程序打开文件。
#[tauri::command]
#[allow(deprecated)]
pub fn shell_open_in_editor(_app: AppHandle, path: String) -> Result<String, String> {
    _app.shell().open(path, None).map_err(|e| e.to_string())?;
    Ok(String::new())
}

/// `shell:openExternal` → 用默认浏览器打开 URL。
#[tauri::command]
#[allow(deprecated)]
pub fn shell_open_external(_app: AppHandle, url: String) -> Result<(), String> {
    _app.shell().open(url, None).map_err(|e| e.to_string())
}
