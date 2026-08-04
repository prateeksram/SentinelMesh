# Gesture Football — native Android striker

Kotlin app that replaces the browser `phone.html` on the Snapdragon phone.

## What it does

- CameraX front camera
- MediaPipe PoseLandmarker (GPU delegate in Phase 1)
- ForcePose engine (Newtons) — same math as the browser
- Hand aim + leg-kick → WebSocket JSON identical to `phone.html`
- Bullet-time skeleton frames for the TV orbit replay

Phase 2 will load the Hexagon QNN pose binaries already on the phone at
`~/gf/models/`.

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
