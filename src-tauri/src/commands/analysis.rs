//! 分析任务命令（1 个）
//!
//! 复盘任务通过独立 PowerShell 窗口异步执行。
//! claude CLI 以交互模式运行，用户在终端查看复盘报告并可直接对话执行优化。

use std::path::PathBuf;
use std::process::Command;

/// `analysis_launch_powershell` → 在新 PowerShell 窗口中启动 claude CLI 交互模式。
///
/// 从 `CARGO_MANIFEST_DIR` 推导项目根目录（src-tauri 的 parent），
/// spawn `powershell.exe -NoExit -File scripts/analysis-run.ps1` 传入 prompt 文件路径。
/// PowerShell 窗口独立于 AgentX 主窗口，关闭不影响主应用。
#[tauri::command]
pub fn analysis_launch_powershell(prompt_file: String) -> Result<(), String> {
    let project_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or("无法解析项目根目录")?
        .to_path_buf();

    let script_path = project_root.join("scripts").join("analysis-run.ps1");

    Command::new("powershell.exe")
        .args([
            "-NoExit",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            &script_path.to_string_lossy(),
            "-PromptFile",
            &prompt_file,
            "-ProjectRoot",
            &project_root.to_string_lossy(),
        ])
        .spawn()
        .map_err(|e| format!("启动 PowerShell 失败: {e}"))?;

    Ok(())
}
