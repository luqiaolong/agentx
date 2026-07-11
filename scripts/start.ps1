# AgentX 启动脚本（Windows PowerShell）
#
# 用法：
#   pwsh scripts/start.ps1              # 启动（默认前台运行，会话结束随终端退出）
#   pwsh scripts/start.ps1 -NoWait      # 后台启动，立即返回
#   pwsh scripts/start.ps1 -Clean       # 启动前先调用 stop.ps1 清理残留
#   pwsh scripts/start.ps1 -SkipHealth  # 跳过健康探测（开发时偶尔想立刻返回）
#
# 设计要点：
#   - 启动入口永远是 `pnpm tauri dev`（不是 uv run python -m app.main）：
#     后端 AGENTX_* 凭证 + 配置由 Rust 主进程通过 src-tauri/src/backend/env.rs::build_env 注入，
#     直接起 uvicorn 会缺 key / Milvus 密码 / tools config。
#   - dev 是长进程，前台运行；后台模式用 -NoWait 立即返回。
#   - 启动后默认等待 uvicorn 起来（8123）再返回退出码 0，避免"以为起来了其实没有"。
#   - 完整 SOP 见 docs/agents/04-restart-sop.md

[CmdletBinding()]
param(
    [switch]$NoWait,          # 后台模式：立即返回 0，不等待 dev 就绪
    [switch]$Clean,           # 启动前自动清理旧进程
    [switch]$SkipHealth,      # 跳过健康探测
    [int]$HealthTimeoutSec = 90  # 健康探测总超时（首次 Rust 编译可能 1-3 分钟）
)

$ErrorActionPreference = 'Stop'

# === 路径与常量 ============================================================
$ROOT = Resolve-Path "$PSScriptRoot/.."
$STOP_SCRIPT = Join-Path $PSScriptRoot 'stop.ps1'
$HEALTH_SCRIPT = Join-Path $PSScriptRoot 'health-check.ps1'

$BACKEND_PORT = 8123
$FRONTEND_PORTS = @(5173, 5174)
$TAURI_READY_MARKER = 'AgentX Tauri shell started'

# === 工具函数 ===============================================================
function Test-PortListen {
    param([int]$Port)
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return [bool]$conn
}

