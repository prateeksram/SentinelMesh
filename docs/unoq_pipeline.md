# UNO Q edge pose pipeline (optional)

Moves USB-camera pose inference onto the Arduino UNO Q while keeping the phone UI and the phone NPU/GPU/CPU pipeline intact. The local phone pipeline stays the default; the phone falls back to it automatically if edge packets stop for ~2.5 s.

## Data path

```
USB webcam → UNO Q OpenCV DNN BlazePose (+ Lucas–Kanade optical flow)
                 │  UDP :9999  sentinel.edge.pose.v1 (33 landmarks + flow)
                 │  HTTP POST /edge/frame (preview JPEG)
                 ▼
            server.py :8080  ──ws edge_pose──►  phone app (Mode: UNO Q)
                                                 · EdgeKickEngine (calibrated)
                                                 · ShotTrajectoryEstimator
```

The phone keeps calibration, kick detection, ForcePose/trajectory math, voice coach, and the private profile. Only camera capture and pose-model execution move to the board. Backend today is **OpenCV DNN** (OpenCV Zoo MediaPipe ONNX). CPU is the safe default; OpenCL is retained only as an explicit benchmark target because it is much slower for these graphs on the current FD702 driver.

## Prerequisites

- Laptop, phone, and UNO Q on the same LAN; inbound TCP 8080 + UDP 9999 allowed on the laptop.
- [`unoq/mediapipe_onnx_backend.py`](../unoq/mediapipe_onnx_backend.py), deployed beside the streamer. SentinelMesh owns this backend; no SnapKick checkout is required for inference.
- Float models on the board:
  - `/home/arduino/models/opencv-mediapipe/pose_estimation_mediapipe_2023mar.onnx`
  - `/home/arduino/models/opencv-mediapipe/person_detection_mediapipe_2023mar.onnx`
- Do **not** install the MediaPipe Python wheel on the board's Python 3.13 image - this path uses OpenCV DNN only ([`unoq/requirements.txt`](../unoq/requirements.txt): numpy + opencv-python-headless).

> The easy path is `.\start-game.bat -UnoQIp <ip> [-SyncUnoQ]` ([`one_step_setup.md`](one_step_setup.md)), which does steps 1, 2, and 5 below automatically. The manual steps follow.

## 1. Start the laptop host

```powershell
python -m pip install -r requirements.txt
python server.py
```

Check `http://127.0.0.1:8080/edge/status` - camera and pose report `waiting` until the board starts.

## 2. Copy the board wrapper

```powershell
.\tools\deploy_unoq.ps1 -HostAddress 192.168.150.72
```

(or copy [`unoq/sentinel_pose_streamer.py`](../unoq/sentinel_pose_streamer.py), [`unoq/mediapipe_onnx_backend.py`](../unoq/mediapipe_onnx_backend.py), and [`unoq/requirements.txt`](../unoq/requirements.txt) to `/home/arduino/sentinelmesh` manually).

## 3. Identify the USB camera on the board

```bash
v4l2-ctl --list-devices
ls -l /dev/video*
```

Use the real UVC capture device, not a Qualcomm Venus codec device.

## 4. Board environment

The launcher prefers the SnapKick venv when it exists and otherwise falls back
to the board's `python3`. On the currently deployed board the venv is absent,
so the tested runtime is system Python 3.13 with OpenCV 4.10:

```bash
PY=/home/arduino/snapkick-starter/unoq/.venv/bin/python
if [ ! -x "$PY" ]; then PY=python3; fi
"$PY" -c 'import cv2, numpy; print(cv2.__version__)'
```

## 5. Run inference on the board

```bash
PY=/home/arduino/snapkick-starter/unoq/.venv/bin/python
if [ ! -x "$PY" ]; then PY=python3; fi
"$PY" /home/arduino/sentinelmesh/sentinel_pose_streamer.py \
  --laptop-ip LAPTOP_IP \
  --camera /dev/video0 \
  --model /home/arduino/models/opencv-mediapipe/pose_estimation_mediapipe_2023mar.onnx \
  --person-model /home/arduino/models/opencv-mediapipe/person_detection_mediapipe_2023mar.onnx \
  --dnn-target cpu \
  --width 640 --height 480 --camera-fps 30 \
  --target-fps 10 --preview-fps 5 \
  --detector-interval 4 --detector-stable-interval 6 --detector-recovery-interval 1 \
  --optical-flow \
  --record-jsonl /home/arduino/sentinelmesh/recordings/kicks.jsonl   # optional
```

The terminal reports measured `pose_fps`, `infer_ms`, body presence, and UDP packet size. Requested FPS is only a cap - measured FPS is what matters. Verify transport from the laptop:

- `http://127.0.0.1:8080/edge/status` (`camera` and `pose` → `live`)
- `http://127.0.0.1:8080/edge/camera.mjpg`

