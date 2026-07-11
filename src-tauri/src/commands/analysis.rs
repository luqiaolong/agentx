//! 分析任务命令（1 个）
//!
//! 复盘任务通过独立 PowerShell 窗口异步执行。
//! claude CLI 以**交互模式**（Ink TUI）运行，用户在终端查看复盘报告并可直接对话执行优化。

use std::path::PathBuf;
use std::process::Command;

/// `analysis_launch_powershell` → 在新 PowerShell 窗口中启动 claude CLI 交互模式。
///
/// 通过 Windows Terminal (`wt.exe`) 启动新 tab/窗口，传入 PowerShell 脚本。
///
/// 为什么用 `wt.exe` 而不是 `cmd.exe /c start`：
/// - 交互模式（`claude` 不带 `-p`）使用 Ink TUI，必须依附真正的 TTY/ConPTY
/// - `cmd start` 启动的 PowerShell 子进程 stdin 不是真实 TTY，Ink 立刻报
///   "Raw mode is not supported on the current process.stdin" 然后崩溃
/// - `wt.exe` 给子进程分配 ConPTY，Ink 在它内部能正常工作
///
/// **不传 stdin prompt**：交互模式下 prompt 由用户在终端直接看到后对话；
/// 当前实现把 prompt 文件路径作为初始消息透传给脚本，由脚本读取后作为
/// `claude` 启动时的第一条 user 消息（通过 `claude <prompt-file-path>` 或
/// 脚本内置 `claude --resume`-风格上下文注入）。
///
/// 失败兜底：如果 `wt.exe` 不可用，回退到 `cmd.exe /c start` 弹窗，
/// 此时 Ink 会崩，用户能直接看到错误而不是完全无响应。
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

    // 首选 wt.exe (Windows Terminal)，提供 ConPTY 支持 Ink TUI
    // 回退 cmd.exe /c start（无 ConPTY，Ink 会崩但至少能弹窗看到错误）
    let candidates: &[(&str, &[&str])] = &[
        (
            "wt.exe",
            &[
                "-d",
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
            ],
        ),
        (
            "cmd.exe",
            &[
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
            ],
        ),
    ];

    let mut last_err: Option<String> = None;
    for (prog, args) in candidates {
        log::info!("[analysis] trying spawn: {} {:?}", prog, args);
        match Command::new(prog).args(*args).spawn() {
            Ok(child) => {
                log::info!(
                    "[analysis] {} spawned, pid={:?}, terminal should appear",
                    prog,
                    child.id()
                );
                return Ok(());
            }
            Err(e) => {
                let msg = format!("{} spawn failed: {}", prog, e);
                log::warn!("[analysis] {}", msg);
                last_err = Some(msg);
            }
        }
    }

    let msg = last_err.unwrap_or_else(|| "无可用终端启动器".to_string());
    log::error!("[analysis] {}", msg);
    Err(msg)
}