function Wait-BackendReady {
    param([int]$TimeoutSec)
    Write-Host "  waiting for backend on 127.0.0.1:$BACKEND_PORT (timeout ${TimeoutSec}s)..." -ForegroundColor Cyan
    $start = Get-Date
    $lastLog = $start
    while (((Get-Date) - $start).TotalSeconds -lt $TimeoutSec) {
        if (Test-PortListen -Port $BACKEND_PORT) {
            $elapsed = [int]((Get-Date) - $start).TotalSeconds
            Write-Host "  backend port $BACKEND_PORT LISTEN after ${elapsed}s" -ForegroundColor Green
            return $true
        }
        # 每 10s 输出一次等待进度
        if (((Get-Date) - $lastLog).TotalSeconds -ge 10) {
            $elapsed = [int]((Get-Date) - $start).TotalSeconds
            Write-Host "    ...still waiting (${elapsed}s elapsed)"
            $lastLog = Get-Date
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Wait-FrontendReady {
    Write-Host "  waiting for frontend on Vite (5173/5174)..." -ForegroundColor Cyan
    $start = Get-Date
    while (((Get-Date) - $start).TotalSeconds -lt 30) {
        foreach ($p in $FRONTEND_PORTS) {
            if (Test-PortListen -Port $p) {
                $elapsed = [int]((Get-Date) - $start).TotalSeconds
                Write-Host "  frontend port $p LISTEN after ${elapsed}s" -ForegroundColor Green
                return $true
            }
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "  frontend port not detected in 30s (may still be compiling)" -ForegroundColor Yellow
    return $false
}

# === 前置检查 ===============================================================
Write-Host '===========================================' -ForegroundColor Cyan
Write-Host '  AgentX START' -ForegroundColor Cyan
Write-Host '===========================================' -ForegroundColor Cyan

# 1) 工作目录必须在项目根（避免在 frontend/renderer 子目录启动导致 Vite @/ 路径解析失败）
Write-Host "`n[1/4] Pre-flight checks..." -ForegroundColor Cyan
if (-not (Test-Path (Join-Path $ROOT 'package.json'))) {
    Write-Host "  ERROR: package.json not found at $ROOT" -ForegroundColor Red
    Write-Host "  Hint: this script must run from scripts/ directory of the project" -ForegroundColor Red
    exit 2
}
Write-Host "  project root: $ROOT" -ForegroundColor Green

# 2) 端口预检：若 8123 / 5173 已被 AgentX 占用，自动清理或报错
$busy = @()
foreach ($p in @($BACKEND_PORT) + $FRONTEND_PORTS) {
    if (Test-PortListen -Port $p) { $busy += $p }
}
if ($busy) {
    if ($Clean) {
        Write-Host "  ports busy ($($busy -join ',')); -Clean specified, calling stop.ps1..." -ForegroundColor Yellow
        & pwsh -NoProfile -File $STOP_SCRIPT
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  stop.ps1 returned $LASTEXITCODE, aborting start" -ForegroundColor Red
            exit 3
        }
    } else {
        Write-Host "  ports busy: $($busy -join ',')" -ForegroundColor Yellow
        Write-Host "  if these are leftover AgentX processes, re-run with -Clean" -ForegroundColor Yellow
        Write-Host "  if these belong to another app, free them manually first" -ForegroundColor Yellow
        exit 3
    }
}

# 3) Node / pnpm / Cargo 健康检查（避免静默失败）
Write-Host "`n[2/4] Toolchain check..." -ForegroundColor Cyan
foreach ($cmd in @('pnpm', 'node', 'cargo')) {
    $path = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($path) {
        Write-Host ("  {0,-8} ok ({1})" -f $cmd, $path.Source) -ForegroundColor Green
    } else {
        Write-Host ("  {0,-8} NOT FOUND" -f $cmd) -ForegroundColor Red
        if ($cmd -eq 'pnpm') {
            Write-Host "    install: npm install -g pnpm" -ForegroundColor Yellow
        } elseif ($cmd -eq 'cargo') {
            Write-Host "    install Rust from https://rustup.rs/" -ForegroundColor Yellow
        }
        exit 4
    }
}

# === 启动 ================================================================
Write-Host "`n[3/4] Launching pnpm tauri dev..." -ForegroundColor Cyan
Set-Location $ROOT

# 用 Start-Process 启动后台进程，把日志重定向到临时文件，便于后台模式读取
$LOG_DIR = Join-Path $ROOT 'data/logs'
if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null }
$LOG_FILE = Join-Path $LOG_DIR 'tauri-dev.log'
$LOG_ERR  = Join-Path $LOG_DIR 'tauri-dev.err'

if ($NoWait) {
    # 后台模式：立即返回，日志写到文件供后续查看
    Write-Host "  background mode (-NoWait), logs at: $LOG_FILE" -ForegroundColor Yellow
    # 用 cmd /c 包装 pnpm 调用，避免 Start-Process 找不到 pnpm.exe（PATH 解析问题）
    $proc = Start-Process -FilePath 'cmd.exe' `
        -ArgumentList '/c','pnpm','tauri','dev' `
        -WorkingDirectory $ROOT `
        -RedirectStandardOutput $LOG_FILE `
        -RedirectStandardError $LOG_ERR `
        -NoNewWindow -PassThru
    Write-Host "  pid: $($proc.Id)" -ForegroundColor Green
    Write-Host "  to follow logs: Get-Content '$LOG_FILE' -Wait" -ForegroundColor Cyan
    Write-Host "  to stop:        pwsh scripts/stop.ps1" -ForegroundColor Cyan
    exit 0
}

# 前台模式：等进程退出（Ctrl+C 中断）
Write-Host "  starting in foreground (Ctrl+C to abort)..." -ForegroundColor Yellow
Write-Host "  ----------------------------------------" -ForegroundColor DarkGray
try {
    if ($SkipHealth) {
        # 直接同步运行，退出码透传
        & pnpm tauri dev
        exit $LASTEXITCODE
    }

    # 启动 pnpm tauri dev 后，等待 backend listen
    # 用 cmd /c 包装避免 Start-Process 找不到 pnpm.exe
    $devProc = Start-Process -FilePath 'cmd.exe' `
        -ArgumentList '/c','pnpm','tauri','dev' `
        -WorkingDirectory $ROOT `
        -RedirectStandardOutput $LOG_FILE `
        -RedirectStandardError $LOG_ERR `
        -NoNewWindow -PassThru

    $backendOk = Wait-BackendReady -TimeoutSec $HealthTimeoutSec
    $frontendOk = Wait-FrontendReady

    Write-Host "`n[4/4] Health summary:" -ForegroundColor Cyan
    if ($backendOk) {
        Write-Host "  backend  127.0.0.1:$BACKEND_PORT  OK" -ForegroundColor Green
        # 用轻量端点验证真的能响应（/api/health 是存活探针的兜底选择，详见 §14.7.5）
        try {
            $resp = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:$BACKEND_PORT/api/health" -TimeoutSec 5
            Write-Host "  /api/health  responded" -ForegroundColor Green
        } catch {
            Write-Host "  /api/health  not responding yet (Tauri window may still be loading)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  backend  127.0.0.1:$BACKEND_PORT  TIMEOUT (after ${HealthTimeoutSec}s)" -ForegroundColor Red
        Write-Host "  hint: tail $LOG_FILE for details" -ForegroundColor Yellow
    }

    if ($frontendOk) {
        Write-Host "  frontend Vite                OK" -ForegroundColor Green
    } else {
        Write-Host "  frontend Vite                NOT READY" -ForegroundColor Yellow
    }

    Write-Host "`n  ----------------------------------------" -ForegroundColor DarkGray
    Write-Host "  AgentX dev session is running in this terminal." -ForegroundColor Cyan
    Write-Host "  Press Ctrl+C to stop. Or in another shell:" -ForegroundColor Cyan
    Write-Host "    pwsh scripts/stop.ps1    # graceful stop" -ForegroundColor Cyan
    Write-Host "    pwsh scripts/restart.ps1 # stop + start" -ForegroundColor Cyan
    Write-Host "  ----------------------------------------" -ForegroundColor DarkGray

    # 阻塞等待 dev 进程退出（Ctrl+C 中断）
    $devProc.WaitForExit()
    exit $devProc.ExitCode
} catch {
    Write-Host "  start failed: $_" -ForegroundColor Red
    exit 5
}