# Forge - stop the two background programs start_forge started.
#
# It finds them by the port they are listening on and only stops a process that
# is actually a Python one, so a stray program that happens to hold the port is
# reported rather than killed.  Blender is never touched.

$ErrorActionPreference = 'Stop'

$targets = @(
    @{ Port = 8765; Name = 'Shape service' },
    @{ Port = 8901; Name = 'Assistant' }
)

function Get-PortOwners([int]$Port) {
    $pids = @()
    try {
        $pids += (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
                  Select-Object -ExpandProperty OwningProcess)
    } catch {
        # Older Windows, or the module is unavailable: fall back to netstat.
        foreach ($line in (netstat -ano -p TCP)) {
            if ($line -match "LISTENING" -and $line -match ":$Port\s") {
                $parts = ($line -split '\s+') | Where-Object { $_ }
                $pids += [int]$parts[-1]
            }
        }
    }
    return ($pids | Sort-Object -Unique)
}

Write-Host ''
Write-Host '  Forge'
Write-Host '  -----'

foreach ($target in $targets) {
    $owners = Get-PortOwners $target.Port
    if (-not $owners) {
        Write-Host ("  [ok] {0} was not running (port {1} is free)" -f $target.Name, $target.Port)
        continue
    }
    foreach ($owner in $owners) {
        $proc = $null
        try { $proc = Get-Process -Id $owner -ErrorAction Stop } catch { }
        if ($null -eq $proc) {
            Write-Host ("  [ok] {0} had already stopped" -f $target.Name)
            continue
        }
        if ($proc.ProcessName -notmatch '^python') {
            Write-Host ("  [!] Port {0} is held by {1} (pid {2}), which is not part of Forge - leaving it alone." `
                        -f $target.Port, $proc.ProcessName, $owner) -ForegroundColor Yellow
            continue
        }
        try {
            Stop-Process -Id $owner -ErrorAction Stop
            Write-Host ("  [ok] {0} stopped (pid {1})" -f $target.Name, $owner)
        } catch {
            Write-Host ("  [X] Could not stop {0} (pid {1}): {2}" -f $target.Name, $owner, $_.Exception.Message) -ForegroundColor Red
        }
    }
}

Write-Host ''
Write-Host '  Forge is stopped. Blender was not touched.'
Write-Host ''
Start-Sleep -Seconds 3
exit 0
