# AgentX 健康探测脚本（Windows PowerShell）
#
# 用法：
#   pwsh scripts/health-check.ps1          # 探测所有端口 + 关键端点
#   pwsh scripts/health-check.ps1 -Wait 30 # 等 30s 再探测（用于异步启动场景）
#
# 设计要点（详见 docs/agents/04-restart-sop.md §14.7.5）：
#   - /api/health 不是存活探针（永远 200 兜底，且串行调 TEI/Milvus 耗时 5s+）
#   - 进程存活检测用 GET / 或 GET /api/skills（轻量、零外部依赖）
#   - 端口探测用 PowerShell 原生 Get-NetTCPConnection，不用 netstat

[CmdletBinding()]
param(
    [int]$WaitSeconds = 0,    # 启动后等待秒数再探测
    [int]$BackendPort = 8123,
    [int[]]$FrontendPorts = @(5173, 5174)
)

$ErrorActionPreference = 'Continue'

if ($WaitSeconds -gt 0) {
    Write-Host "waiting ${WaitSeconds}s before health check..." -ForegroundColor Cyan
    Start-Sleep -Seconds $WaitSeconds
}

Write-Host '===========================================' -ForegroundColor Cyan
Write-Host '  AgentX HEALTH CHECK' -ForegroundColor Cyan
Write-Host '===========================================' -ForegroundColor Cyan

$ok = $true

# 1) 端口探测
Write-Host "`n[1/3] Port listen check:" -ForegroundColor Cyan

# Backend 端口必须 LISTEN，否则直接 FAIL
$backendConn = Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue
if ($backendConn) {
    $ownerPid = ($backendConn | Select-Object -ExpandProperty OwningProcess -Unique) -join ','
    Write-Host ("  {0,-6} :{1,4}  LISTEN  (pid={2})" -f 'backend', $BackendPort, $ownerPid) -ForegroundColor Green
} else {
    Write-Host ("  {0,-6} :{1,4}  DOWN" -f 'backend', $BackendPort) -ForegroundColor Red
    $ok = $false
}

# Frontend：任何一个备用端口 LISTEN 算 OK（Vite 端口被占用时自动递增）
$frontendOk = $false
$frontendPort = $null
foreach ($p in $FrontendPorts) {
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($c) {
        $frontendOk = $true
        $frontendPort = $p
        $ownerPid = ($c | Select-Object -ExpandProperty OwningProcess -Unique) -join ','
        Write-Host ("  {0,-6} :{1,4}  LISTEN  (pid={2})" -f 'frontend', $p, $ownerPid) -ForegroundColor Green
        break
    }
}
if (-not $frontendOk) {
    Write-Host ("  {0,-6} :{1}-{2}  DOWN (Vite 还没起来或编译中)" -f 'frontend', $FrontendPorts[0], $FrontendPorts[-1]) -ForegroundColor Yellow
    # frontend DOWN 视为 WARN，不设 $ok=false（Vite 首次启动需要 Rust 编译时间）
}

# 2) 后端 HTTP 探测（用轻量端点）
Write-Host "`n[2/3] Backend HTTP probe:" -ForegroundColor Cyan
$endpoints = @(
    @{ Name = 'GET /             (zero-dep)';     Url = "http://127.0.0.1:$BackendPort/" }
    @{ Name = 'GET /api/skills   (loader chain)'; Url = "http://127.0.0.1:$BackendPort/api/skills" }
    @{ Name = 'GET /api/health   (always 200)';   Url = "http://127.0.0.1:$BackendPort/api/health" }
)
foreach ($e in $endpoints) {
    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $resp = Invoke-RestMethod -Method GET -Uri $e.Url -TimeoutSec 5
        $sw.Stop()
        $ms = $sw.ElapsedMilliseconds
        Write-Host ("  {0,-40} OK  ({1}ms)" -f $e.Name, $ms) -ForegroundColor Green
    } catch {
        $msg = $_.Exception.Message
        if ($msg.Length -gt 80) { $msg = $msg.Substring(0, 80) + '...' }
        Write-Host ("  {0,-40} FAIL  ({1})" -f $e.Name, $msg) -ForegroundColor Red
        $ok = $false
    }
}

# 3) 进程家族探测（看是否有 AgentX 进程在跑）
Write-Host "`n[3/3] AgentX process family:" -ForegroundColor Cyan
$keywords = @('tauri dev', 'pnpm tauri', 'vite', 'uvicorn', 'app.main', 'backend.app', 'src-tauri', 'target\debug\agentx.exe', 'target\release\agentx.exe')
$excludeKeywords = @('.qoder', 'qoder', 'ide\plugins')

function Test-IsAgentXProcess($p) {
    if (-not $p) { return $false }
    # 项目二进制名兜底
    if ($p.ProcessName -in 'agentx', 'AgentX') { return $true }
    # 无 CommandLine 跳过
    $cl = ''
    try { $cl = $p.CommandLine } catch { return $false }
    if (-not $cl) { return $false }
    $clLower = $cl.ToLowerInvariant()
    # 黑名单优先
    foreach ($kw in $excludeKeywords) { if ($clLower.Contains($kw)) { return $false } }
    # 白名单
    foreach ($kw in $keywords) { if ($clLower.Contains($kw)) { return $true } }
    return $false
}

$candidates = @()
foreach ($p in (Get-Process -Name python,node,uv,agentx -ErrorAction SilentlyContinue)) {
    if (Test-IsAgentXProcess $p) { $candidates += $p }
}

if ($candidates.Count -gt 0) {
    $candidates | Sort-Object ProcessName, Id | Format-Table Id, ProcessName, StartTime -AutoSize | Out-String | Write-Host
} else {
    Write-Host "  no AgentX process found" -ForegroundColor Yellow
    $ok = $false
}

Write-Host "`n===========================================" -ForegroundColor Cyan
if ($ok) {
    Write-Host "  HEALTH: OK" -ForegroundColor Green
    Write-Host "===========================================" -ForegroundColor Cyan
    exit 0
} else {
    Write-Host "  HEALTH: DEGRADED" -ForegroundColor Yellow
    Write-Host "===========================================" -ForegroundColor Cyan
    exit 1
}