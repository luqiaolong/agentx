//! Git 命令（9 个）
//!
//! 对应原 Electron IPC handler `git:*`：
//! - `git:getStatus` → `git_get_status`
//! - `git:getLog` → `git_get_log`
//! - `git:getBranches` → `git_get_branches`
//! - `git:checkout` → `git_checkout`
//! - `git:stage` → `git_stage`
//! - `git:unstage` → `git_unstage`
//! - `git:commit` → `git_commit`
//! - `git:discardChanges` → `git_discard_changes`
//! - `git:getDiff` → `git_get_diff`
//!
//! 底层实现位于 `crate::git`，通过 `std::process::Command` 调用 `git` CLI
//! （替代原 Electron 的 dugite N-API 依赖）。

use crate::git;

/// `git:getStatus` → 返回仓库状态 + 文件条目列表。
#[tauri::command]
pub fn git_get_status(repo_path: String) -> git::GitStatusResult {
    git::get_status(&repo_path)
}

/// `git:getLog` → 返回最近 N 条提交记录。
#[tauri::command]
pub fn git_get_log(repo_path: String, limit: Option<usize>) -> git::GitLogResult {
    let limit = limit.unwrap_or(50);
    git::get_log(&repo_path, limit)
}

/// `git:getBranches` → 返回所有本地 + 远程分支。
#[tauri::command]
pub fn git_get_branches(repo_path: String) -> git::GitBranchesResult {
    git::get_branches(&repo_path)
}

/// `git:checkout` → 切换分支。
#[tauri::command]
pub fn git_checkout(repo_path: String, branch: String) -> git::GitOpResult {
    git::checkout(&repo_path, &branch)
}

/// `git:stage` → `git add -- <files>`。
#[tauri::command]
pub fn git_stage(repo_path: String, files: Vec<String>) -> git::GitOpResult {
    git::stage(&repo_path, &files)
}

/// `git:unstage` → `git reset HEAD -- <files>`。
#[tauri::command]
pub fn git_unstage(repo_path: String, files: Vec<String>) -> git::GitOpResult {
    git::unstage(&repo_path, &files)
}

/// `git:commit` → `git commit -m <message>`。
#[tauri::command]
pub fn git_commit(repo_path: String, message: String) -> git::GitOpResult {
    git::commit(&repo_path, &message)
}

/// `git:discardChanges` → `git checkout -- <files>`。
#[tauri::command]
pub fn git_discard_changes(repo_path: String, files: Vec<String>) -> git::GitOpResult {
    git::discard_changes(&repo_path, &files)
}

/// `git:getDiff` → `git diff [-- <file>]`，file 为 None 时返回整个仓库 diff。
#[tauri::command]
pub fn git_get_diff(repo_path: String, file: Option<String>) -> git::GitDiffResult {
    git::get_diff(&repo_path, file.as_deref())
}
