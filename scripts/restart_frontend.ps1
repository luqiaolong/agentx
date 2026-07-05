$ErrorActionPreference = 'Continue'

function Kill-Family {
    param([string[]]$Names)
    $cnt = 0
    while ($true) {
        $pids = Get-Process -Name $Names -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id
        if (-not $pids) { break }
        $cnt++
        if ($cnt -gt 6) {
            Write-Host "  ABORT after 6 rounds for [$($Names -join ',')]"
            break
        }
        foreach ($id in $pids) {
            $name = (Get-Process -Id $id -ErrorAction SilentlyContinue).ProcessName
            Write-Host ("  round {0} stop {1} PID={2}" -f $cnt, $name, $id)
            Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 2
    }
}

Write-Host '[1/3] Killing electron + node-watcher family...'
Kill-Family -Names @('electron','node')

Write-Host '[2/3] Killing python/uv family...'
Kill-Family -Names @('python','uv')

Start-Sleep -Seconds 3

Write-Host '[3/3] Status:'
$left = Get-Process -Name electron,python,uv,node -ErrorAction SilentlyContinue
if ($left) {
    Write-Host '  STILL ALIVE:'
    $left | Format-Table ProcessName,Id,StartTime -AutoSize | Out-String | Write-Host
} else {
    Write-Host '  ALL CLEAR'
}

$listen = netstat -ano | Select-String ':8123 .*LISTENING'
if ($listen) {
    Write-Host '  PORT 8123 LISTENING:'
    $listen | ForEach-Object { Write-Host ("    {0}" -f $_) }
} else {
    Write-Host '  PORT 8123 FREE'
}
