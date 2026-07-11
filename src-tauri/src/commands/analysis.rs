//! 分析任务命令（1 个）
//!
//! 复盘任务通过独立 PowerShell 窗口异步执行。
//! claude CLI 以交互模式运行，用户在终端查看复盘报告并可直接对话执行优化。

use std::path::PathBuf;
use std::process::Command;

/// `analysis_launch_powershell` → 在新 PowerShell 窗口中启动 claude CLI 交互模式。
///
/// 通过 `cmd.exe /c start "标题" powershell.exe ...` 启动新窗口。
/// `cmd start` 内部调用 `ShellExecuteW`，会自动为新进程分配独立 console 窗口，
/// 是 Windows 上从 GUI 进程弹出可见终端窗口最可靠的方式。
///
/// **不使用 `CREATE_NEW_CONSOLE` / `DETACHED_PROCESS` 标志**：
/// - 二者互斥（Microsoft 文档明确禁止组合），组合使用会让 `CreateProcessW` 直接失败
/// - `cmd start` 自身已负责新窗口创建，叠加 flags 反而冲突
///
/// PowerShell 窗口独立于 AgentX 主窗口，关闭不影响主应用。
#[tauri::command]
pub fn analysis_launch_powershell(prompt_file: String) -> Result<(), String> {
    log::info!("[analysis] invoke received, prompt_file={}", prompt_file);

    let project_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or_else(|| {
            "无法解析项目根目录".to_string()
        })?
        .to_path_buf();

    let script_path = project_root.join("scripts").join("analysis-run.ps1");
    log::info!(
        "[analysis] project_root={} script_path={}",
        project_root.display(),
        script_path.display()
    );

    if !script_path.exists() {
        let msg = format!("脚本不存在: {}", script_path.display());
        log::error!("[analysis] {}", msg);
        return Err(msg);
    }

    // cmd.exe /c start "标题" /D <工作目录> powershell.exe <args...>
    // start 是 cmd 内建命令，内部走 ShellExecuteW → 新 console 窗口
    let mut cmd = Command::new("cmd.exe");
    cmd.args([
        "/c",
        "start",
        "AgentX 复盘",
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

    match cmd.spawn() {
        Ok(child) => {
            log::info!(
                "[analysis] cmd spawned, pid={:?}, powershell should appear in new window",
                child.id()
            );
            Ok(())
        }
        Err(e) => {
            let msg = format!("启动 PowerShell 失败: {e}");
            log::error!("[analysis] {}", msg);
            Err(msg)
        }
    }
}
