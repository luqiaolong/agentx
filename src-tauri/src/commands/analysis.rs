//! 分析任务命令（1 个）
//!
//! 复盘任务通过独立 PowerShell 窗口异步执行。
//! claude CLI 以交互模式运行，用户在终端查看复盘报告并可直接对话执行优化。

use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;

#[cfg(windows)]
const CREATE_NEW_CONSOLE: u32 = 0x00000010;
#[cfg(windows)]
const DETACHED_PROCESS: u32 = 0x00000008;

/// `analysis_launch_powershell` → 在新 PowerShell 窗口中启动 claude CLI 交互模式。
///
/// 从 `CARGO_MANIFEST_DIR` 推导项目根目录（src-tauri 的 parent），
/// 通过 `cmd.exe /c start "AgentX 复盘" powershell ...` 启动新窗口。
///
/// `cmd start` 会自动使用 `CREATE_NEW_CONSOLE` 标志创建独立 console 窗口，
/// 是 Windows 上最可靠的"从 GUI 进程弹出可见终端窗口"的做法。
/// PowerShell 窗口独立于 AgentX 主窗口，关闭不影响主应用。
#[tauri::command]
pub fn analysis_launch_powershell(prompt_file: String) -> Result<(), String> {
    eprintln!("[analysis] invoke received, prompt_file={}", prompt_file);

    let project_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or_else(|| {
            eprintln!(
                "[analysis] 无法解析项目根目录 (CARGO_MANIFEST_DIR={})",
                env!("CARGO_MANIFEST_DIR")
            );
            "无法解析项目根目录".to_string()
        })?
        .to_path_buf();

    let script_path = project_root.join("scripts").join("analysis-run.ps1");
    eprintln!(
        "[analysis] project_root={} script_path={}",
        project_root.display(),
        script_path.display()
    );

    if !script_path.exists() {
        let msg = format!("脚本不存在: {}", script_path.display());
        eprintln!("[analysis] {}", msg);
        return Err(msg);
    }

    // 用 cmd.exe /c start 启动 powershell.exe
    // start 的 /B 标志会阻止新窗口，但我们需要新窗口所以不加 /B
    // "AgentX 复盘" 是窗口标题
    let mut cmd = Command::new("cmd.exe");
    cmd.args([
        "/c",
        "start",
        "\"AgentX 复盘\"",
        "/D",
        &project_root.to_string_lossy(),
        "powershell.exe",
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
        // CREATE_NEW_CONSOLE 强制新窗口，DETACHED_PROCESS 让父进程不等待
        cmd.creation_flags(CREATE_NEW_CONSOLE | DETACHED_PROCESS);
    }

    match cmd.spawn() {
        Ok(child) => {
            eprintln!(
                "[analysis] cmd spawned, pid={:?}, powershell should appear in new window",
                child.id()
            );
            Ok(())
        }
        Err(e) => {
            let msg = format!("启动 PowerShell 失败: {e}");
            eprintln!("[analysis] {}", msg);
            Err(msg)
        }
    }
}
