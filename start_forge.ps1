# Forge - start the two background programs Blender's panels talk to.
#
#   * the shape service   (port 8765) - builds the actual geometry
#   * the assistant       (port 8901) - the chat box in Blender's sidebar
#
# Each is started only if its port is free, so running this twice is harmless
# and it will never trample a service you already had running.  Both run hidden;
# stop_forge.ps1 (or stop_forge.cmd) shuts them down again.

$ErrorActionPreference = 'Stop'

$root          = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython    = Join-Path $root 'service\.venv\Scripts\python.exe'
$venvPythonW   = Join-Path $root 'service\.venv\Scripts\pythonw.exe'
$bridge        = Join-Path $root 'assistant\bridge.py'
$servicePort   = 8765
$assistantPort = 8901

function Test-Port([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(400)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-Port([int]$Port, [int]$Seconds = 25) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        if (Test-Port $Port) { return $true }
        Start-Sleep -Milliseconds 800
    }
    return (Test-Port $Port)
}

Write-Host ''
Write-Host '  Forge'
Write-Host '  -----'

if (-not (Test-Path $venvPython)) {
    Write-Host "  [X] The shape service is not installed yet." -ForegroundColor Red
    Write-Host "      Expected: $venvPython"
    Write-Host "      Create service\.venv and install service\pyproject.toml first."
    Write-Host ''
    exit 1
}
# pythonw runs with no console window at all; plain python is hidden instead.
$runner = if (Test-Path $venvPythonW) { $venvPythonW } else { $venvPython }

# --- the shape service -------------------------------------------------------
if (Test-Port $servicePort) {
    Write-Host "  [ok] Shape service already running on port $servicePort"
} else {
    Start-Process -FilePath $runner -ArgumentList @('-m', 'service.main') `
        -WorkingDirectory $root -WindowStyle Hidden | Out-Null
    if (Wait-Port $servicePort) {
        Write-Host "  [ok] Shape service started on port $servicePort"
    } else {
        Write-Host "  [X] The shape service did not start." -ForegroundColor Red
        Write-Host "      To see why, run:  `"$venvPython`" -m service.main"
    }
}

# --- the assistant bridge ----------------------------------------------------
if (Test-Port $assistantPort) {
    Write-Host "  [ok] Assistant already running on port $assistantPort"
} elseif (-not (Test-Path $bridge)) {
    Write-Host "  [X] assistant\bridge.py is missing." -ForegroundColor Red
} else {
    Start-Process -FilePath $runner -ArgumentList @($bridge) `
        -WorkingDirectory $root -WindowStyle Hidden | Out-Null
    if (Wait-Port $assistantPort) {
        Write-Host "  [ok] Assistant started on port $assistantPort"
    } else {
        Write-Host "  [X] The assistant did not start." -ForegroundColor Red
        Write-Host "      To see why, run:  `"$venvPython`" `"$bridge`""
    }
}

Write-Host ''
Write-Host '  Forge is running - open Blender.' -ForegroundColor Green
Write-Host '  Press N in the 3D view, click the Forge tab, and type in the Assistant box.'
Write-Host ''
Start-Sleep -Seconds 4
exit 0
