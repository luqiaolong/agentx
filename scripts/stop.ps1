# AgentX 停止脚本（Windows PowerShell）
#
# 用法：
#   pwsh scripts/stop.ps1                # 停止 AgentX（Tauri + Vite + Python）
#   pwsh scripts/stop.ps1 -Force         # 强制模式：不等进程优雅退出
#
# 设计原则（详见 docs/agents/04-restart-sop.md §14.7.8）：
#   - 严格按 CommandLine 精准筛选，禁止 `Get-Process -Name python | Stop-Process -Force`
#     （会误杀同机的其他项目如 Hermes）
#   - 多轮 Stop-Process 直至干净（最多 6 轮，每轮间隔 2s）
#   - 只用 PowerShell 原生命令（Get-NetTCPConnection），禁止 taskkill / netstat
#   - 不重启：纯停止，若要重启请用 scripts/restart.ps1

[CmdletBinding()]
param(
    [switch]$Force,           # 跳过优雅等待，立即强杀
    [int]$WaitSeconds = 3     # 非 Force 模式下，等待子进程优雅退出的秒数
)

$ErrorActionPreference = 'Continue'

# === 路径与常量 ============================================================
$ROOT = Resolve-Path "$PSScriptRoot/.."
$PORTS = @(8123, 5173, 5174)  # 后端 + 前端默认 + 前端备用

# AgentX 进程白名单（CommandLine 中包含任一即视为 AgentX dev session）
#   关键：只用 *启动参数* 关键字，不用项目名 "agentx"（太宽，会误伤 Qoder IDE 的 python extension）
$PROJECT_KEYWORDS = @(
    'tauri dev'         # pnpm tauri dev / cargo tauri dev
    'pnpm tauri'        # pnpm 调 tauri
    'vite'              # Vite dev server
    'uvicorn'           # backend uvicorn 进程
    'app.main'          # backend entrypoint
    'backend.app'       # backend app package
    'src-tauri'         # rust build artifacts 路径
    'target\debug\agentx.exe'  # rust 编译产物
    'target\release\agentx.exe'
)

# AgentX 进程黑名单（即使白名单命中也排除，避免误杀 IDE / 其他并行项目的子进程）
$EXCLUDE_KEYWORDS = @(
    '.qoder'            # Qoder IDE extension
    'qoder'             # 兜底
    'ide\plugins'       # IDE 插件
)

# === 工具函数 ===============================================================
function Test-ProjectProcess {
    <#
    按 CommandLine 判断一个进程是否属于 AgentX dev session。
    策略：白名单（启动参数关键字）AND NOT 黑名单（IDE/其他项目进程）。
    无 CommandLine 或 ProcessName 命中 agentx 二进制名也算 AgentX。
    #>
    param([System.Diagnostics.Process]$Proc)

    if (-not $Proc) { return $false }

    # 项目二进制名兜底（rust 编译产物 agentx.exe，CommandLine 可能为空）
    $pn = $Proc.ProcessName
    if ($pn -eq 'agentx' -or $pn -eq 'AgentX') {
        # 但如果 CommandLine 命中黑名单，仍排除（防止 IDE 加载同名插件）
        return $true
    }

    $cl = ''
    try { $cl = $Proc.CommandLine } catch { $cl = '' }
    if (-not $cl) { return $false }

    $clLower = $cl.ToLowerInvariant()

    # 黑名单优先：IDE / 其他项目的子进程直接排除
    foreach ($kw in $EXCLUDE_KEYWORDS) {
        if ($clLower.Contains($kw)) { return $false }
    }

    # 白名单：必须命中明确的 AgentX dev 启动参数
    foreach ($kw in $PROJECT_KEYWORDS) {
        if ($clLower.Contains($kw)) { return $true }
    }

    return $false
}

function Get-AgentXProcesses {
    <#
    收集所有候选进程（python/node/uv + 项目二进制），由调用方再按 CommandLine 精准筛选。
    #>
    return Get-Process -Name python,node,uv,agentx -ErrorAction SilentlyContinue
}

