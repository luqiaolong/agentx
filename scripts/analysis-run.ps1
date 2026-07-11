<#
.SYNOPSIS
    AgentX 执行轨迹复盘 — 在 PowerShell 中启动 Claude CLI 交互模式。

.DESCRIPTION
    由 Tauri analysis_launch_powershell 命令调用。
    读取后端导出的 prompt 文件，通过 stdin 管道传给 claude CLI。
    Claude 处理完初始 prompt 后进入交互式会话，用户可直接对话追问或执行优化。

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

Set-Location $ProjectRoot

Write-Host ""
Write-Host "==== AgentX 执行轨迹复盘 ====" -ForegroundColor Cyan
Write-Host "项目根目录: $ProjectRoot" -ForegroundColor DarkGray
Write-Host "Prompt 文件: $PromptFile" -ForegroundColor DarkGray
Write-Host ""
Write-Host "提示: 复盘完成后如需执行优化，输入 /permission-mode acceptEdits" -ForegroundColor Yellow
Write-Host "提示: 输入 /quit 退出 Claude CLI" -ForegroundColor Yellow
Write-Host ""

$prompt = Get-Content -Raw $PromptFile
$prompt | claude --model sonnet --permission-mode plan --max-turns 25
