<#
.SYNOPSIS
    AgentX 执行轨迹复盘 — 在 PowerShell 中启动 Claude CLI 交互模式。

.DESCRIPTION
    由 Tauri analysis_launch_powershell 命令调用。

    两阶段执行：
    1. 阶段 1 (headless)：claude -p --dangerously-skip-permissions 跑完整个复盘任务，
       报告直接打印到当前终端。会话自动持久化（-p 默认不传 --no-session-persistence）。
    2. 阶段 2 (interactive)：报告输出后 Read-Host 暂停，用户按 Enter 启动
       claude --dangerously-skip-permissions -c 继续当前会话，进入 Ink TUI 交互模式，
       可在终端中多轮对话、确认操作、执行后续优化。

    权限策略：bypassPermissions（自动批准所有工具调用，不弹权限确认）。
    prompt 文件通过 stdin 管道传给 claude -p（PowerShell 5.1 需 Console.OutputEncoding=UTF8）。

.PARAMETER PromptFile
    后端 export-trace 端点导出的 prompt 文件绝对路径。

.PARAMETER ProjectRoot
    项目根目录路径（claude CLI 的工作目录）。

.EXAMPLE
    powershell.exe -NoExit -File scripts/analysis-run.ps1 -PromptFile "D:\...\data\traces\abc.md" -ProjectRoot "D:\java\agentprojects\agentx"
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$PromptFile,

    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot
)

# === 关键：让 PowerShell 5.1 写管道到外部进程时用 UTF-8 编码 ===
# 默认 [Console]::OutputEncoding = 系统 ANSI 代码页（中文 Windows = GBK）。
# `$str | external.exe` 会用该编码把 .NET String 重编码成字节写 stdin，
# Node.js claude.exe 收到 GBK 字节却按 UTF-8 解码 → 中文变 "?"。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location $ProjectRoot

Write-Host ""
Write-Host "==== AgentX 执行轨迹复盘 ====" -ForegroundColor Cyan
Write-Host "项目根目录:    $ProjectRoot" -ForegroundColor DarkGray
Write-Host "Prompt 文件:   $PromptFile" -ForegroundColor DarkGray
Write-Host "权限模式:      bypassPermissions (跳过所有权限确认)" -ForegroundColor DarkGray
Write-Host "工作模式:      交互模式 (Ink TUI 多轮对话)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "阶段 1: headless 跑完复盘任务，报告直接打印到本窗口" -ForegroundColor Yellow
Write-Host "阶段 2: 报告结束后按 Enter 进入 Ink TUI 交互模式，可继续对话" -ForegroundColor Yellow
Write-Host ""
Write-Host "---- Claude 复盘报告 (阶段 1: headless) ----" -ForegroundColor Green
Write-Host ""

$prompt = Get-Content -Raw -Encoding UTF8 $PromptFile
$prompt | claude -p --model sonnet --dangerously-skip-permissions --max-turns 25

Write-Host ""
Write-Host "---- 复盘报告结束 ----" -ForegroundColor Green
Write-Host ""
Write-Host "阶段 2: 按 Enter 启动 Ink TUI 交互模式继续对话；按 Ctrl+C 跳过。" -ForegroundColor Yellow
$null = Read-Host

Write-Host ""
Write-Host "---- Claude 交互模式 (阶段 2: Ink TUI) ----" -ForegroundColor Green
Write-Host "退出交互模式: 输入 /quit 或 Ctrl+C`n" -ForegroundColor DarkGray

# -c continue 上面的 -p 会话
claude --model sonnet --dangerously-skip-permissions -c

Write-Host ""
Write-Host "---- 复盘会话已结束 ----" -ForegroundColor Green
Write-Host "关闭窗口退出..." -ForegroundColor DarkGray
$null = Read-Host