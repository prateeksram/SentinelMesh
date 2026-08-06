param(
    [string]$HostAddress = "192.168.150.72",
    [string]$User = "arduino",
    [string]$RemoteDir = "/home/arduino/sentinelmesh"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$streamer = Join-Path $repoRoot "unoq\sentinel_pose_streamer.py"
$requirements = Join-Path $repoRoot "unoq\requirements.txt"

ssh "$User@$HostAddress" "mkdir -p '$RemoteDir'"
scp $streamer "$User@$HostAddress`:$RemoteDir/sentinel_pose_streamer.py"
scp $requirements "$User@$HostAddress`:$RemoteDir/requirements.txt"

Write-Host "UNO Q files copied to $User@$HostAddress`:$RemoteDir"
Write-Host "Next: follow docs/unoq_pipeline.md to start the streamer."
