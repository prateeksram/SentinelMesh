#!/data/data/com.termux/files/usr/bin/bash
# Runs ON THE PHONE: install the Qualcomm AI Hub client and register the
# API token (passed as $1 — never written into the repo).
set -eu
TOKEN="$1"
pip install -q --disable-pip-version-check qai-hub
qai-hub configure --api_token "$TOKEN" > /dev/null
echo "--- token registered; probing AI Hub ---"
qai-hub list-devices 2>/dev/null | grep -iE "elite for galaxy|galaxy s2[45]" | head -8 || echo "device list failed"
