//! Dialog 命令（4 个）
//!
//! 对应原 Electron IPC handler `dialog:*`：
//! - `dialog:openFile` → `dialog_open_file`
//! - `dialog:openFolder` → `dialog_open_folder`
//! - `dialog:saveFile` → `dialog_save_file`
//! - `dialog:saveDroppedFile` → `dialog_save_dropped_file`（含系统目录黑名单 + 大小限制）

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;
use uuid::Uuid;

use crate::store;

// =============================================================================
// 返回类型
// =============================================================================

/// `dialog:openFile` 返回结果（对应 Electron `OpenDialogReturnValue`）。
#[derive(Debug, Serialize)]
pub struct OpenDialogResult {
    pub canceled: bool,
    pub file_paths: Vec<String>,
}

/// `dialog:saveFile` 返回结果（对应 Electron `SaveDialogReturnValue`）。
#[derive(Debug, Serialize)]
pub struct SaveDialogResult {
    pub canceled: bool,
    pub file_path: Option<String>,
}

/// `dialog:openFile` 可选参数（对应 Electron `OpenDialogOptions` 的子集）。
#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct OpenDialogOptions {
    pub title: Option<String>,
    pub default_path: Option<String>,
    pub filters: Option<Vec<DialogFilter>>,
}

/// 文件过滤器（对应 Electron `FileFilter`）。
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DialogFilter {
    pub name: String,
    pub extensions: Vec<String>,
}

/// `dialog:saveFile` 可选参数。
#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct SaveDialogOptions {
    pub title: Option<String>,
    pub default_path: Option<String>,
    pub filters: Option<Vec<DialogFilter>>,
}

// =============================================================================
// 命令实现
// =============================================================================

/// `dialog:openFile` → 打开文件选择对话框。
#[tauri::command]
pub async fn dialog_open_file(
    app: AppHandle,
    opts: Option<OpenDialogOptions>,
) -> Result<OpenDialogResult, String> {
    let main_window = app.get_webview_window("main").ok_or("main window not found")?;
    let mut builder = main_window.dialog().file().add_filter("All Files", &["*"]);
    if let Some(opts) = opts {
        if let Some(title) = opts.title {
            builder = builder.set_title(title);
        }
        if let Some(default_path) = opts.default_path {
            builder = builder.set_directory(default_path);
        }
        if let Some(filters) = opts.filters {
            for f in filters {
                let exts: Vec<&str> = f.extensions.iter().map(|s| s.as_str()).collect();
                builder = builder.add_filter(&f.name, &exts);
            }
        }
    }

    match builder.blocking_pick_file() {
        Some(file_path) => Ok(OpenDialogResult {
            canceled: false,
            file_paths: vec![file_path.to_string()],
        }),
        None => Ok(OpenDialogResult {
            canceled: true,
            file_paths: vec![],
        }),
    }
}

/// `dialog:openFolder` → 打开目录选择对话框。
#[tauri::command]
pub async fn dialog_open_folder(app: AppHandle) -> Result<OpenDialogResult, String> {
    let main_window = app.get_webview_window("main").ok_or("main window not found")?;
    match main_window.dialog().file().blocking_pick_folder() {
        Some(folder_path) => Ok(OpenDialogResult {
            canceled: false,
            file_paths: vec![folder_path.to_string()],
        }),
        None => Ok(OpenDialogResult {
            canceled: true,
            file_paths: vec![],
        }),
    }
}

/// `dialog:saveFile` → 打开保存文件对话框。
#[tauri::command]
pub async fn dialog_save_file(
    app: AppHandle,
    opts: Option<SaveDialogOptions>,
) -> Result<SaveDialogResult, String> {
    let main_window = app.get_webview_window("main").ok_or("main window not found")?;
    let mut builder = main_window.dialog().file();
    if let Some(opts) = opts {
        if let Some(title) = opts.title {
            builder = builder.set_title(title);
        }
        if let Some(default_path) = opts.default_path {
            builder = builder.set_directory(default_path);
        }
        if let Some(filters) = opts.filters {
            for f in filters {
                let exts: Vec<&str> = f.extensions.iter().map(|s| s.as_str()).collect();
                builder = builder.add_filter(&f.name, &exts);
            }
        }
    }

    match builder.blocking_save_file() {
        Some(file_path) => Ok(SaveDialogResult {
            canceled: false,
            file_path: Some(file_path.to_string()),
        }),
        None => Ok(SaveDialogResult {
            canceled: true,
            file_path: None,
        }),
    }
}

