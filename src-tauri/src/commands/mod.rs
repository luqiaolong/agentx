//! Tauri 命令模块
//!
//! 所有 `#[tauri::command]` 函数按功能域分文件组织，
//! 在 `lib.rs` 的 `invoke_handler` 中统一注册。

pub mod app;
pub mod clipboard;
pub mod dialog;
pub mod git;
pub mod logs;
pub mod notify;
pub mod settings;
pub mod shell;
pub mod window;
