#!/data/data/com.termux/files/usr/bin/bash
# Fetch AI Hub models with curl + API token (no Python SDK — Termux is on 3.14).
set -eu
TOKEN="${QAI_HUB_API_TOKEN:?set QAI_HUB_API_TOKEN}"
OUT=~/gf/models/aihub
mkdir -p "$OUT"
cd "$OUT"

auth() {
  # Qualcomm Workbench accepts X-API-Key / Authorization Bearer
  curl -sS -L \
    -H "Authorization: Bearer $TOKEN" \
    -H "X-API-Token: $TOKEN" \
    -H "api_token: $TOKEN" \
    "$@"
}

echo "=== probe workbench API ==="
for url in \
  "https://workbench.aihub.qualcomm.com/api/v1/devices" \
  "https://workbench.aihub.qualcomm.com/api/v1/me" \
  "https://app.aihub.qualcomm.com/api/v1/devices" \
  "https://aihub.qualcomm.com/api/v1/devices"
do
  code=$(curl -sS -o /tmp/aihub_probe.json -w "%{http_code}" -L \
    -H "Authorization: Bearer $TOKEN" \
    -H "X-API-Token: $TOKEN" \
    "$url" || echo err)
  echo "$code  $url"
  head -c 200 /tmp/aihub_probe.json; echo
done

echo "=== try authenticated S3 whisper URLs ==="
B="https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models"
CHIP="qualcomm_snapdragon_8_elite_for_galaxy"
for path in \
  "whisper_tiny_en/releases/v0.59.0/whisper_tiny_en-tflite-float.zip" \
  "whisper_tiny_en/releases/v0.59.0/whisper_tiny_en-qnn_context_binary-float-$CHIP.zip" \
  "whisper_tiny_en/releases/v0.58.0/whisper_tiny_en-tflite-float.zip" \
  "whisper_base_en/releases/v0.59.0/whisper_base_en-tflite-float.zip"
do
  f=$(basename "$path")
  code=$(curl -sS -o "$f.tmp" -w "%{http_code}" -L \
    -H "Authorization: Bearer $TOKEN" \
    -H "x-amz-security-token: $TOKEN" \
    "$B/$path" || echo err)
  size=$(wc -c < "$f.tmp" 2>/dev/null || echo 0)
  if [ "$code" = "200" ] && [ "$size" -gt 1000 ]; then
    mv "$f.tmp" "$f"
    echo "OK $code $f ($(du -h "$f" | cut -f1))"
  else
    echo "FAIL $code $f size=$size"
    rm -f "$f.tmp"
  fi
done

echo "=== HuggingFace gated resolve with token ==="
for repo_file in \
  "qualcomm/Whisper-Tiny-En/resolve/main/release_assets.json" \
  "qualcomm/Llama-v3.2-3B-Chat/resolve/main/release_assets.json" \
  "qualcomm/Whisper-Base-En/resolve/main/release_assets.json"
do
  f=$(basename "$(dirname "$repo_file")")_$(basename "$repo_file")
  code=$(curl -sS -o "$f" -w "%{http_code}" -L \
    -H "Authorization: Bearer $TOKEN" \
    "https://huggingface.co/$repo_file" || echo err)
  echo "$code  $f ($(wc -c < "$f" 2>/dev/null || echo 0) bytes)"
  head -c 180 "$f"; echo
done

echo "=== done ==="
ls -lah "$OUT" 2>/dev/null | head -30
