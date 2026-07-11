<#
.SYNOPSIS
    AgentX 执行轨迹复盘 — 在 PowerShell 中启动 Claude CLI 交互模式。

.DESCRIPTION
    由 Tauri analysis_launch_powershell 命令调用。

    两阶段执行：
    1. 阶段 1 (headless)：claude -p --dangerously-skip-permissions 跑完整个复盘任务，
       报告直接打印到当前终端，**同时通过 Tee-Object 落盘到 <PromptFile 同目录>/<runId>_<ts>_review.md**。
    2. 阶段 2 (interactive)：报告输出后 Read-Host 暂停，用户按 Enter 启动
       claude --dangerously-skip-permissions "<initial_prompt>" 创建**全新会话**（不带 -c/-r，
       不依赖"最近会话"语义，避免多实例并发串台），用 initial_prompt 携带上下文
       （报告文件路径 + 原始 trace 路径），LLM 可主动 Read 报告内容继续对话。

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

# 推导报告文件路径：与 PromptFile 同目录，文件名追加 _review.md
# 例：PromptFile = data\traces\<runId>_<ts>.md
#   → ReportFile = data\traces\<runId>_<ts>_review.md
$promptDir = Split-Path -Parent $PromptFile
$promptLeaf = Split-Path -Leaf $PromptFile
$promptBase = [System.IO.Path]::GetFileNameWithoutExtension($promptLeaf)
$ReportFile = Join-Path $promptDir ($promptBase + "_review.md")

Write-Host "复盘报告将保存到: $ReportFile" -ForegroundColor DarkGray
Write-Host ""

$prompt = Get-Content -Raw -Encoding UTF8 $PromptFile

# === 阶段 1: headless 复盘，stdout 用 Tee-Object 同时输出到终端 + 报告文件 ===
$prompt | claude -p --model sonnet --dangerously-skip-permissions --max-turns 25 |
    Tee-Object -FilePath $ReportFile | Out-Null

Write-Host ""
Write-Host "---- 复盘报告结束 ----" -ForegroundColor Green
Write-Host "报告已落盘: $ReportFile" -ForegroundColor DarkGray
Write-Host ""
Write-Host "阶段 2: 按 Enter 创建全新 Claude 交互会话并带入上下文；按 Ctrl+C 跳过。" -ForegroundColor Yellow
$null = Read-Host

Write-Host ""
Write-Host "---- Claude 交互模式 (阶段 2: 新会话 + Ink TUI) ----" -ForegroundColor Green
Write-Host "退出交互模式: 输入 /quit 或 Ctrl+C`n" -ForegroundColor DarkGray

# === 阶段 2: 创建全新交互会话（不带 -c/-r），用 initial_prompt 携带上下文 ===
# 不依赖"最近会话"语义，避免多 PowerShell 实例并发时串台。
# LLM 收到 initial_prompt 后可主动 Read 报告文件 + 原始 trace 文件继续对话。
$initialPrompt = @"
[AgentX 复盘后续对话模式]

你刚刚完成了一次 AgentX 执行轨迹复盘任务，相关文件：
- 上一轮复盘报告（Markdown 格式）：$ReportFile
- 原始执行轨迹 prompt 文件：$PromptFile

请先用 Read 工具阅读以上两份文件了解上一轮复盘的结论，然后等待用户继续提问。
用户接下来可能会：追问报告中的具体问题 / 要求执行某条优化方案 / 要求重新分析某个方面。

如果用户直接发送空消息或只是打招呼，简单确认你已加载报告即可，不要重复复盘内容。
"@

claude --model sonnet --dangerously-skip-permissions "$initialPrompt"

Write-Host ""
Write-Host "---- 复盘会话已结束 ----" -ForegroundColor Green
Write-Host "关闭窗口退出..." -ForegroundColor DarkGray
$null = Read-Host