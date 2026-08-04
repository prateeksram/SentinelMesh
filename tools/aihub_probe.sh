#!/data/data/com.termux/files/usr/bin/bash
# Probe Qualcomm AI Hub auth + download Whisper/LLM assets onto the phone.
set -u
TOKEN="${1:?usage: aihub_probe.sh <api_token>}"
OUT="$HOME/gf/models/aihub"
mkdir -p "$OUT" /tmp
cd "$OUT"

echo "python: $(python --version 2>&1)"
echo "token length: ${#TOKEN}"

echo
echo "=== 1) Workbench auth probes ==="
for hdr in \
  "Authorization: Token ${TOKEN}" \
  "Authorization: Bearer ${TOKEN}" \
  "X-API-Token: ${TOKEN}" \
  "api-token: ${TOKEN}"
do
  code=$(curl -sS -o /tmp/aihub_body.txt -w "%{http_code}" \
    -H "$hdr" \
    "https://workbench.aihub.qualcomm.com/api/v1/devices" || echo curl_fail)
  bytes=$(wc -c < /tmp/aihub_body.txt 2>/dev/null || echo 0)
  echo "[$code] bytes=$bytes  via: $hdr"
  head -c 160 /tmp/aihub_body.txt; echo
done

echo
echo "=== 2) HuggingFace resolve with AI Hub token as HF token ==="
for path in \
  "qualcomm/Whisper-Tiny-En/resolve/main/README.md" \
  "qualcomm/Whisper-Tiny-En/resolve/main/release_assets.json" \
  "qualcomm/Llama-v3.2-3B-Instruct/resolve/main/README.md"
do
  name=$(echo "$path" | tr '/' '_')
  code=$(curl -sS -o "/tmp/$name" -w "%{http_code}" -L \
    -H "Authorization: Bearer ${TOKEN}" \
    "https://huggingface.co/${path}" || echo curl_fail)
  bytes=$(wc -c < "/tmp/$name" 2>/dev/null || echo 0)
  echo "[$code] $bytes bytes  $path"
  head -c 120 "/tmp/$name"; echo
done

echo
echo "=== 3) Public S3 pose already on phone; try whisper with query token ==="
BASE="https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models"
for path in \
  "whisper_tiny_en/releases/v0.59.0/whisper_tiny_en-tflite-float.zip" \
  "whisper_tiny_en/releases/v0.59.0/whisper_tiny_en-onnx-float.zip" \
  "mediapipe_pose/releases/v0.59.0/mediapipe_pose-tflite-float.zip"
do
  f=$(basename "$path")
  code=$(curl -sS -o "/tmp/$f" -w "%{http_code}" -L "${BASE}/${path}" || echo curl_fail)
  bytes=$(wc -c < "/tmp/$f" 2>/dev/null || echo 0)
  echo "[$code] $bytes bytes  $f"
  if [ "$code" = "200" ] && [ "${bytes:-0}" -gt 5000 ]; then
    mv "/tmp/$f" "$OUT/$f"
    echo "  SAVED -> $OUT/$f"
  fi
done

echo
echo "=== 4) Try installing python 3.12 via conda-like or pyenv? skip ==="
echo "listing existing models:"
ls -lah "$HOME/gf/models" "$OUT" 2>/dev/null
echo DONE
