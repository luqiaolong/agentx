//! 分析任务命令（1 个）
//!
//! 复盘任务通过独立 PowerShell 窗口异步执行。
//! claude CLI 以交互模式运行，用户在终端查看复盘报告并可直接对话执行优化。

use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;

#[cfg(windows)]
const CREATE_NEW_CONSOLE: u32 = 0x00000010;

/// `analysis_launch_powershell` → 在新 PowerShell 窗口中启动 claude CLI 交互模式。
///
/// 从 `CARGO_MANIFEST_DIR` 推导项目根目录（src-tauri 的 parent），
/// spawn `powershell.exe -NoExit -File scripts/analysis-run.ps1` 传入 prompt 文件路径。
///
/// Windows 专属：通过 `CREATE_NEW_CONSOLE` 标志强制 powershell.exe 启动独立 console 窗口，
/// 避免 Tauri GUI 父进程没有 console 导致子进程无可见窗口的问题。
/// PowerShell 窗口独立于 AgentX 主窗口，关闭不影响主应用。
#[tauri::command]
pub fn analysis_launch_powershell(prompt_file: String) -> Result<(), String> {
    let project_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or("无法解析项目根目录")?
        .to_path_buf();

    let script_path = project_root.join("scripts").join("analysis-run.ps1");

    let mut cmd = Command::new("powershell.exe");
    cmd.args([
        "-NoExit",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        &script_path.to_string_lossy(),
        "-PromptFile",
        &prompt_file,
        "-ProjectRoot",
        &project_root.to_string_lossy(),
    ]);

    #[cfg(windows)]
    {
        cmd.creation_flags(CREATE_NEW_CONSOLE);
    }

    cmd.spawn().map_err(|e| format!("启动 PowerShell 失败: {e}"))?;

    Ok(())
}
