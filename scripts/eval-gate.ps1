<#
.SYNOPSIS
    AgentX 发版门禁脚本：运行 smoke 评测集，失败则禁止发版。

.DESCRIPTION
    调用 `agentx eval run --suite smoke --format=console,md` 运行冒烟评测集。
    - 退出码 0：评测门禁通过
    - 退出码 1：评测门禁失败（有 case 失败），禁止发版
    - 退出码 2：suite 不存在或执行错误

    默认 mock 模式（离线，无需 API key），30 秒内完成。

.PARAMETER Suite
    要运行的 suite 名称，默认 smoke。

.PARAMETER Format
    输出格式，默认 console,md。

.EXAMPLE
    .\scripts\eval-gate.ps1
    .\scripts\eval-gate.ps1 -Suite router
    .\scripts\eval-gate.ps1 -Format json
#>

param(
    [string]$Suite = "smoke",
    [string]$Format = "console,md"
)

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  AgentX 评测门禁 — Suite: $Suite" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 切换到项目根目录（scripts/ 的父目录）
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# 运行评测
Write-Host "[1/2] 运行评测: agentx eval run --suite=$Suite --format=$Format" -ForegroundColor Yellow
$startTime = Get-Date

& uv run agentx eval run --suite=$Suite --format=$Format
$exitCode = $LASTEXITCODE

$duration = (Get-Date) - $startTime
Write-Host ""
Write-Host "评测耗时: $($duration.TotalSeconds.ToString('F1'))s" -ForegroundColor Gray

# 判定结果
Write-Host ""
Write-Host "[2/2] 判定结果" -ForegroundColor Yellow

if ($exitCode -eq 0) {
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  评测门禁通过" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    exit 0
} else {
    Write-Host "========================================" -ForegroundColor Red
    Write-Host "  评测门禁失败（退出码 $exitCode），禁止发版" -ForegroundColor Red
    Write-Host "========================================" -ForegroundColor Red
    exit $exitCode
}
