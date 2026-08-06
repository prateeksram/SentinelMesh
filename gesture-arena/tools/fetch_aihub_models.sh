#!/data/data/com.termux/files/usr/bin/bash
# Runs ON THE PHONE: lightweight AI Hub Models CLI + fetch gated/public assets
# for Snapdragon 8 Elite (Galaxy). Token via env QAI_HUB_API_TOKEN (never committed).
set -eu
export QAI_HUB_API_TOKEN="${QAI_HUB_API_TOKEN:-}"
mkdir -p ~/gf/models/aihub
cd ~/gf/models/aihub

echo "=== installing lightweight qai_hub_models_cli ==="
pip install -q --disable-pip-version-check "qai_hub_models_cli" || {
  echo "CLI install failed"; exit 1
}
echo "CLI: $(qai-hub-models --version 2>/dev/null || qai-hub-models -h | head -1)"

# Configure token for Workbench-backed fetches if present
if [ -n "$QAI_HUB_API_TOKEN" ]; then
  mkdir -p ~/.qai_hub
  cat > ~/.qai_hub/client.ini <<EOF
[api]
api_token = $QAI_HUB_API_TOKEN
api_url = https://workbench.aihub.qualcomm.com
web_url = https://workbench.aihub.qualcomm.com
EOF
  echo "token written to ~/.qai_hub/client.ini"
fi

CHIP="qualcomm-snapdragon-8-elite-for-galaxy"
OUT=~/gf/models/aihub

echo "=== listing Whisper options ==="
qai-hub-models info whisper_tiny_en 2>&1 | head -40 || true
qai-hub-models models --llm 2>&1 | head -30 || true

echo "=== fetch whisper_tiny_en (tflite float, if available) ==="
qai-hub-models fetch whisper_tiny_en -r tflite -p float -q -o "$OUT" 2>&1 || \
qai-hub-models fetch whisper_tiny_en -r onnx -p float -q -o "$OUT" 2>&1 || \
echo "whisper tiny fetch failed"

echo "=== fetch whisper_tiny_en QNN for 8 Elite Galaxy ==="
qai-hub-models fetch whisper_tiny_en -r qnn -p float -c "$CHIP" -q -o "$OUT" 2>&1 || \
qai-hub-models fetch whisper_tiny_en -r qnn_context_binary -p float -c "$CHIP" -q -o "$OUT" 2>&1 || \
echo "whisper QNN fetch failed (may need different runtime name)"

echo "=== fetch llama / small LLM catalog hit ==="
qai-hub-models models --llm -q 2>&1 | head -20 || true

echo "=== done; contents ==="
ls -lah "$OUT" ~/gf/models 2>/dev/null | head -40