/// `dialog:saveDroppedFile` → 复制拖拽文件到 `data/uploads/{uuid}_{fileName}`。
///
/// 安全措施（与 [frontend/main/index.ts:242-288](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L242-L288) 一致）：
/// - 系统目录黑名单（防 renderer XSS 后借 copyFile 读取系统敏感文件）
/// - 文件大小校验（受 `approval.maxUploadBytes` 限制）
/// - 文件名 path.basename 清洗（防路径穿越）
#[tauri::command]
pub async fn dialog_save_dropped_file(
    app: AppHandle,
    file_path: String,
    file_name: String,
) -> Result<String, String> {
    let max_bytes = store::get_approval_config(&app).max_upload_bytes as u64;

    // 系统目录黑名单
    let resolved_src = Path::new(&file_path)
        .canonicalize()
        .map_err(|_| format!("无法读取源文件: {}", file_name))?;
    let lower_src = resolved_src.to_string_lossy().to_lowercase();
    let system_prefixes = [
        "c:\\windows\\",
        "c:\\program files\\",
        "c:\\program files (x86)\\",
        "c:\\programdata\\",
        "c:\\system volume information\\",
        "/etc/",
        "/usr/",
        "/bin/",
        "/sbin/",
        "/var/",
        "/boot/",
        "/proc/",
        "/sys/",
        "/system/",
        "/private/",
    ];
    if system_prefixes
        .iter()
        .any(|p| lower_src.starts_with(p) || lower_src == &p[..p.len() - 1])
    {
        log::warn!("saveDroppedFile rejected system path: {}", resolved_src.display());
        return Err("不允许读取系统目录文件".into());
    }

    // 文件大小校验
    let size = fs::metadata(&file_path)
        .map(|m| m.len())
        .map_err(|_| format!("无法读取源文件: {}", file_name))?;
    if size > max_bytes {
        log::warn!(
            "saveDroppedFile rejected: {} size={} > max={}",
            file_name,
            size,
            max_bytes
        );
        return Err(format!("文件大小 {} 超过上限 {} 字节", size, max_bytes));
    }

    // 获取后端工作目录
    let backend_cwd = resolve_backend_cwd(&app);
    let upload_dir = backend_cwd.join("data").join("uploads");
    fs::create_dir_all(&upload_dir)
        .map_err(|e| format!("无法创建上传目录: {}", e))?;

    // 文件名清洗：取 basename，防路径穿越
    let safe_name = Path::new(&file_name)
        .file_name()
        .and_then(|n| n.to_str())
        .filter(|s| !s.is_empty() && *s != "." && *s != "..")
        .ok_or_else(|| "非法文件名".to_string())?
        .to_string();

    let id = Uuid::new_v4();
    let dest_name = format!("{}_{}", id, safe_name);
    let dest_path = upload_dir.join(&dest_name);
    fs::copy(&file_path, &dest_path)
        .map_err(|e| format!("复制文件失败: {}", e))?;

    let relative = format!("data/uploads/{}", dest_name);
    log::info!("saveDroppedFile saved {} -> {}", safe_name, relative);
    Ok(relative)
}

/// 解析后端工作目录（与 lib.rs 的 `resolve_backend_cwd` 一致逻辑）。
fn resolve_backend_cwd(app: &AppHandle) -> PathBuf {
    if let Ok(resource_dir) = app.path().resource_dir() {
        let backend = resource_dir.join("backend");
        if backend.exists() {
            return backend;
        }
    }
    PathBuf::from("backend")
}
