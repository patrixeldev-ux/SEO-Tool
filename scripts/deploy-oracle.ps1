<#
.SYNOPSIS
  Installs or updates Search OS on an Oracle Cloud VM from your Windows PC.

.DESCRIPTION
  Copies deploy/oracle/install.sh to the VM over SSH and runs it. Uses the OpenSSH that ships with Windows 10/11
  (ssh.exe and scp.exe). Safe to run again: it updates the code and keeps your settings and data.

.EXAMPLE
  .\scripts\deploy-oracle.ps1 -Ip 152.67.1.2 -KeyPath "$env:USERPROFILE\.ssh\oracle.key"

.EXAMPLE
  .\scripts\deploy-oracle.ps1 -Ip 152.67.1.2 -KeyPath C:\keys\ssh-key.key -Branch main
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Ip,
  [Parameter(Mandatory = $true)][string]$KeyPath,
  [string]$User = "ubuntu",
  [string]$Branch = "main",
  [string]$RepoUrl = "",
  [string]$GithubToken = ""
)

$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
  Write-Host ""
  Write-Host "ERROR: $Message" -ForegroundColor Red
  exit 1
}

function Step([string]$Message) {
  Write-Host "==> $Message" -ForegroundColor Cyan
}

# --- 0. checks on this PC ---------------------------------------------------------------------------------
if (-not (Get-Command ssh -ErrorAction SilentlyContinue) -or -not (Get-Command scp -ErrorAction SilentlyContinue)) {
  Fail ("ssh / scp were not found. Install the Windows feature: Settings > Apps > Optional features > " +
    "Add a feature > 'OpenSSH Client', then open a new PowerShell window.")
}
if ($Ip -notmatch '^\d{1,3}(\.\d{1,3}){3}$') {
  Fail "-Ip must be the VM's public IPv4 address, for example 152.67.1.2 (you gave '$Ip')."
}
if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
  Fail "The key file was not found: $KeyPath`nUse the private key you downloaded when you created the VM (often ssh-key-DATE.key)."
}
$KeyPath = (Resolve-Path -LiteralPath $KeyPath).Path
$installScript = Join-Path $PSScriptRoot "..\deploy\oracle\install.sh"
if (-not (Test-Path -LiteralPath $installScript)) {
  Fail "deploy\oracle\install.sh was not found next to this script. Run this from a full checkout of the repository."
}

$sshOpts = @(
  "-i", $KeyPath,
  "-o", "StrictHostKeyChecking=accept-new",
  "-o", "ConnectTimeout=25",
  "-o", "ServerAliveInterval=30",
  "-o", "IdentitiesOnly=yes"
)
$target = "$User@$Ip"

function Explain-SshFailure([string]$Output) {
  if ($Output -match "UNPROTECTED PRIVATE KEY FILE|bad permissions|Permissions .* too open") {
    Fail ("Windows says the key file is readable by other users, so ssh refuses to use it.`n" +
      "Fix it by running these two commands in PowerShell, then run this script again:`n`n" +
      "  icacls `"$KeyPath`" /inheritance:r`n" +
      "  icacls `"$KeyPath`" /grant:r `"`${env:USERNAME}:(R)`"`n")
  }
  if ($Output -match "timed out|Connection timed out|No route to host|Network is unreachable") {
    Fail ("The connection to $Ip timed out.`n" +
      "1. Check the IP address and that the VM is 'Running' in the Oracle Cloud console.`n" +
      "2. In the Oracle console open Networking > Virtual cloud networks > your VCN > Security Lists >`n" +
      "   Default Security List > Add Ingress Rules. Make sure TCP port 22 is allowed, and add TCP 80 and 443`n" +
      "   (Source CIDR 0.0.0.0/0). The guide docs\DEPLOY_ORACLE.md has the exact clicks.`n" +
      "3. A VPN or company firewall on this PC can also block port 22.")
  }
  if ($Output -match "Connection refused") {
    Fail "The VM refused the connection on port 22. Wait a minute if it was just created, or check that the instance is running."
  }
  if ($Output -match "Permission denied") {
    Fail ("The VM rejected the key.`n" +
      "- Use the PRIVATE key that matches the public key you gave Oracle (not the .pub file).`n" +
      "- The user name for Ubuntu images is 'ubuntu' (for Oracle Linux it is 'opc'). Use -User to change it.")
  }
  Fail "ssh failed:`n$Output"
}

# --- 1. can we log in? ------------------------------------------------------------------------------------
Step "Connecting to $target"
$prev = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$probe = (& ssh @sshOpts -o BatchMode=yes $target "echo searchos-ssh-ok" 2>&1 | Out-String)
$probeCode = $LASTEXITCODE
$ErrorActionPreference = $prev
if ($probeCode -ne 0 -or $probe -notmatch "searchos-ssh-ok") {
  Explain-SshFailure $probe
}

# --- 2. copy the installer (with Unix line endings) -------------------------------------------------------
Step "Copying the installer"
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) "searchos-install.sh"
$text = [System.IO.File]::ReadAllText($installScript) -replace "`r`n", "`n"
[System.IO.File]::WriteAllText($tmp, $text, (New-Object System.Text.UTF8Encoding($false)))
$ErrorActionPreference = "Continue"
$copyOut = (& scp @sshOpts $tmp "${target}:/tmp/searchos-install.sh" 2>&1 | Out-String)
$copyCode = $LASTEXITCODE
$ErrorActionPreference = $prev
Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
if ($copyCode -ne 0) {
  Explain-SshFailure $copyOut
}

# --- 3. run it ---------------------------------------------------------------------------------------------
Step "Running the installer on the VM (the first run takes 10-20 minutes; keep this window open)"
$envPrefix = ""
if ($RepoUrl -ne "") { $envPrefix += "REPO_URL='$RepoUrl' " }
if ($GithubToken -ne "") { $envPrefix += "GITHUB_TOKEN='$GithubToken' " }
$remote = "${envPrefix}bash /tmp/searchos-install.sh '$Branch'"
& ssh @sshOpts -t $target $remote
$code = $LASTEXITCODE
if ($code -ne 0) {
  Fail ("The installer stopped with exit code $code. Read the message above. You can run this script again: " +
    "it continues where it stopped and keeps your settings.")
}

$hostName = ($Ip -replace '\.', '-') + ".sslip.io"
Write-Host ""
Write-Host "Done. Open https://$hostName/ in your browser." -ForegroundColor Green
Write-Host "Create your admin account:"
Write-Host "  ssh -i `"$KeyPath`" -t $target `"cd ~/seo-tool && deploy/oracle/create-admin.sh`""
