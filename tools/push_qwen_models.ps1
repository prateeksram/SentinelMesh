# Stage GenieX Qwen3 0.6B weights into the app's internal files/qwen/.
# Source: Termux ~/gf/models/qwen3_0_6b-geniex_* or a local extract folder.
# Large bins stay off git — push per device for demos.
param(
    [string]$Source = ""
)
$pkg = "com.sentinelmesh.gesturefootball"
$tmp = "/data/local/tmp/gf_qwen"

if (-not $Source) {
    $candidates = @(
        "$env:TEMP\gf_qwen",
        "$env:USERPROFILE\gf\models\qwen"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $Source = $c; break }
    }
}
if (-not $Source -or -not (Test-Path $Source)) {
    Write-Host "Set -Source to the extracted GenieX qwen3_0_6b folder (from ~/gf/models)."
    Write-Host "App still runs a grounded on-device coach without these weights."
    exit 1
}

adb shell "mkdir -p $tmp"
Get-ChildItem $Source -File | ForEach-Object {
    adb push $_.FullName "$tmp/$($_.Name)"
}
adb shell "run-as $pkg sh -c 'mkdir -p files/qwen && cp -f $tmp/* files/qwen/ && ls -lah files/qwen/ | head'"
Write-Host "Done. Relaunch Gesture Football — NEURAL LOAD backend becomes QWEN when config is present."