### Variant: USB camera on the laptop instead

Keep `server.py` running and feed frames from Windows via the SnapKick relay; the board then reads the MJPEG stream instead of `/dev/video0`:

```powershell
$env:OPENCV_VIDEOIO_PRIORITY_MSMF = "0"    # DirectShow avoids a UVC reopen stall
<snapkick>\laptop\.venv\Scripts\python.exe <snapkick>\unoq\camera_relay.py `
  --camera 1 --url http://127.0.0.1:8080/edge/source/frame --width 640 --height 480 --fps 30
```

On the board, replace the camera argument with `--camera http://LAPTOP_IP:8080/edge/source/camera.mjpg`. Start order matters: server → relay → streamer, and restart the streamer whenever the server restarts (OpenCV's network `VideoCapture` does not reopen broken MJPEG connections). Do not run the SnapKick laptop server itself - it competes for UDP 9999.

## 6. Select UNO Q on the phone

1. Set `HOST` to the laptop address, e.g. `192.168.1.20:8080`.
2. Tap the `AI` chip to expand telemetry, then tap the `POSE` value to cycle `NPU → GPU → CPU → UNO Q`.
3. The full-screen UI now shows the board's camera feed (relayed JPEG preview); calibration, T-pose, aim, and practice swings all consume the board's 33-point pose. UNO Q mode calibrates its **own** kick threshold (`unoQKickMs`), separate from the phone-camera one.
4. In UNO Q mode the telemetry card's last rows show source/flow FPS, swing state, speeds, gates, and the final rejection reason; the same line hits Logcat under tag `UnoQKick`.

The TV's top-left telemetry card has an always-visible **UNO Q LINK** row. It is
driven by freshness of the raw UDP 9999 pose stream rather than by the optional
UNO Q/SnapKick WebSocket role, so a direct `sentinel_pose_streamer.py` session
correctly shows `LIVE`. Select **UNO Q** in that card to see board-wide CPU,
Adreno GPU utilization when the kernel exposes a KGSL/devfreq counter, the
actual pose backend, pose FPS, temperature, and memory use. `N/A` is intentional
for GPU utilization on kernels that do not export a readable counter; it is not
reported as a fabricated 0%. The sampler reads procfs/sysfs only once per second.

The same information is available as JSON at `/edge/status`, including
`poseAgeMs`, `cameraAgeMs`, and the normalized `telemetry` device record.

Tap POSE again to return to NPU. Unplugging the camera, stopping the board process, or losing UDP restores the local pipeline automatically after ~2.5 s.

## Performance notes

- Pose inference typically holds 8–10 FPS on the board CPU. Keep camera capture at 20–30 FPS so **optical flow** (pelvis-relative, confidence-gated, corrected by each new pose) can measure lower-body motion between pose anchors - that's what makes kick detection work at low pose rates. A/B it with `--no-optical-flow`.
- The detector cadence is adaptive: every frame during recovery, every 4th pose while tracking, every 6th with a stable high-confidence torso.
- `--dnn-target cpu` is intentional. On the tested UNO Q, direct graph benchmarks measured roughly 113 ms/99 ms for detector/landmark on CPU versus 3993 ms/3431 ms on OpenCL. OpenCL FP16 fell back to FP32, and the available block-quantized ONNX files do not load in OpenCV 4.10. Re-run [`tools/unoq_dnn_probe.py`](../tools/unoq_dnn_probe.py) after changing the board image, OpenCV, or driver.
- `--record-jsonl` captures every packet for deterministic offline replay:

  ```powershell
  python tools\replay_unoq_recording.py recordings\kicks.jsonl [--speed 0.5] [--no-timing]
  ```

  with the server running and `UNO Q` selected on the phone.
- If measured FPS is too low: stay at 640×480 with one person in frame, lower `--preview-fps` (reduces LAN/JPEG load without touching inference), and fall back to phone NPU mode for the demo if needed.

## Troubleshooting

| Symptom | Check |
|---|---|
| `edge/status` camera `waiting` | TCP 8080 reachable; `--laptop-ip` correct |
| pose `waiting`, camera `live` | UDP 9999 in Windows Firewall |
| `body=False` | Person-detector model path; full body in view |
| Camera opens but black | Another `/dev/videoN`; unrotated UVC device |
| Skeleton offset | Keep the camera unrotated - the app mirrors/center-crops video and landmarks together |
| `flow 0 fps` | `--optical-flow` on; camera producing frames faster than pose |
| `not in shoot` / `not framed` | Motion qualified but a gameplay gate rejected it - `soft` / `short` / `foot hidden` / `no speed` identify the lower-body gate |
| Instant fallback to phone | Select UNO Q only after host + board report `live` |
