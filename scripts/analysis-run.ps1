<#
.SYNOPSIS
    AgentX 执行轨迹复盘 — 在 PowerShell 中启动 Claude CLI headless 模式。

.DESCRIPTION
    由 Tauri analysis_launch_powershell 命令调用。
    读取后端导出的 prompt 文件，通过 stdin 管道传给 claude CLI 的 -p (--print) headless 模式。
    -p 模式不需要 TTY / Ink TUI，可在 cmd /c start 弹出的非交互式窗口中正常运行。
    复盘报告直接打印到终端，用户看完即可关闭窗口。

    权限策略：默认 plan（只读，不执行写操作）。
    如需执行写操作：在新窗口中重跑本脚本并加 -PermissionMode acceptEdits 参数。

.PARAMETER PromptFile
    后端 export-trace 端点导出的 prompt 文件绝对路径。

.PARAMETER ProjectRoot
    项目根目录路径（claude CLI 的工作目录）。

.PARAMETER PermissionMode
    claude --permission-mode 取值。默认 plan。常见值: plan / acceptEdits / bypassPermissions。

.EXAMPLE
    powershell.exe -NoExit -File scripts/analysis-run.ps1 -PromptFile "D:\...\data\traces\abc.md" -ProjectRoot "D:\java\agentprojects\agentx"
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$PromptFile,

    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [Parameter(Mandatory = $false)]
    [ValidateSet("plan", "acceptEdits", "bypassPermissions", "default", "dontAsk", "auto")]
    [string]$PermissionMode = "plan"
)

# === 关键：让 PowerShell 5.1 写管道到外部进程时用 UTF-8 编码 ===
# 默认 [Console]::OutputEncoding = 系统 ANSI 代码页（中文 Windows = GBK）。
# `$str | external.exe` 会用该编码把 .NET String 重编码成字节写 stdin，
# Node.js claude.exe 收到 GBK 字节却按 UTF-8 解码 → 中文变 "?"。
# 同时设置 InputEncoding 防止反向污染；OutputEncoding 是核心修复点。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location $ProjectRoot

Write-Host ""
Write-Host "==== AgentX 执行轨迹复盘 ====" -ForegroundColor Cyan
Write-Host "项目根目录:    $ProjectRoot" -ForegroundColor DarkGray
Write-Host "Prompt 文件:   $PromptFile" -ForegroundColor DarkGray
Write-Host "权限模式:      $PermissionMode" -ForegroundColor DarkGray
Write-Host "工作模式:      claude -p (headless, 非交互)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "提示: 关闭此窗口即可结束复盘。" -ForegroundColor Yellow
Write-Host "提示: 如需允许写入，修改脚本 -PermissionMode 参数为 acceptEdits 后重跑。" -ForegroundColor Yellow
Write-Host ""
Write-Host "---- Claude 复盘报告 ----" -ForegroundColor Green
Write-Host ""

$prompt = Get-Content -Raw -Encoding UTF8 $PromptFile
$prompt | claude -p --model sonnet --permission-mode $PermissionMode --max-turns 25

Write-Host ""
Write-Host "---- 复盘结束 ----" -ForegroundColor Green
Write-Host "关闭窗口或按 Enter 退出..." -ForegroundColor DarkGray
$null = Read-Host