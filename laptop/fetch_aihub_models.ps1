# Fetch / export AI Hub models for the laptop four-pillar stack.
# Requires: QAI_HUB_API_TOKEN in the environment (never commit the token).
# Run on the Snapdragon X Elite with the Python that has qai_hub_models.
#
# Usage:
#   $env:QAI_HUB_API_TOKEN = "<token>"
#   .\laptop\fetch_aihub_models.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Models = Join-Path $Root "models"
New-Item -ItemType Directory -Force -Path $Models | Out-Null

if (-not $env:QAI_HUB_API_TOKEN) {
    Write-Error "Set QAI_HUB_API_TOKEN before running this script."
}

$iniDir = Join-Path $env:USERPROFILE ".qai_hub"
New-Item -ItemType Directory -Force -Path $iniDir | Out-Null
@"
[api]
api_token = $($env:QAI_HUB_API_TOKEN)
api_url = https://workbench.aihub.qualcomm.com
web_url = https://workbench.aihub.qualcomm.com
"@ | Set-Content -Path (Join-Path $iniDir "client.ini") -Encoding UTF8
Write-Host "Wrote ~/.qai_hub/client.ini (token not echoed)"

Write-Host "`n=== GenieX LLM (Qwen3-4B-Instruct-2507) ==="
$geniex = Get-Command geniex -ErrorAction SilentlyContinue
if ($geniex) {
    & geniex pull ai-hub-models/Qwen3-4B-Instruct-2507
    Write-Host "Start serve with: geniex serve   # http://127.0.0.1:18181/v1"
} else {
    Write-Warning "geniex not on PATH — install GenieX / AI Stack, then: geniex pull ai-hub-models/Qwen3-4B-Instruct-2507"
}

Write-Host "`n=== Depth-Anything-V2 → models/hero_depth.onnx ==="
python -m pip install -q numpy onnxruntime 2>$null

$Out = Join-Path $Models "_export_depth"
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$dest = Join-Path $Models "hero_depth.onnx"

# Fast path: AI Hub public Universal float ONNX zip (no cloud compile needed)
$zipUrl = "https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models/depth_anything_v2/releases/v0.59.0/depth_anything_v2-onnx-float.zip"
$zipPath = Join-Path $Models "_depth_onnx_float.zip"
$exported = $false
try {
    Write-Host "Downloading Universal float ONNX zip…"
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
    if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }
    Expand-Archive -Path $zipPath -DestinationPath $Out -Force
    $exported = $true
} catch {
    Write-Warning "Public zip download failed: $_"
}

if (-not $exported) {
    try {
        python -m qai_hub_models.models.depth_anything_v2.export `
            --target-runtime onnx `
            --device "Snapdragon X Elite CRD" `
            --output-dir $Out
        $exported = $true
    } catch {
        Write-Warning "Device export failed: $_"
    }
}

$onnx = Get-ChildItem -Path $Out -Recurse -Filter "*.onnx" -ErrorAction SilentlyContinue |
    Sort-Object Length -Descending |
    Select-Object -First 1
if ($onnx) {
    # Keep Hub basename so external *.data locator resolves.
    $canonOnnx = Join-Path $Models "depth_anything_v2.onnx"
    $canonData = Join-Path $Models "depth_anything_v2.data"
    Copy-Item $onnx.FullName $canonOnnx -Force
    Copy-Item $onnx.FullName $dest -Force
    $dataSibling = Join-Path $onnx.DirectoryName ($onnx.BaseName + ".data")
    if (Test-Path $dataSibling) {
        Copy-Item $dataSibling $canonData -Force
        Copy-Item $dataSibling (Join-Path $Models "hero_depth.data") -Force
    }
    Write-Host "Installed $($onnx.Name) → $canonOnnx (+ hero_depth.onnx alias)"
} elseif (Test-Path $dest) {
    Write-Host "Keeping existing $dest"
} else {
    Write-Warning "No ONNX produced. Place Depth-Anything-V2 ONNX at laptop/models/hero_depth.onnx manually."
}

Write-Host "`nDone. Restart: python server.py"
