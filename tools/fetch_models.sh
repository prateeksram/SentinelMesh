#!/data/data/com.termux/files/usr/bin/bash
# Runs ON THE PHONE: fetch Qualcomm AI Hub public model bundles for the
# Snapdragon 8 Elite (Galaxy) straight onto the device.
set -u
mkdir -p ~/gf/models
cd ~/gf/models

B="https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models"
V="v0.59.0"

FILES="
mediapipe_pose/releases/$V/mediapipe_pose-qnn_context_binary-w8a8-qualcomm_snapdragon_8_elite_for_galaxy.zip
mediapipe_pose/releases/$V/mediapipe_pose-qnn_context_binary-float-qualcomm_snapdragon_8_elite_for_galaxy.zip
mediapipe_pose/releases/$V/mediapipe_pose-precompiled_qnn_onnx-w8a8-qualcomm_snapdragon_8_elite_for_galaxy.zip
"

for path in $FILES; do
  f=$(basename "$path")
  if [ -s "$f" ]; then echo "SKIP $f (exists)"; continue; fi
  if curl -sfL --retry 2 -o "$f" "$B/$path"; then
    echo "OK   $f  $(du -h "$f" | cut -f1)"
  else
    echo "FAIL $f"
    rm -f "$f"
  fi
done
echo "---"
ls -la ~/gf/models
