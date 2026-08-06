# Gesture Football — native Android striker

Kotlin app that replaces the browser `phone.html` on the Snapdragon phone (Player 1).

## What it does

- CameraX front camera (camera-first play layout)
- **Pose on Hexagon NPU** via ONNX Runtime QNN + AI Hub `pose_landmark_detector` — tap `DELEGATE` to cycle **NPU → GPU → CPU**
- MediaPipe PoseLandmarker for GPU/CPU delegates
- ForcePose engine (Newtons) + richer kick wire (`height` / `spin` / `strike` / `foot`)
- On-device calibration → `player_profile.json` (never uploaded)
- Anti-cheat: full-body frame streak required before kicks fire
- Predictability HUD when you loop the same corner
- Whisper Tiny ASR on Hexagon + Android TTS
- Private coach (GenieX Qwen when weights present; grounded on-device coach otherwise)
- `HOST` field → laptop IP (`ws://<ip>:8080/ws`)
- **NEURAL LOAD** strip: POSE / ASR / LLM ms

NPU pose assets: `app/src/main/assets/npu/`. Whisper weights are **not** in the APK.

### Measured on Galaxy S25 Ultra (Snapdragon 8 Elite)

| Block | Delegate | Typical |
|---|---|---|
| Pose landmark | QNN HTP (NPU) | ~15–40 ms / frame (live badge) |
| Whisper Tiny encode+decode | QNN HTP | ~1–10 s / utterance (length-dependent) |
| Private coach line | on-device grounded / Qwen | &lt;50 ms grounded; GenieX when pushed |

Use QUAD `/quad-profile` for formal power numbers if available on the bench.

### Whisper
```powershell
.\tools\push_whisper_models.ps1
```
Internal `files/whisper/` via `run-as`. Badge: `VOICE · LISTENING`.

### Qwen / coach weights (optional)
```powershell
.\tools\push_qwen_models.ps1 -Source <extracted_geniex_folder>
```
Without weights the app still coaches offline from profile + kick memory (`COACH` backend on NEURAL LOAD).

### Calibration
First launch (or **CALIBRATE**): height/weight → T-pose → aim L/C/R → practice swing → **PLAY**.

### Host
Type laptop address (e.g. `192.168.1.20:8080`) → **HOST**. Prefs persist. Protocol: [`docs/phone_protocol.md`](../docs/phone_protocol.md).

## Build

```powershell
$env:JAVA_HOME = "C:\Users\prate\android-dev\jdk\jdk-17.0.20+8"
$env:ANDROID_HOME = "C:\Users\prate\android-dev\sdk"
cd android
.\gradlew.bat :app:assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

## 90s demo script

1. Show `DELEGATE · NPU` → tap → GPU → CPU → numbers change  
2. Calibrate once → “profile never leaves this Snapdragon”  
3. Full body → kick → ForcePose Newtons + zone/height  
4. Say “ready” / trash-talk → Whisper NPU → TTS  
5. Miss → private coach line  
6. Airplane mode → still coaches  
7. Point at **NEURAL LOAD** strip  
