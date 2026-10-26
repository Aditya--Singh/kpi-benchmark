# One-time setup on Windows 10/11 or Windows Server 2019+. Safe to run again.
# Easiest: double-click setup.bat (it runs this script with the right PowerShell options).
# Works in Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = 'Continue'   # native tools write progress to stderr; we check exit codes instead
Set-Location -Path $PSScriptRoot
$env:PYTHONUTF8 = '1'

function Say([string]$text) { Write-Host ''; Write-Host "== $text" -ForegroundColor Yellow }
function Get-EnvValue([string]$name, [string]$default) {
    if (Test-Path '.env') {
        foreach ($line in Get-Content '.env') {
            if ($line -match "^\s*$name\s*=\s*(.*)$") {
                $v = $Matches[1].Trim().Trim('"').Trim("'")
                if ($v) { return $v }
            }
        }
    }
    return $default
}

Say '1/5  Checking Python'
$PyExe = $null; $PyArgs = @()
foreach ($c in @('py -3', 'python', 'python3')) {
    $parts = $c.Split(' ')
    $exe = $parts[0]
    $extra = @()
    if ($parts.Count -gt 1) { $extra = @($parts[1..($parts.Count - 1)]) }
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $ver = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $ver) {
        try { if ([version]("$ver".Trim()) -ge [version]'3.10') { $PyExe = $exe; $PyArgs = $extra; break } } catch { }
    }
}
if (-not $PyExe) {
    Write-Host 'Python 3.10 or newer was not found.'
    Write-Host 'Install it from https://www.python.org/downloads/windows/ and tick "Add python.exe to PATH",'
    Write-Host 'then close this window and run setup.bat again.'
    exit 1
}
& $PyExe @PyArgs --version

Say '2/5  Creating the private Python environment (.venv) and installing libraries'
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    & $PyExe @PyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host 'Could not create the .venv folder.'; exit 1 }
}
$Py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $Py -m pip install --upgrade pip wheel | Out-Null
& $Py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'Some required libraries could not be downloaded. Checking the connection to the Python package index...'
    & $Py -m app.cli netcheck
    Write-Host '  pip configuration (an index-url here means pip uses a private mirror):'
    & $Py -m pip config list
    Write-Host ''
    Write-Host 'What to do:'
    Write-Host '  - Behind the bank proxy, in this PowerShell window type:'
    Write-Host '        $env:HTTPS_PROXY = "http://<proxy-host>:<port>"; $env:HTTP_PROXY = $env:HTTPS_PROXY'
    Write-Host '    and then run  .\setup.ps1  again from the same window.'
    Write-Host '  - Private package mirror missing a library: ask for it to be added, or set PIP_INDEX_URL.'
    exit 1
}
& $Py -m pip install "matplotlib>=3.8" 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Host 'Optional chart library (matplotlib) installed.' }
else { Write-Host 'Optional chart library (matplotlib) not available - Word report charts are drawn with Pillow instead. Nothing else changes.' }

Say '3/5  Creating the settings file (.env)'
if (-not (Test-Path '.env')) {
    $secret = (& $Py -c "import secrets; print(secrets.token_hex(32))").Trim()
    $lines = Get-Content '.env.example' | ForEach-Object { if ($_ -match '^SECRET_KEY=') { "SECRET_KEY=$secret" } else { $_ } }
    [System.IO.File]::WriteAllLines((Join-Path $PSScriptRoot '.env'), [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host 'Created .env with a fresh secret key.'
} else {
    Write-Host '.env already exists - left unchanged.'
}
foreach ($b in 'PNB', 'SBI', 'BOB', 'CANARA', 'UNION') {
    New-Item -ItemType Directory -Force -Path (Join-Path (Join-Path 'data' 'inbox') $b) | Out-Null
}

Say '4/5  Local language model (optional but recommended)'
if ($env:SKIP_MODELS -eq '1') {
    Write-Host 'Skipped (SKIP_MODELS=1).'
} elseif (Get-Command ollama -ErrorAction SilentlyContinue) {
    $model = Get-EnvValue 'OLLAMA_MODEL' 'qwen2.5:14b'
    $emb = Get-EnvValue 'EMBED_MODEL' 'nomic-embed-text'
    Write-Host "Downloading $model and $emb with Ollama (several GB the first time)..."
    ollama pull $model
    $ok1 = $LASTEXITCODE
    ollama pull $emb
    if ($ok1 -ne 0 -or $LASTEXITCODE -ne 0) { Write-Host 'Model download failed - the app still works in template mode.' }
} else {
    Write-Host 'Ollama is not installed. The app will work in template mode (no AI wording).'
    Write-Host 'To add it later: install from https://ollama.com/download/windows, then run setup.bat again.'
}

Say '5/5  Self-test (uses a temporary folder, your data is not touched)'
& $Py -m app.cli selftest
if ($LASTEXITCODE -ne 0) { Write-Host 'Self-test reported a problem - see the lines above.'; exit 1 }

$port = Get-EnvValue 'PORT' '8077'
Write-Host ''
Write-Host 'All set. Start the app by double-clicking run.bat'
Write-Host "Then open  http://localhost:$port  in a browser."