function Stop-AgentXFamily {
    <#
    多轮清理 AgentX 进程家族，每轮 Sleep 2s 让子进程反应，最多重试 6 轮。
    #>
    [int]$maxRounds = 6
    [int]$round = 0

    while ($round -lt $maxRounds) {
        $round++
        $targets = Get-AgentXProcesses | Where-Object { Test-ProjectProcess $_ }

        if (-not $targets) {
            Write-Host ("  round {0}: ALL CLEAR" -f $round) -ForegroundColor Green
            return
        }

        Write-Host ("  round {0}: killing {1} process(es)..." -f $round, $targets.Count)
        foreach ($p in $targets) {
            Write-Host ("    stop {0,-12} PID={1,6}  cmd={2}" -f `
                $p.ProcessName, $p.Id, `
                ($(if ($p.CommandLine) { $p.CommandLine.Substring(0, [Math]::Min(80, $p.CommandLine.Length)) } else { '<n/a>' })))
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }

        if ($Force) { continue }
        Start-Sleep -Seconds 2
    }

    Write-Host ("  ABORT after {0} rounds, still alive:" -f $maxRounds) -ForegroundColor Yellow
    $left = Get-AgentXProcesses | Where-Object { Test-ProjectProcess $_ }
    if ($left) {
        $left | Format-Table Id, ProcessName, StartTime -AutoSize | Out-String | Write-Host
    }
}

function Test-PortInUse {
    <#
    检查指定端口是否有 LISTEN 状态的连接（排除 TimeWait）。
    返回占用端口的 OwningProcess 列表。
    #>
    param([int]$Port)
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
}

# === 主流程 ================================================================
Write-Host '===========================================' -ForegroundColor Cyan
Write-Host '  AgentX STOP' -ForegroundColor Cyan
Write-Host '===========================================' -ForegroundColor Cyan

# 1) 端口预扫描（让用户先看到占用情况）
Write-Host "`n[1/4] Port scan:" -ForegroundColor Cyan
$busyPorts = @()
foreach ($p in $PORTS) {
    $conn = Test-PortInUse -Port $p
    if ($conn) {
        $busyPorts += $p
        Write-Host ("  port {0,4}: LISTEN by PID {1}" -f $p, ($conn | Select-Object -ExpandProperty OwningProcess -Unique)) -ForegroundColor Yellow
    } else {
        Write-Host ("  port {0,4}: FREE" -f $p) -ForegroundColor Green
    }
}

if (-not $busyPorts) {
    Write-Host "`nNo AgentX ports in use, nothing to stop." -ForegroundColor Green
    exit 0
}

# 2) 优雅等待（如非 Force）
if (-not $Force -and $WaitSeconds -gt 0) {
    Write-Host "`n[2/4] Waiting ${WaitSeconds}s for graceful shutdown..." -ForegroundColor Cyan
    Start-Sleep -Seconds $WaitSeconds
} else {
    Write-Host "`n[2/4] Force mode: skipping graceful wait" -ForegroundColor Yellow
}

# 3) 按 CommandLine 精准清理三棵树
Write-Host "`n[3/4] Killing AgentX process family (precise by CommandLine)..." -ForegroundColor Cyan
Stop-AgentXFamily

# 4) 端口复检
Write-Host "`n[4/4] Port re-check:" -ForegroundColor Cyan
Start-Sleep -Seconds 1
$stillBusy = @()
foreach ($p in $PORTS) {
    $conn = Test-PortInUse -Port $p
    if ($conn) {
        $stillBusy += $p
        Write-Host ("  port {0,4}: STILL BUSY (pid={1}, may be TimeWait or non-AgentX)" -f $p, ($conn.OwningProcess -join ',')) -ForegroundColor Yellow
    } else {
        Write-Host ("  port {0,4}: FREE" -f $p) -ForegroundColor Green
    }
}

if ($stillBusy) {
    Write-Host "`nSome ports still busy. Hint: TimeWait may take 1-2 min, or another app owns the port." -ForegroundColor Yellow
    Write-Host "  Run again with -Force, or diagnose with:" -ForegroundColor Yellow
    Write-Host "    Get-NetTCPConnection -LocalPort 8123,5173 -State Listen | Select-Object LocalPort, OwningProcess, State" -ForegroundColor Yellow
    exit 1
}

Write-Host "`nAgentX stopped cleanly." -ForegroundColor Green
exit 0