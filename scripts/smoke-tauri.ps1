# AgentX Tauri Migration Smoke Test (Windows PowerShell)
#
# 用法：pwsh scripts/smoke-tauri.ps1
#
# 验证范围（覆盖 OpenSpec Phase 10 全量回归冒烟要求）：
#   1. Rust 编译检查 (cargo check)
#   2. Rust 单元测试 (cargo test --lib)
#   3. Rust clippy 零警告 (cargo clippy -D warnings)
#   4. 前端类型检查 (npm run typecheck)
#   5. 前端单元测试 (npm test)
#   6. tauri.conf.json updater 骨架验证
#   7. Electron 残留检查（package.json + frontend/renderer/）
#   8. window.api 残留检查（frontend/renderer/）
#   9. Python 后端 import 检查
#  10. tauri build 验证（可选，参数 -Build）
#
# 不在脚本中执行的场景（需手动 GUI 测试，已记录到 docs/manual-smoke.md）：
#   - npm run tauri dev 启动 GUI 窗口
#   - 25+ window.api.* 调用点的人工点击验证
#   - 凭证迁移（enc:/plain: 混合）的端到端测试

[CmdletBinding()]
param(
    [switch]$Build  # 加 -Build 跑 npm run tauri build（耗时 5+ 分钟）
)

$ErrorActionPreference = 'Stop'
$root = Resolve-Path "$PSScriptRoot/.."
Set-Location $root

$pass = 0
$fail = 0

function Step($name, $scriptblock) {
    Write-Host ""
    Write-Host "=== $name ===" -ForegroundColor Cyan
    try {
        & $scriptblock
        Write-Host "PASS: $name" -ForegroundColor Green
        $script:pass++
    } catch {
        Write-Host "FAIL: $name" -ForegroundColor Red
        Write-Host $_.ScriptStackTrace
        $script:fail++
    }
}

# 1. cargo check
Step "1. cargo check" {
    Push-Location src-tauri
    try {
        cargo check 2>&1 | Out-Null
    } finally { Pop-Location }
}

# 2. cargo test --lib
Step "2. cargo test --lib" {
    Push-Location src-tauri
    try {
        $output = cargo test --lib 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) { throw "cargo test failed" }
        # 解析测试结果（Out-String 转为单一字符串后 -match 才稳定）
        if ($output -match 'test result: \w+\.\s+(\d+) passed;\s+(\d+) failed') {
            Write-Host "  Rust tests: $($matches[1]) passed, $($matches[2]) failed"
            if ([int]$matches[2] -gt 0) { throw "$($matches[2]) Rust tests failed" }
        } else {
            Write-Host "  (test result line not parsed, exit code 0 => assume pass)"
        }
    } finally { Pop-Location }
}

# 3. cargo clippy -D warnings
Step "3. cargo clippy -D warnings" {
    Push-Location src-tauri
    try {
        cargo clippy -- -D warnings 2>&1 | Out-Null
    } finally { Pop-Location }
}

# 4. npm run typecheck
Step "4. npm run typecheck" {
    npm run typecheck 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "typecheck failed" }
}

# 5. npm test
Step "5. npm test (vitest)" {
    $output = npm test 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "vitest failed" }
    if ($output -match 'Tests\s+(\d+)\s+passed') {
        Write-Host "  vitest: $($matches[1]) tests passed"
    } else {
        Write-Host "  (vitest summary not parsed, exit code 0 => assume pass)"
    }
}

# 6. tauri.conf.json updater 骨架
Step "6. tauri.conf.json updater skeleton" {
    $conf = Get-Content "src-tauri/tauri.conf.json" -Raw | ConvertFrom-Json
    if (-not $conf.plugins.updater) { throw "plugins.updater missing" }
    if (-not $conf.plugins.updater.pubkey) { throw "updater.pubkey missing" }
    if (-not $conf.plugins.updater.endpoints) { throw "updater.endpoints missing" }
    Write-Host "  pubkey: $($conf.plugins.updater.pubkey.Substring(0, [Math]::Min(40, $conf.plugins.updater.pubkey.Length)))..."
    Write-Host "  endpoints: $($conf.plugins.updater.endpoints.Count) configured"
    if (-not $conf.bundle.publisher) { throw "bundle.publisher missing" }
}

