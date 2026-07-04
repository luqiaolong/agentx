# 显式为我们的 AppUserModelID 在注册表中注册图标
# 这样 Windows 任务栏会从我们指定的 ico 文件读取图标，而不是从 exe 资源
$ErrorActionPreference = "Stop"

$appId = "com.agentpy.desktop"
$icoPath = Join-Path $PSScriptRoot "..\build\icon.ico"
$icoPath = (Resolve-Path $icoPath).Path

Write-Host "[register-appuser] 注册 AppUserModelID: $appId"
Write-Host "  图标路径: $icoPath"

# 创建 HKCU\Software\Classes\AppUserModelId\<appId>
$keyPath = "HKCU:\Software\Classes\AppUserModelId\$appId"
if (-not (Test-Path $keyPath)) {
    New-Item -Path $keyPath -Force | Out-Null
}

# 写入 DisplayName
Set-ItemProperty -Path $keyPath -Name "DisplayName" -Value "AgentPy" -Type String

# 写入 IconUrl / IconBackgroundColor 触发 Windows 重新读取图标
# 在 HKCU\Software\Classes\AppUserModelId\<appId>\ 下创建 Icon 子键
$iconKey = "$keyPath\Icon"
if (Test-Path $iconKey) {
    Remove-Item -Path $iconKey -Recurse -Force
}
New-Item -Path $iconKey -Force | Out-Null

# 通过 DefaultIcon 子键指向我们的 .ico 文件
$defaultIcon = "$keyPath\DefaultIcon"
if (Test-Path $defaultIcon) {
    Remove-Item -Path $defaultIcon -Recurse -Force
}
New-Item -Path $defaultIcon -Force | Out-Null
Set-ItemProperty -Path $defaultIcon -Name "(default)" -Value "$icoPath,0" -Type String

# 让 Windows 重新构建图标缓存
# 方法：清除 IconCache 数据库
$userProfile = $env:USERPROFILE
if (-not $userProfile) {
    $userProfile = "C:\Users\luqia"
}
$localAppData = Join-Path $userProfile "AppData\Local"
$iconCache = Join-Path $localAppData "IconCache.db"
if (Test-Path $iconCache) {
    Remove-Item -Path $iconCache -Force -ErrorAction SilentlyContinue
    Write-Host "  已清除 IconCache.db"
}
$iconCacheDir = Join-Path $localAppData "Microsoft\Windows\Explorer"
if (Test-Path $iconCacheDir) {
    Get-ChildItem -Path $iconCacheDir -Filter "iconcache*" -ErrorAction SilentlyContinue | ForEach-Object {
        Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
        Write-Host "  已清除 $($_.Name)"
    }
}

Write-Host "[register-appuser] 完成 ✓"
Write-Host "  现在请重启 Windows 资源管理器（任务管理器 → Windows 资源管理器 → 重新启动）"
