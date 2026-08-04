# Gesture Football — native Android striker

Kotlin app that replaces the browser `phone.html` on the Snapdragon phone.

## What it does

- CameraX front camera
- MediaPipe PoseLandmarker (**GPU fallback**)
- **Hexagon NPU pose** via ONNX Runtime QNN + AI Hub `pose_landmark_detector` (8 Elite for Galaxy) — badge `DELEGATE · NPU · xx ms`
- ForcePose engine (Newtons) — same math as the browser
- **On-device calibration** → `player_profile.json` (height/weight, T-pose torso scale, aim envelope, practice-swing kick threshold, dominant foot)
- Hand aim + leg-kick → WebSocket JSON identical to `phone.html`
- Bullet-time skeleton frames for the TV orbit replay

NPU assets live in `app/src/main/assets/npu/` (copied from `~/gf/models` AI Hub bundle). If QNN/HTP fails to load, the app falls back to MediaPipe GPU automatically.

**Notes:** AI Hub landmark net exposes 25 BlazePose points (face→hips); ankles/feet are synthesised from hips + ROI so ForcePose still runs. Requires `libcdsprpc.so` (`uses-native-library`) and `extractNativeLibs` so Hexagon can mmap `libQnnHtpV79Skel.so`.

### Calibration flow
First launch (or tap **CALIBRATE**): height/weight → T-pose hold → aim L/C/R holds → practice swing → **PLAY**. Profile never leaves the phone.

## Build (this machine)

```powershell
$env:JAVA_HOME = (Get-ChildItem "$env:USERPROFILE\android-dev\jdk" -Directory | Select-Object -First 1).FullName
$env:ANDROID_HOME = "$env:USERPROFILE\android-dev\sdk"
cd android
.\gradlew.bat :app:assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

Server must be running on the phone (`Termux`: `python ~/gf/laptop/server.py`).
The app connects to `ws://127.0.0.1:8080/ws` (same-device localhost).