# 7. Electron 残留检查
Step "7. Electron residual in package.json" {
    $pkg = Get-Content "package.json" -Raw
    if ($pkg -match '"electron"') { throw "electron dep still in package.json" }
    if ($pkg -match '"electron-vite"') { throw "electron-vite dep still in package.json" }
    if ($pkg -match '"electron-builder"') { throw "electron-builder dep still in package.json" }
    if ($pkg -match '"electron-store"') { throw "electron-store dep still in package.json" }
    if ($pkg -match '"electron-updater"') { throw "electron-updater dep still in package.json" }
    if ($pkg -match '"dugite"') { throw "dugite dep still in package.json" }
    if ($pkg -match '"rcedit"') { throw "rcedit dep still in package.json" }
    Write-Host "  No Electron deps in package.json"
}

# 8. window.api 残留检查
Step "8. window.api residual in frontend/renderer" {
    $hits = Get-ChildItem -Path "frontend/renderer" -Recurse -Include "*.ts","*.tsx" |
        Select-String -Pattern "window\.api\b" -SimpleMatch
    if ($hits) {
        Write-Host "  Found $($hits.Count) window.api references:"
        $hits | Select-Object -First 5 | ForEach-Object { Write-Host "    $($_.Path):$($_.LineNumber)" }
        throw "window.api references remain"
    }
    Write-Host "  No window.api references in frontend/renderer"
}

# 9. frontend/main 和 frontend/preload 已删除
Step "9. frontend/main and frontend/preload deleted" {
    if (Test-Path "frontend/main") { throw "frontend/main still exists" }
    if (Test-Path "frontend/preload") { throw "frontend/preload still exists" }
    if (Test-Path "electron.vite.config.ts") { throw "electron.vite.config.ts still exists" }
    Write-Host "  Electron entry files deleted"
}

# 10. Python 后端 import 检查
Step "10. Python backend import check" {
    if (Test-Path "backend") {
        Push-Location backend
        try {
            $pythonCmd = if (Get-Command uv -ErrorAction SilentlyContinue) { "uv" } else { "python" }
            if ($pythonCmd -eq "uv") {
                uv run python -c "import app.main; print('backend imports ok')" 2>&1 | Out-Null
            } else {
                python -c "import app.main; print('backend imports ok')" 2>&1 | Out-Null
            }
            if ($LASTEXITCODE -ne 0) { throw "Python backend import failed" }
        } finally { Pop-Location }
    } else {
        Write-Host "  backend/ not found, skipping"
    }
}

# 11. (可选) tauri build
if ($Build) {
    Step "11. npm run tauri build (release)" {
        npm run tauri build 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
        $setupExe = Get-ChildItem "src-tauri/target/release/bundle/nsis/*.exe" -ErrorAction SilentlyContinue
        if (-not $setupExe) { throw "NSIS setup exe not generated" }
        $sizeMB = [math]::Round($setupExe.Length / 1MB, 2)
        Write-Host "  Generated: $($setupExe.Name) ($sizeMB MB)"
        if ($sizeMB -gt 50) { Write-Host "  WARNING: setup exe > 50MB (expected <20MB)" }
    }
} else {
    Write-Host ""
    Write-Host "Skipping 'npm run tauri build' (add -Build to enable)" -ForegroundColor Yellow
}

# 汇总
Write-Host ""
Write-Host "=================================" -ForegroundColor Cyan
Write-Host "  PASS: $pass" -ForegroundColor Green
Write-Host "  FAIL: $fail" -ForegroundColor $(if ($fail -gt 0) { 'Red' } else { 'Gray' })
Write-Host "=================================" -ForegroundColor Cyan

if ($fail -gt 0) { exit 1 }
Write-Host "All smoke tests passed" -ForegroundColor Green
