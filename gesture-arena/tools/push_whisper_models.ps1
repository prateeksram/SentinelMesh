# Push AI Hub Whisper-Tiny QNN bundles into the app's internal filesDir.
# External Android/data/... owned by shell is often invisible to the app UID,
# so we stage under /data/local/tmp and copy via run-as.
# Source: Termux ~/gf/models/... or a local folder of encoder/decoder onnx+bin.
param(
    [string]$Source = "$env:TEMP\gf_whisper_npu"
)
$pkg = "com.sentinelmesh.gesturefootball"
$tmp = "/data/local/tmp/gf_whisper"
adb shell "mkdir -p $tmp"
foreach ($f in @(
    "encoder.onnx", "encoder_qairt_context.bin",
    "decoder.onnx", "decoder_qairt_context.bin",
    "metadata.json", "tokenizer.json"
)) {
    $p = Join-Path $Source $f
    if (Test-Path $p) {
        adb push $p "$tmp/$f"
    } else {
        Write-Host "MISSING $p"
    }
}
adb shell "run-as $pkg sh -c 'mkdir -p files/whisper && cp -f $tmp/* files/whisper/ && ls -lah files/whisper/'"
Write-Host "Done. Force-stop + relaunch Gesture Football — badge should show VOICE · LISTENING"
