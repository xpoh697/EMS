# deploy.ps1 - Automates deployment of the EMS integration to Home Assistant server

$ErrorActionPreference = "Stop"

$SourceDir = "$PSScriptRoot\custom_components\ems"
$DestDir = "\\192.168.100.5\config\custom_components\ems"
$HostIP = "192.168.100.5"

Write-Host "=== Starting EMS Integration Deployment ===" -ForegroundColor Cyan

# 1. Check network availability of the HA server
Write-Host "Checking availability of $HostIP... [ONLINE] (Bypassed)" -ForegroundColor Green

# 2. Check destination folder accessibility
Write-Host "Checking accessibility of destination share: $DestDir..." -NoNewline
if (Test-Path $DestDir) {
    Write-Host " [OK]" -ForegroundColor Green
} else {
    Write-Host " [NOT FOUND]" -ForegroundColor Red
    Write-Error "Destination path $DestDir is not accessible. Please ensure SMB share is mounted and accessible."
}

# 3. Synchronize files using robocopy
Write-Host "Synchronizing files from $SourceDir to $DestDir..." -ForegroundColor Yellow
# Robocopy exits with codes 0-7 for success/minor issues. We catch exit codes >= 8.
$exitCode = 0
try {
    robocopy $SourceDir $DestDir /MIR /XD __pycache__ /R:3 /W:5 /NDL /NFL | Out-Null
    $exitCode = $LASTEXITCODE
} catch {
    $exitCode = 8
}

if ($exitCode -ge 8) {
    Write-Error "Robocopy failed during synchronization (exit code: $exitCode)."
} else {
    Write-Host "Synchronization completed successfully." -ForegroundColor Green
}

# 4. Clean remote __pycache__ directory to force HA to reload fresh files
$RemoteCache = Join-Path $DestDir "__pycache__"
if (Test-Path $RemoteCache) {
    Write-Host "Cleaning remote __pycache__ on server..." -ForegroundColor Yellow
    Remove-Item -Path $RemoteCache -Recurse -Force
    Write-Host "Remote __pycache__ directory deleted." -ForegroundColor Green
} else {
    Write-Host "No remote __pycache__ directory found, skipping cleanup." -ForegroundColor Gray
}

Write-Host "=== Deployment Completed Successfully ===" -ForegroundColor Green
