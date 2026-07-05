$ErrorActionPreference = "Continue"

Write-Host "=== Stop Explorer ==="
Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

Write-Host "=== Clear icon cache ==="
$localAppData = $env:LOCALAPPDATA
$iconCache = Join-Path $localAppData "IconCache.db"
if (Test-Path $iconCache) {
    Remove-Item -Path $iconCache -Force
    Write-Host "  Removed IconCache.db"
}

$explorerDir = Join-Path $localAppData "Microsoft\Windows\Explorer"
if (Test-Path $explorerDir) {
    Get-ChildItem -Path $explorerDir -Filter "iconcache*" -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            Remove-Item -Path $_.FullName -Force -ErrorAction Stop
            Write-Host "  Removed $($_.Name)"
        } catch {
            Write-Host "  Could not remove $($_.Name)"
        }
    }
    Get-ChildItem -Path $explorerDir -Filter "thumbcache*" -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            Remove-Item -Path $_.FullName -Force -ErrorAction Stop
            Write-Host "  Removed $($_.Name)"
        } catch {
            Write-Host "  Could not remove $($_.Name)"
        }
    }
}

Write-Host "=== Clear shell cache ==="
$shellCache = Join-Path $localAppData "Microsoft\Windows\Shell\IconCacheToDelete"
if (Test-Path $shellCache) {
    Remove-Item -Path $shellCache -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "=== Restart Explorer ==="
Start-Process explorer.exe
Start-Sleep -Seconds 3
Write-Host "Done"
