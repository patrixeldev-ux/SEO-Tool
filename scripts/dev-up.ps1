# Starts the whole local stack on Windows with one command and opens the app.
#   powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1
# On any failure it writes diagnose.txt in the project folder (see scripts\diagnose.ps1).
# ASCII only on purpose: Windows PowerShell 5.1 reads BOM-less scripts as ANSI.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$compose = @('compose', '-f', 'docker-compose.dev.yml')

function Fail([string]$message) {
    Write-Host ''
    Write-Host "FAILED: $message" -ForegroundColor Red
    Write-Host 'Collecting logs into diagnose.txt ...'
    & (Join-Path $PSScriptRoot 'diagnose.ps1')
    exit 1
}

function Wait-Url([string]$url, [string]$mustContain, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 $url
            if ($r.StatusCode -eq 200 -and ($mustContain -eq '' -or $r.Content -like "*$mustContain*")) { return $true }
        } catch { }
        Start-Sleep -Seconds 5
        Write-Host -NoNewline '.'
    }
    Write-Host ''
    return $false
}

Write-Host '1/6 Checking Docker ...'
$ErrorActionPreference = 'Continue'
docker info *> $null
$dockerOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = 'Stop'
if (-not $dockerOk) {
    Fail 'Docker is not running. Open Docker Desktop, wait until it shows "Engine running", then run this script again.'
}

Write-Host '2/6 Checking .env ...'
if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env'; Write-Host '    created .env from .env.example' }

$envVals = @{}
Get-Content '.env' | ForEach-Object { if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.*)$') { $envVals[$matches[1]] = $matches[2].Trim() } }
$pgPort = if ($envVals['POSTGRES_PORT']) { [int]$envVals['POSTGRES_PORT'] } else { 55432 }
$redisPort = if ($envVals['REDIS_PORT']) { [int]$envVals['REDIS_PORT'] } else { 56379 }
$ports = @(5173, 8000, $pgPort, $redisPort)

Write-Host "3/6 Checking ports $($ports -join ', ') ..."
$busy = @()
# Containers from OTHER projects (e.g. Agency OS) holding our ports: the most common reason the app won't open.
$ErrorActionPreference = 'Continue'
# No spaces or quotes in the format string: Windows PowerShell 5.1 mangles embedded quotes in native arguments.
$running = docker ps --format '{{.Names}}|{{.Ports}}|{{.Labels}}' 2>$null
$ErrorActionPreference = 'Stop'
foreach ($line in $running) {
    $parts = "$line" -split '\|'
    if ($parts.Count -lt 3 -or $parts[2] -like '*com.docker.compose.project=search-os*') { continue }
    foreach ($p in $ports) {
        if ($parts[1] -like "*:$p->*") { $busy += "port $p is used by Docker container '$($parts[0])' (stop it with: docker stop $($parts[0]))" }
    }
}
# Non-Docker programs holding our ports.
$dockerProcs = @('com.docker.backend', 'wslrelay', 'vpnkit', 'com.docker.proxy', 'docker-proxy')
foreach ($p in $ports) {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        $name = (Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        if ($dockerProcs -notcontains $name) { $busy += "port $p is used by '$name' (PID $($conn.OwningProcess))" }
    }
}
if ($busy.Count -gt 0) {
    Fail ("These ports are taken: " + ($busy -join '; ') +
          ". Stop that container/program and run this script again. (Postgres/Redis ports can also be changed via POSTGRES_PORT / REDIS_PORT in .env.)")
}

Write-Host '4/6 Building and starting containers (first run takes 5-10 minutes) ...'
$ErrorActionPreference = 'Continue'
docker @compose up -d --build
$upOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = 'Stop'
if (-not $upOk) { Fail 'docker compose up failed (see the output above).' }

Write-Host -NoNewline '5/6 Waiting for the API on http://localhost:8000 '
if (-not (Wait-Url 'http://localhost:8000/health' '' 300)) { Fail 'The API did not become healthy within 5 minutes.' }
Write-Host ' ok'

$ErrorActionPreference = 'Continue'
docker @compose exec -T api python -m searchos.seed_demo
$seedOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = 'Stop'
if (-not $seedOk) { Fail 'Creating the demo account failed.' }

Write-Host -NoNewline '6/6 Waiting for the web app on http://localhost:5173 '
if (-not (Wait-Url 'http://localhost:5173/' 'id="root"' 300)) { Fail 'The web app did not start within 5 minutes.' }
Write-Host ' ok'

Write-Host ''
Write-Host 'Ready: http://localhost:5173' -ForegroundColor Green
Write-Host 'Demo login: demo@example.com / demo-password-123'
Write-Host 'Stop everything later with:  docker compose -f docker-compose.dev.yml down'
Start-Process 'http://localhost:5173'
