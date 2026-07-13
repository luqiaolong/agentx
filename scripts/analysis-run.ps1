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
# max-turns=120：复盘任务需 dispatch 4 个专家 subagent 并行分析（前端/架构/测试/可观测性），
# Claude CLI 的 subagent 机制下子代理所有工具调用 turn 都计入主代理 max-turns。
# 4 专家 × ~8-12 turns + 主代理汇总 ~20 turns ≈ 60-80，留 50% 余量到 120。
# 旧值 25 会导致后启动的专家被截断（"递归限制失败"）。
$prompt | claude -p --model sonnet --dangerously-skip-permissions --max-turns 120 |
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
[AgentX 复盘报告已生成 — 请立即打印给用户]

阶段 1 headless 复盘任务已完成，报告已落盘到：
- 复盘报告 Markdown: $ReportFile
- 原始 trace prompt: $PromptFile

请你按以下步骤立即行动（**不要等待用户提问**）：

1. 用 Read 工具读取 $ReportFile
2. **完整**地把报告内容以 Markdown 格式输出给用户：
   - **不要**摘要、不要省略任何章节
   - 必须包含全部 5 个章节：用户意图 / 执行路径 / 问题诊断 / 优化方案 / 优先级排序
   - 保留原始 Markdown 结构（标题层级、列表、代码块原样输出）
3. 报告输出完毕后追加一行：

   > 以上是本轮复盘报告全文（已落盘到 $ReportFile）。
   > 如需追问某个问题 / 执行某条优化方案 / 重新分析某方面，请直接告诉我。

如果用户首条消息是空内容或只是打招呼：仍按上述步骤先打印报告全文，再等待后续问题。
"@

claude --model sonnet --dangerously-skip-permissions "$initialPrompt"

Write-Host ""
Write-Host "---- 复盘会话已结束 ----" -ForegroundColor Green
Write-Host "关闭窗口退出..." -ForegroundColor DarkGray
$null = Read-Host