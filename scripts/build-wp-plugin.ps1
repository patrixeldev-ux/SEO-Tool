# Builds dist\searchos-connector.zip, ready to upload in WordPress (Plugins > Add New > Upload Plugin).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force -Path "$root\dist" | Out-Null
$zip = "$root\dist\searchos-connector.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "$root\wp-plugin\searchos-connector" -DestinationPath $zip
Write-Host $zip
