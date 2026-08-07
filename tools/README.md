# `tools/` — launchers, model movers, and diagnostics

Catalog of every script in this folder. Windows scripts are PowerShell (run from the **repo root** unless noted); `.sh` scripts run **on the phone in Termux**.

## Launcher

| Script | Purpose |
|---|---|
| [`start_game.ps1`](start_game.ps1) | The full game supervisor behind [`../start-game.bat`](../start-game.bat): stale-process cleanup by port ownership, host launch + health wait, optional SnapKick bridge and laptop camera relay, SSH provisioning/launch of the UNO Q streamer, watchdog, reverse shutdown. Full parameter reference: [`../docs/one_step_setup.md`](../docs/one_step_setup.md) |

## Android model push / pull (adb)

| Script | Purpose |
|---|---|
| [`push_whisper_models.ps1`](push_whisper_models.ps1) | Push Whisper Tiny QNN files (`encoder/decoder.onnx`, `*_qairt_context.bin`, `metadata.json`, `tokenizer.json`; ~112 MB) from `-Source` (default `%TEMP%\gf_whisper_npu`) to the app's `files/whisper/`. Stages via `/data/local/tmp` + `run-as` because shell-pushed external storage is invisible to the app UID. Missing files are reported, not fatal |
| [`push_qwen_models.ps1`](push_qwen_models.ps1) | Push an extracted GenieX Qwen3 0.6B w4a16 folder (~752 MB) to `files/qwen/` (`-Source`, or auto-probes `%TEMP%\gf_qwen` and `%USERPROFILE%\gf\models\qwen`). Optional — the coach works without it |
| [`pull_debug_videos.ps1`](pull_debug_videos.ps1) | Pull the newest `gf_screen_*.mp4` screen recording into `debug_videos\latest\` |

## UNO Q

| Script | Purpose |
|---|---|
| [`deploy_unoq.ps1`](deploy_unoq.ps1) | `scp` the streamer, self-contained MediaPipe ONNX backend, and requirements to `/home/arduino/sentinelmesh` (`-HostAddress`, `-User`, `-RemoteDir`) — the manual alternative to `start-game.bat -SyncUnoQ` |
| [`unoq_dnn_probe.py`](unoq_dnn_probe.py) | Read-only detector/landmark graph benchmark for CPU, OpenCL, and OpenCL-FP16 on the UNO Q; also reports the actual OpenCL device and runtime fallbacks |
| [`replay_unoq_recording.py`](replay_unoq_recording.py) | Replay a `--record-jsonl` capture through the real UDP 9999 path with rebased timestamps (`--speed`, `--no-timing`) for deterministic offline tuning |
| [`unoq_imu_sender.py`](unoq_imu_sender.py) | **Legacy, superseded** — old IMU-only sender (LSM6DSOX → `{yaw,pitch,force,event}` on UDP 5005); `LAPTOP_IP` is hardcoded and must be edited. Kept for reference |

## AI Hub model acquisition

The pose bundle downloads without auth; the Whisper/Qwen paths document what was attempted — token-gated AI Hub fetching proved unreliable, so treat these as **best-effort helpers** and expect a manual unzip/rename step before the push scripts (which want loose files, not zips).

| Script | Runs on | Purpose |
|---|---|---|
| [`fetch_models.sh`](fetch_models.sh) | phone (Termux) | `curl` the public S3 `mediapipe_pose` bundles (8 Elite for Galaxy) into `~/gf/models` — no token needed |
| [`fetch_aihub_models.sh`](fetch_aihub_models.sh) | phone | `qai-hub-models fetch whisper_tiny_en` across runtimes (needs `QAI_HUB_API_TOKEN`) |
| [`fetch_models_relay.py`](fetch_models_relay.py) | laptop | Same Whisper fetch via a throwaway venv on the laptop; results are scp'd to the phone manually |
| [`aihub_probe.sh`](aihub_probe.sh) / [`aihub_curl_fetch.sh`](aihub_curl_fetch.sh) | phone | Auth/endpoint diagnostics for AI Hub + gated HF assets; promote any successful download |
| [`setup_aihub.sh`](setup_aihub.sh) | phone | `pip install qai-hub` + `qai-hub configure` + device-list sanity check |
| [`list_models.sh`](list_models.sh) | phone | List contents of every downloaded `~/gf/models/*.zip` |
| [`probe_whisper.py`](probe_whisper.py) | laptop | HEAD-check candidate Whisper URLs (status + size), stdlib only |

**Model inventory for the phone** (Snapdragon 8 Elite / Galaxy S25 Ultra): `mediapipe_pose` QNN bundle (~16 MB, ships in the APK), Whisper Tiny QNN (~112 MB, pushed), Qwen3 0.6B GenieX w4a16 (~752 MB, optional push; acquisition is manual — no script fetches it). Llama 3.2 was ruled out by its HF license gate; Qwen is the drop-in substitute. These binaries need the native app's QNN/Genie runtimes — they cannot run in a browser.

## Phone-page debugging

| Script | Purpose |
|---|---|
| [`phone_debug.py`](phone_debug.py) | Chrome DevTools client over `adb forward tcp:9222` for `phone.html` tabs: `probe` (DOM/camera state), `close-dupes`, `reload`, `eval "<expr>"`. Needs `aiohttp` |
