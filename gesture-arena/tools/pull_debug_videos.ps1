# Pull the newest debug screen recording from the phone into debug_videos/latest/.
# Usage: powershell -ExecutionPolicy Bypass -File tools\pull_debug_videos.ps1

$ErrorActionPreference = "Stop"

$pkg = "com.sentinelmesh.gesturefootball"
$deviceDir = "/sdcard/Android/data/$pkg/files/Movies"
$repoRoot = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $repoRoot "debug_videos\latest"

New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# Timestamped names (gf_screen_yyyyMMdd_HHmmss.mp4) sort naturally — last one is newest.
$files = adb shell "ls $deviceDir 2>/dev/null" 2>$null |
    Where-Object { $_ -match '^gf_screen_.*\.mp4$' } |
    Sort-Object

if (-not $files) {
    Write-Host "No gf_screen_*.mp4 on the device ($deviceDir)." -ForegroundColor Yellow
    Write-Host "Tap REC in the app, do the thing, tap STOP, then rerun this script."
    exit 1
}

$newest = ($files | Select-Object -Last 1).Trim()

# Keep latest/ holding exactly the newest clip.
Get-ChildItem $outDir -Filter "gf_screen_*.mp4" -ErrorAction SilentlyContinue | Remove-Item -Force

adb pull "$deviceDir/$newest" "$outDir\$newest"
Write-Host "Pulled newest screen recording -> debug_videos\latest\$newest" -ForegroundColor Green
