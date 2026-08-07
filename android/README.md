# QPlay - native Android striker

Kotlin app (`com.sentinelmesh.gesturefootball`, display name **QPlay**) that turns a Snapdragon phone into the body-controlled striker for all three sports. It replaces the browser `phone.html` fallback.

## What it does

- **CameraX** front camera, camera-first layout; portrait, keep-screen-on.
- **Pose on the Hexagon NPU** via ONNX Runtime QNN (two-stage BlazePose from Qualcomm AI Hub, QAIRT 2.45 precompiled contexts in the APK). MediaPipe PoseLandmarker covers the GPU/CPU delegates.
- **Four pose sources** - tap the `AI` chip to expand the telemetry panel, then tap the **POSE badge** to cycle `NPU → GPU → CPU → UNO Q`. `UNO Q` consumes remote landmarks streamed by the edge board ([`../docs/unoq_pipeline.md`](../docs/unoq_pipeline.md)) and falls back to local pose automatically if the stream goes stale.
- **Three sports** - a sport picker (football / darts / basketball) runs on every launch. Football uses the ForcePose leg-swing engine (Newtons); darts and basketball use a wrist-release `HandThrowEngine`. Same kick wire format either way.
- **On-device calibration** → `player_profile.json` in app-private storage (never uploaded): biometrics → T-pose → aim L/C/R → 3 practice swings. Each step is gated on a thumbs-up hold (or "I'M READY" / voice "ready"). UNO Q mode calibrates its own separate kick threshold.
- **Anti-cheat:** 8-frame full-body streak before kicks can fire, 900 ms kick cooldown, person-switch buffer reset, and a predictability hint when you loop the same corner.
- **Whisper Tiny ASR on the Hexagon NPU** (weights pushed post-install, see below) + Android TTS; a **private coach** that works fully offline (grounded on your profile + recent kicks), upgradeable to a GenieX Qwen3 0.6B backend.
- **HOST** field → the laptop, normalized to `ws://<ip>:8080/ws`. Malformed input reverts to the default `ws://127.0.0.1:8080/ws`, so re-check the field if the connection surprises you.
- **NEURAL LOAD** strip (POSE / ASR / LLM ms) plus ~1 Hz `telem` scalars to the host's TV rail - no frames, transcripts, or profile data ever leave the phone.

## Build from scratch

Requirements: **JDK 17**, **Android SDK platform 35** (AGP 8.7.3 / Gradle 8.9 wrapper is committed), `adb`. Device: arm64-v8a, Android 9+ (minSdk 28); NPU paths need a Snapdragon with Hexagon HTP (`libcdsprpc.so`) - Galaxy S25 Ultra is the reference device.

```powershell
$env:JAVA_HOME = "<path-to-jdk-17>"
# SDK location - either:
$env:ANDROID_HOME = "<path-to-android-sdk>"
# or: Copy-Item local.properties.template local.properties  (then set sdk.dir inside)

cd android
.\gradlew.bat :app:assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

Unit tests (no device needed): `.\gradlew.bat :app:testDebugUnitTest` - see [`../docs/TESTING.md`](../docs/TESTING.md).

## Model push (after install)

The APK ships pose models, the MediaPipe fallback, and Whisper's tokenizer + mel filterbank (~14 MB total). The large weights are pushed over adb **from the repo root**:

```powershell
cd ..                                  # repo root - the scripts live in tools\
.\tools\push_whisper_models.ps1        # Whisper Tiny QNN (~112 MB) → app files/whisper/
.\tools\push_qwen_models.ps1 -Source <extracted_geniex_folder>   # optional Qwen coach (~752 MB) → files/qwen/
```

Then force-stop and relaunch the app. Voice badge: `VOICE · LISTENING` when Whisper is present, `VOICE · NO MODEL` otherwise (the game still plays). Without Qwen weights the coach runs its grounded on-device backend (`COACH` on the NEURAL LOAD strip).

## Play

1. Phone + laptop on the **same Wi-Fi / hotspot**; host running (`python server.py`).
2. HOST = `<laptop-ip>:8080` → tap **HOST** (pill turns green, TV PHONE LED lights).
3. Pick a sport, calibrate once if prompted, step back until **FULL BODY ✓**.
4. START MATCH on the TV - or say "ready" / hold a thumbs-up in the lobby.
5. Aim with a raised hand (or say left / center / right); kick or throw on **KICK!** (~380 N ≈ full-power reference for kicks).

### Indicative latency (Galaxy S25 Ultra, Snapdragon 8 Elite)

| Block | Delegate | Typical |
|---|---|---|
| Pose landmark | QNN HTP (NPU) | ~15–40 ms / frame (live badge) |
| Whisper Tiny encode+decode | QNN HTP | ~1–10 s / utterance (length-dependent) |
| Private coach line | grounded / Qwen | <50 ms grounded |

## 90-second demo script

1. Expand the `AI` chip → tap the POSE badge: NPU → GPU → CPU ms change live.
2. Calibrate once - "this profile never leaves the Snapdragon."
3. Full body → kick → ForcePose Newtons + zone/height on the TV.
4. Say "ready" / trash-talk → Whisper on the Hexagon → TTS answers.
5. Miss → private coach line.
6. Airplane mode → pose, ASR, and coach still work (the match link itself needs LAN, so reconnect for play).
7. Point at the **NEURAL LOAD** strip.

Deep dive (source map, ForcePose math, calibration internals, model inventory): [`../docs/README_GalaxyS25.md`](../docs/README_GalaxyS25.md). Wire protocol: [`../docs/phone_protocol.md`](../docs/phone_protocol.md).
