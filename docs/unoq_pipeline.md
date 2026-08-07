# Optional UNO Q pose pipeline

This pipeline moves USB-camera pose inference to the Arduino UNO Q while
preserving the native phone interface and the original phone NPU/GPU/CPU
pipeline. The original local pipeline remains the default.

## Data path

```text
USB webcam -> UNO Q OpenCV/BlazePose
                  | UDP full 33-point pose :9999
                  | HTTP JPEG POST /edge/frame
                  v
             Sentinel laptop :8080
                  | existing WebSocket + /edge/frame.jpg
                  v
             unchanged Android UI
```

The phone still owns the existing calibration, ForcePose calculation, match
protocol, voice coach, and private player profile. Only camera and pose-model
execution move to the UNO Q. If edge packets stop for 2.5 seconds, the app
automatically restores local inference and the local phone camera.

## Prerequisites

- The laptop, phone, and UNO Q must be on the same LAN.
- Allow inbound TCP `8080` and UDP `9999` on the laptop firewall.
- The UNO Q must already contain the SnapKick board checkout at
  `/home/arduino/snapkick-starter/unoq`. Sentinel reuses its tested OpenCV Zoo
  MediaPipe backend and vendor preprocessing.
- Keep these float models on the UNO Q:
  - `/home/arduino/models/opencv-mediapipe/pose_estimation_mediapipe_2023mar.onnx`
  - `/home/arduino/models/opencv-mediapipe/person_detection_mediapipe_2023mar.onnx`

The current backend is OpenCV DNN on the UNO Q CPU, not QNN/NPU.

## 1. Start the Sentinel laptop host

From the SentinelMesh repository:

```powershell
python -m pip install -r requirements.txt
python server.py
```

Check the new relay before starting the board:

```text
http://127.0.0.1:8080/edge/status
```

Both camera and pose should initially report `waiting`.

## 2. Copy the board wrapper

From PowerShell in the SentinelMesh repository:

```powershell
.\tools\deploy_unoq.ps1 -HostAddress 192.168.150.72
```

Or copy `unoq/sentinel_pose_streamer.py` and `unoq/requirements.txt` to
`/home/arduino/sentinelmesh` manually.

## 3. Identify the USB camera

On the UNO Q:

```bash
v4l2-ctl --list-devices
ls -l /dev/video*
```

Use the real UVC capture device, not a Qualcomm Venus codec device. The
examples below use `/dev/video0`.

## 4. Prepare the board environment

The launcher prefers the SnapKick venv when it exists and otherwise falls back
to the board's `python3`. On the currently deployed board the venv is absent,
so the tested runtime is system Python 3.13 with OpenCV 4.10:

```bash
PY=/home/arduino/snapkick-starter/unoq/.venv/bin/python
if [ ! -x "$PY" ]; then PY=python3; fi
"$PY" -c 'import cv2, numpy; print(cv2.__version__)'
```

Do not install the MediaPipe Python wheel on the current Python 3.13 board
image. This path uses OpenCV DNN and the OpenCV Zoo ONNX models.

## 5. Run direct USB-camera inference

Replace `LAPTOP_IP` with the laptop LAN address:

```bash
PY=/home/arduino/snapkick-starter/unoq/.venv/bin/python
if [ ! -x "$PY" ]; then PY=python3; fi
"$PY" /home/arduino/sentinelmesh/sentinel_pose_streamer.py \
  --laptop-ip LAPTOP_IP \
  --camera /dev/video0 \
  --model /home/arduino/models/opencv-mediapipe/pose_estimation_mediapipe_2023mar.onnx \
  --person-model /home/arduino/models/opencv-mediapipe/person_detection_mediapipe_2023mar.onnx \
  --width 640 \
  --height 480 \
  --camera-fps 30 \
  --target-fps 10 \
  --preview-fps 5 \
  --detector-interval 4 \
  --detector-stable-interval 6 \
  --detector-recovery-interval 1 \
  --optical-flow \
  --record-jsonl /home/arduino/sentinelmesh/recordings/kicks.jsonl
```

The terminal reports measured `pose_fps`, `infer_ms`, whether a body is
present, and UDP packet size. Requested FPS is only a cap; measured FPS is the
number that matters.

Open these on the laptop to verify transport:

- `http://127.0.0.1:8080/edge/status`
- `http://127.0.0.1:8080/edge/camera.mjpg`

### Temporary laptop USB-camera input

When the USB camera is connected to Windows instead of the UNO Q, keep the
Sentinel laptop server on port `8080` and run the existing SnapKick relay
script against Sentinel's dedicated source endpoint:

```powershell
cd C:\Users\parth\Desktop\snap-kick\snapkick-starter
$env:OPENCV_VIDEOIO_PRIORITY_MSMF="0"
.\laptop\.venv\Scripts\python.exe unoq\camera_relay.py `
  --camera 1 `
  --url http://127.0.0.1:8080/edge/source/frame `
  --width 640 `
  --height 480 `
  --fps 30
```

Use `--camera 0` if needed. Do not start the SnapKick laptop server; its
current local version does not provide the documented camera routes and also
competes with Sentinel for UDP port `9999`.

Keep this foreground terminal open. Disabling OpenCV's Media Foundation
backend selects DirectShow, which avoids a Windows UVC reopen stall observed
with the Brio 101 after a relay process was force-stopped.

On the UNO Q, replace the direct `/dev/video0` camera argument with:

```bash
--camera http://LAPTOP_IP:8080/edge/source/camera.mjpg
```

Verify the input before starting inference:

- `http://127.0.0.1:8080/edge/source/camera.mjpg`
- `http://127.0.0.1:8080/edge/status` (`sourceCamera` should be `live`)

Start components in this order: Sentinel laptop server, camera relay, then
the UNO Q streamer. If the laptop server is restarted while the board is
reading its MJPEG source, restart the UNO Q streamer too; OpenCV's network
`VideoCapture` does not reliably reopen a broken MJPEG connection.

## 6. Select UNO Q in the phone app

1. Build/install the Android app normally.
2. Set `HOST` to the Sentinel laptop address, for example
   `192.168.1.20:8080`.
3. Tap the `AI` chip to expand telemetry.
4. Tap the `POSE` value to cycle `NPU -> GPU -> CPU -> UNO Q`.
5. The same full-screen camera UI and overlay now show the USB-camera feed.
6. Run the existing calibration flow. T-pose, aim, body guide, and practice
   swings consume the UNO Q's full 33-point pose.

Tap the compact `AI` chip to expand the existing telemetry card. In UNO Q
mode its last rows show source/flow FPS, timestamp spacing, selected foot and
swing state, raw/filtered/signal speed, the saved threshold, visibility,
path/lift/knee extension, body streak, phase, and the final rejection reason.
The same line is emitted to Android Logcat under tag `UnoQKick` every 300 ms.

Tap the pose value again to return to NPU. Unplugging the camera, stopping the
board process, losing UDP, or losing the host automatically restores the local
pipeline after approximately 2.5 seconds.

## Performance gate and tuning

The pose model may remain near 8-10 FPS. Keep camera capture at 20-30 FPS so
Lucas-Kanade optical flow can measure lower-body motion between pose anchors.
Optical flow is pelvis-relative, confidence-gated, and corrected by every new
MediaPipe pose. Disable it for an A/B check with `--no-optical-flow`.

The detector cadence is adaptive when the imported SnapKick backend exposes
its detector-interval field: recovery uses every frame, normal tracking uses
every fourth pose, and a stable high-confidence torso uses every sixth pose.

`--record-jsonl PATH` is optional. It records each raw pose packet, diagnostics,
and optical-flow summary for deterministic offline replay/tuning. Stop the
streamer before copying the file so the final line is flushed.

Replay a copied recording through the real laptop/WebSocket/phone detector:

```powershell
python tools\replay_unoq_recording.py recordings\kicks.jsonl
```

Keep the Sentinel laptop server running and select `UNO Q` on the phone. Use
`--speed 0.5` for slow motion or `--no-timing` for a transport stress test.

If measured pose FPS is too low:

1. Keep `--width 640 --height 480` and one person.
2. Compare `flow fps` in the phone telemetry against pose FPS; flow should be
   materially higher when the camera really supplies 20-30 FPS.
3. Reduce `--target-fps`; this does not accelerate inference but makes logs
   and thermal behavior more predictable.
4. Keep preview and inference independent; lowering `--preview-fps` reduces
   LAN/JPEG load without changing pose inference.
5. Use the original phone NPU mode for the demo if the board cannot sustain
   the required rate.

## Troubleshooting

- `edge/status` camera waiting: check TCP 8080 and the `--laptop-ip` value.
- Pose waiting but camera live: check UDP 9999 in Windows Firewall.
- `body=False`: verify the person detector model and fit the full body in view.
- Camera opens but is black: try another `/dev/videoN` device.
- Skeleton is offset: keep the USB camera unrotated; the app mirrors and
  center-crops both the video and landmark coordinates together.
- `flow 0 fps`: confirm `--optical-flow` is enabled and the camera source is
  producing frames faster than pose inference.
- `not in shoot` / `not framed`: the motion qualified, but a gameplay gate
  rejected it. `soft`, `short`, `foot hidden`, and `no speed` identify the
  lower-body gate directly.
- Immediate phone fallback: select UNO Q only after the host and board report
  live status.
