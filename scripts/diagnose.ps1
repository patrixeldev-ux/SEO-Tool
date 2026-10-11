# Writes diagnose.txt in the project folder with everything needed to debug the local stack.
#   powershell -ExecutionPolicy Bypass -File scripts\diagnose.ps1
# Does not include the contents of .env. ASCII only (Windows PowerShell 5.1).

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$out = Join-Path $root 'diagnose.txt'
$compose = @('compose', '-f', 'docker-compose.dev.yml')

& {
    '=== time'; Get-Date -Format o
    '=== windows'; [System.Environment]::OSVersion.VersionString
    '=== git'; git branch --show-current 2>&1; git log --oneline -1 2>&1
    '=== .env present'; Test-Path '.env'
    '=== docker version'; docker version 2>&1
    '=== docker compose version'; docker compose version 2>&1
    '=== all running containers (any project)'; docker ps --format '{{.Names}} | {{.Ports}} | {{.Status}}' 2>&1
    '=== compose ps -a'; docker @compose ps -a 2>&1
    foreach ($s in 'postgres', 'redis', 'api', 'worker', 'beat', 'web') {
        "=== logs $s (last 80)"; docker @compose logs --no-color --tail 80 $s 2>&1
    }
    '=== listening ports'
    Get-NetTCPConnection -State Listen -LocalPort 5173, 8000, 5432, 6379, 55432, 56379 -ErrorAction SilentlyContinue |
        ForEach-Object { "$($_.LocalAddress):$($_.LocalPort) pid=$($_.OwningProcess) $((Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName)" }
    '=== http checks'
    foreach ($u in 'http://localhost:8000/health', 'http://localhost:5173/') {
        try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 $u; "$u -> $($r.StatusCode)" }
        catch { "$u -> $($_.Exception.Message)" }
    }
} *>&1 | ForEach-Object { "$_" } | Out-File -Encoding utf8 $out

Write-Host "Saved $out" -ForegroundColor Yellow
Write-Host 'Tell Claude: "diagnose ready".'
