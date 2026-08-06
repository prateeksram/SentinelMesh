# AI Hub models on the phone (`~/gf/models`)

Fetched with your Qualcomm AI Hub API token for **Snapdragon 8 Elite / Galaxy S25 Ultra**.

| Bundle | Size | Runtime | Use |
|---|---|---|---|
| `mediapipe_pose-*-8_elite_for_galaxy.zip` | ~16 MB | QNN context / precompiled ONNX | Hexagon pose (Phase 2 NPU swap) |
| `whisper_tiny-qnn_context_binary-float-…` | ~112 MB | QAIRT Context Binary | On-device ASR (hear trash-talk) |
| `whisper_tiny-precompiled_qnn_onnx-float-…` | ~113 MB | Precompiled QAIRT ONNX | Same, ONNX Runtime path |
| `qwen3_0_6b-geniex_qairt-w4a16-…` | ~752 MB | GenieX (QAIRT) w4a16 | On-device LLM commentator |

**Not fetched (license gate):** `llama_v3_2_*` — Meta Llama license requires accepting terms on HuggingFace and exporting via AI Hub Workbench (`qai-hub-models export …`). Qwen 0.6B is the drop-in substitute for THE WALL's voice brain.

These binaries need the **native app** (QNN / Genie SDK) to run — they cannot execute inside Chrome.
