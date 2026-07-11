# AgentX 重启脚本（Windows PowerShell）
#
# 用法：
#   pwsh scripts/restart.ps1            # stop + start（前台阻塞）
#   pwsh scripts/restart.ps1 -NoWait    # stop + 后台 start
#   pwsh scripts/restart.ps1 -Force     # 跳过优雅等待，直接强杀
#
# 等价于：先 stop，再 start。所有参数透传给两个子脚本。

[CmdletBinding()]
param(
    [switch]$NoWait,
    [switch]$Force,
    [switch]$SkipHealth,
    [int]$HealthTimeoutSec = 90
)

$ErrorActionPreference = 'Stop'

$STOP_SCRIPT = Join-Path $PSScriptRoot 'stop.ps1'
$START_SCRIPT = Join-Path $PSScriptRoot 'start.ps1'

Write-Host '===========================================' -ForegroundColor Cyan
Write-Host '  AgentX RESTART' -ForegroundColor Cyan
Write-Host '===========================================' -ForegroundColor Cyan

# 1) 停
Write-Host "`n[1/2] Stopping..." -ForegroundColor Cyan
$stopArgs = @{}
if ($Force) { $stopArgs['Force'] = $true }
& pwsh -NoProfile -File $STOP_SCRIPT @stopArgs
$stopCode = $LASTEXITCODE
if ($stopCode -ne 0) {
    Write-Host "  stop.ps1 returned $stopCode, aborting restart" -ForegroundColor Red
    exit $stopCode
}

# 2) 起
Write-Host "`n[2/2] Starting..." -ForegroundColor Cyan
$startArgs = @{}
if ($NoWait) { $startArgs['NoWait'] = $true }
if ($SkipHealth) { $startArgs['SkipHealth'] = $true }
if ($HealthTimeoutSec) { $startArgs['HealthTimeoutSec'] = $HealthTimeoutSec }
& pwsh -NoProfile -File $START_SCRIPT @startArgs
exit $LASTEXITCODE