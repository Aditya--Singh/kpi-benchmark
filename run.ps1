# Starts the web app on Windows. Stop it with Ctrl+C or by closing the window.
# Easiest: double-click run.bat.
$ErrorActionPreference = 'Continue'
Set-Location -Path $PSScriptRoot
$env:PYTHONUTF8 = '1'
$Py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { Write-Host 'Run setup.bat first.'; exit 1 }

$port = 8077
if (Test-Path '.env') {
    foreach ($line in Get-Content '.env') {
        if ($line -match '^\s*PORT\s*=\s*(\d+)') { $port = [int]$Matches[1] }
    }
}

function Test-PortFree([int]$p) {
    try {
        $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $p)
        $l.Start(); $l.Stop(); return $true
    } catch { return $false }
}

if (-not (Test-PortFree $port)) {
    Write-Host "Port $port is already used by another application on this machine (not this app)."
    $new = $null
    foreach ($p in ($port + 1)..($port + 99)) { if (Test-PortFree $p) { $new = $p; break } }
    if (-not $new) { Write-Host "No free port found near $port. Set a free PORT= in .env and try again."; exit 1 }
    Write-Host "Using port $new instead. To make this permanent, set  PORT=$new  in the .env file."
    $port = $new
}
$env:PORT = "$port"

$ip = $null
try {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
           Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
           Select-Object -First 1).IPAddress
} catch { }
if (-not $ip) { $ip = 'localhost' }
Write-Host ''
Write-Host 'PNB Peer Benchmarking is starting.'
Write-Host "Open  http://${ip}:$port   (on this machine: http://localhost:$port)"
Write-Host 'Stop it with Ctrl+C or by closing this window.'
Write-Host ''
& $Py -m app.main
