# README_GalaxyS25 — Phone Side (Player 1)

**Device:** Samsung Galaxy S25 Ultra · Snapdragon 8 Elite (`sm8750-ac`, Hexagon HTP v79)  
**App:** Gesture Football · `com.sentinelmesh.gesturefootball` · `v0.1.0` (`versionCode` 1)  
**ABI / SDK:** `arm64-v8a` only · `minSdk` 28 · `targetSdk` / `compileSdk` 35  
**Branch intent:** `prateek` — native edge-AI striker, not the match host  

This document covers **only the phone**. The laptop host / TV stadium (`laptop/server.py`, `tv.html`) and Arduino Player 2 are out of scope except where the phone talks to them over Wi‑Fi.

---

## 1. What this phone is (and is not)

| Role | Detail |
|---|---|
| **Is** | Player 1 edge station: sees you, measures kick force in Newtons, hears you, coaches you privately, aims + shoots over a tiny WebSocket |
| **Is not** | Match host, TV renderer, or cloud AI client for video |
| **Host** | Laptop on the same Wi‑Fi / hotspot (`ws://<laptop-ip>:8080/ws`) |
| **Wire** | JSON only: `hello`, `aim`, `kick`, `skel`, `start` — **no camera frames** |

**Pitch line:** Snapdragon hears you, measures your kick in Newtons, coaches you privately, and only sends a tiny JSON shot to the TV.

---

## 2. End-to-end data flow

```
Front camera (CameraX RGBA)
        │
        ▼
 PoseAnalyzer ──► NPU (ONNX Runtime QNN HTP)  or  GPU/CPU (MediaPipe)
        │              pose_landmark_detector / pose_landmarker_lite.task
        ▼
 BodyGuide (full-body / torso / T-pose gates)
        │
        ├─► Aim (raised wrist → L / C / R) ──► GameClient.sendAim  (~200 ms)
        │
        └─► ForcePoseEngine (leg swing → Newtons) ──► KickEvent
                 │
                 ├─► GameClient.sendKick  {zone,power,force,dirDeg,height,spin,strike,foot}
                 └─► skeleton buffer ──► GameClient.sendSkeleton  (bullet-time for TV)

Mic ──► VoiceListener ──► WhisperEngine (Hexagon) ──► VoiceCoach intents
                                                      └─► QwenCoach (private TTS)

Calibration ──► player_profile.json  (filesDir — never uploaded)
```

---

## 3. Source map

Package root:

`android/app/src/main/java/com/sentinelmesh/gesturefootball/`

| Path | Responsibility |
|---|---|
| `MainActivity.kt` | CameraX lifecycle, HUD, HOST prefs, calibration UI, kick/skel send, voice wiring, **NEURAL LOAD**, **DELEGATE**, anti-predictability hint, TTS mute during coach |
| `pose/PoseAnalyzer.kt` | Delegate cycle **NPU → GPU → CPU**; landmarks → aim + ForcePose; `BODY_OK_FRAMES` anti-cheat; profile aim thresholds |
| `pose/NpuPoseEngine.kt` | Two-stage BlazePose on QNN HTP (detector 128² + landmark 256²); synthesises ankles/feet to MediaPipe-33 layout |
| `pose/BodyGuide.kt` | Stand-here silhouette, torso/full-body acceptance, loose T-pose + corrective coach strings |
| `forcepose/ForcePoseEngine.kt` | ForcePose port: median → Savitzky–Golay → torso metres → `F = m_leg × a_peak` → kick validation → `KickEvent` |
| `calibrate/CalibrationSession.kt` | Conversational FSM: biometrics → T-pose → aim L/C/R → 3 practice swings → done |
| `profile/PlayerProfile.kt` | Profile schema + `PlayerProfileStore` → `filesDir/player_profile.json` |
| `voice/WhisperEngine.kt` | Whisper-Tiny encoder/decoder on HTP from `files/whisper/` |
| `voice/MelSpectrogram.kt` | 80-bin Slaney mel @ 16 kHz → 80×3000 |
| `voice/WhisperTokenizer.kt` | HF `tokenizer.json` decode |
| `voice/VoiceListener.kt` | Energy-gated mic capture → ASR |
| `voice/VoiceCoach.kt` | Intent parse (READY / LEFT / CENTER / RIGHT / NEXT / SKIP / TRASH) + Android TTS |
| `voice/QwenCoach.kt` | Private coach grounded on profile + recent kicks; optional GenieX Qwen backend |
| `npu/HtpNative.kt` | `ADSP_LIBRARY_PATH`, QNN HTP session (`burst` then `high_performance`) |
| `net/GameClient.kt` | OkHttp WebSocket client, URL normalize/validate, prefs, reconnect |
| `ui/OverlayView.kt` | Mirrored skeleton overlay + stand-here guide |
| `debug/ScreenRecordService.kt` | MediaProjection REC → `Movies/gf_screen_*.mp4` |

Resources of note:

- `res/layout/activity_main.xml` — camera-first HUD, HOST row, neural load, calibrate
- `res/layout/include_calibration.xml` — biometrics + progress
- `res/xml/network_security_config.xml` — cleartext OK for LAN `ws://`
- `AndroidManifest.xml` — permissions + required `libcdsprpc.so` for Hexagon RPC

---

## 4. Capabilities (what the phone holds)

### 4.1 Vision — sees you

- **CameraX** front camera (`DEFAULT_FRONT_CAMERA`), portrait, keep-screen-on, RGBA image analysis.
- **Hexagon NPU pose (default):** Qualcomm AI Hub `mediapipe_pose` / BlazePose-style stack via ONNX Runtime **QNN HTP**.
- **Fallback:** MediaPipe Tasks Vision PoseLandmarker (`pose_landmarker_lite.task`) on **GPU** or **CPU**.
- Tap **DELEGATE** (badge) to cycle **NPU → GPU → CPU**. Live inference ms updates on the badge.
- Overlay draws mirrored landmarks so the player mirrors the phone like a mirror.

### 4.2 Identity — knows you (local only)

One-time **calibration** writes `filesDir/player_profile.json`:

| Field | Meaning |
|---|---|
| `heightCm` / `weightKg` | Biometrics; leg mass for ForcePose |
| `torsoM` | Mid-shoulder → mid-hip length in metres (`heightM × TORSO_FRAC`, `TORSO_FRAC = 0.288`) refined by T-pose |
| `kickMs` | Personal minimum foot speed (m/s) to count a kick |
| `dominantFoot` | `L` / `R` from practice swings |
| `aimLMax`, `aimCMin`, `aimCMax`, `aimRMin` | Mirrored wrist-X envelope for L/C/R |
| `calibratedAt` | Epoch ms |

**Never uploaded.** Profile stays on the Snapdragon.

### 4.3 Biomechanics — measures you (ForcePose)

On-device port of [ForcePose (arXiv:2503.22363)](https://arxiv.org/abs/2503.22363). See §6 for the full pipeline.

Kick JSON extras beyond classic zone/power:

| Field | Type | Notes |
|---|---|---|
| `force` | int | Newtons |
| `power` | float | `min(1, forceN / fMax)`, `fMax = 380` |
| `dirDeg` | int | Launch elevation cue |
| `height` | `H` \| `L` | High vs low band |
| `spin` | float | ∈ [−1, 1] lateral cue |
| `strike` | `chip` \| `drive` | Trajectory class |
| `foot` | `L` \| `R` | Swinging foot |

### 4.4 Hearing — Whisper on Hexagon

- **Whisper Tiny** QNN context binaries (**pushed**, not in APK — see §5).
- Commands / phrases: “ready”, left / center / right, next / skip (calib), trash-talk.
- Badge: `VOICE · LISTENING` when model present; `VOICE · NO MODEL` otherwise.

### 4.5 Talking back — TTS + private coach

- **Android TTS** for commands, match announce / countdown / goal / miss, and coach lines.
- **Private coach** always offline:
  - Default backend label **`COACH`**: grounded lines from profile + recent kick memory (no LLM weights required).
  - Optional **`QWEN`**: GenieX Qwen3 0.6B w4a16 weights pushed to `files/qwen/` (~752 MB).

### 4.6 Integrity — demo protection

- **Full-body streak:** **`BODY_OK_FRAMES = 8`** consecutive frames with shoulders **and** ankles inside the stand-here guide before kicks fire (anti close-up cheat).
- **Person jump:** large tracker discontinuity clears foot history so stale motion cannot invent a kick.
- **Kick cooldown:** ≥ **900 ms** between accepted kicks.
- **Predictability HUD:** if the last four distinct aim-zone changes are the same corner → “Mix it up — stop looping …”.
- Calibration practice requires real leg swings; soft/short swings are rejected with coach hints.

### 4.7 Networking — HOST

- In-app **HOST** field: laptop address, e.g. `10.73.51.224:8080`.
- Normalized to `ws://<host>:8080/ws`.
- Prefs: `SharedPreferences` name `gf_net`, key `host_url`.
- Pill states: Offline / Connecting / Connected / Failed.
- Do **not** use `localhost` unless the server is on the phone itself.

### 4.8 Telemetry HUD — NEURAL LOAD

On-device strip (not on the wire):

- **POSE** ms (current delegate)
- **ASR** ms (last Whisper utterance)
- **LLM** ms · backend (`COACH` / `QWEN`)
- Live ForcePose force chip during shoot / calib

### 4.9 Debug — REC

Optional screen capture via `ScreenRecordService` (MediaProjection FGS) → `Movies/gf_screen_*.mp4`.

---

## 5. Models & on-device storage

### 5.1 Bundled in the APK (`android/app/src/main/assets/`)

| Asset | Role |
|---|---|
| `npu/pose_detector.onnx` | Stage-1 person detector, **128×128 uint8**, w8a8 |
| `npu/pose_detector_qairt_context.bin` | Precompiled QAIRT context for detector (~1.3 MB class) |
| `npu/pose_landmark_detector.onnx` | Stage-2 landmarks, **256×256** |
| `npu/pose_landmark_detector_qairt_context.bin` | Precompiled landmark context (~4.2 MB class) |
| `npu/anchors_pose.bin` | 896 BlazePose anchors (hub copy also present) |
| `npu/metadata.json` | AI Hub export metadata: `mediapipe_pose`, `precompiled_qnn_onnx`, precision **w8a8**, QAIRT **2.45**, Galaxy / 8 Elite target |
| `pose_landmarker_lite.task` | MediaPipe PoseLandmarker lite (~5.8 MB) for GPU/CPU delegates |
| `whisper/tokenizer.json` | HF tokenizer for Whisper decode (weights still pushed) |
| `whisper/mel_filters.bin` | Slaney mel filterbank |

On first NPU use, runtime copies NPU assets into **`filesDir/npu/`** (`NpuPoseEngine.create`).

### 5.2 Pushed after install (not in git — large)

| Bundle | Script | On-device path | Approx size | Runtime |
|---|---|---|---|---|
| Whisper Tiny QNN | `tools/push_whisper_models.ps1` | `files/whisper/` via `run-as` | ~112–113 MB | QAIRT context + ONNX on HTP |
| Qwen3 0.6B GenieX | `tools/push_qwen_models.ps1 -Source <dir>` | `files/qwen/` | ~752 MB | GenieX QAIRT w4a16 (optional) |

Whisper push stages under `/data/local/tmp/gf_whisper`, then:

`run-as com.sentinelmesh.gesturefootball` → `files/whisper/`

Expected Whisper files:

- `encoder.onnx`, `encoder_qairt_context.bin`
- `decoder.onnx`, `decoder_qairt_context.bin`
- `metadata.json`, `tokenizer.json`

Default staging folder on the laptop: `%TEMP%\gf_whisper_npu`.

### 5.3 Runtimes (Gradle)

| Dependency | Use |
|---|---|
| `onnxruntime-android-qnn` (e.g. 1.28.0) | ORT + QNN Execution Provider |
| `qnn-runtime` (e.g. 2.45.0) | Hexagon HTP skel / EP |
| MediaPipe Tasks Vision `0.10.14` | GPU/CPU PoseLandmarker |
| OkHttp | WebSocket |
| CameraX | Capture + analysis |

Manifest requires vendor native library:

```xml
<uses-native-library android:name="libcdsprpc.so" android:required="true" />
```

`HtpNative` sets `ADSP_LIBRARY_PATH` and opens QNN sessions with performance profile **`burst`**, falling back toward **`high_performance`**.

### 5.4 Indicative latency (Galaxy S25 Ultra)

| Block | Delegate | Typical |
|---|---|---|
| Pose landmark | QNN HTP | ~15–40 ms / frame (live badge) |
| Whisper Tiny encode+decode | QNN HTP | ~1–10 s / utterance (length-dependent) |
| Private coach | grounded / Qwen | &lt;50 ms grounded; GenieX when pushed |

---

## 6. ForcePose — full technical detail

**Source:** `forcepose/ForcePoseEngine.kt`  
**Paper:** [ForcePose: Estimating Force from Pose (arXiv:2503.22363)](https://arxiv.org/abs/2503.22363)  
**Nature of this port:** Pose-only. Paper BiLSTM weights are **not public**; we use Winter-style leg mass and peak acceleration from smoothed foot kinematics.

### 6.1 Physical model

\[
m_{\mathrm{leg}} = 0.0618 \times m_{\mathrm{body}},\qquad
F = m_{\mathrm{leg}} \times a_{\mathrm{peak}}
\]

- Default ctor: `bodyKg = 70`, `torsoM = 0.52`, `kickMs = 3.0`, `fMax = 380` N (reference “full power”).
- After calibration, `bodyKg`, `torsoM`, and `kickMs` come from `PlayerProfile`.

### 6.2 Signal pipeline (per foot)

1. **Joint tracks** for ankles / feet (MediaPipe indices 27/28/31/32). Skip sample if visibility `< 0.4`.
2. **3-tap median** on raw `(x, y)` into a ring buffer (max length 12).
3. Require ≥ **9** buffered samples.
4. **Savitzky–Golay** smoothing with coefficients  
   `[-2, 3, 6, 7, 6, 3, -2] / 21` over ±3 neighbors.
5. **Torso scale:**  
   `torsoLen = hypot(shoulderMid − hipMid)`  
   EMA `torsoEma ← 0.9·torsoEma + 0.1·torsoLen` (reject if `torsoLen < 0.05`).  
   `mPerUnit = torsoM / torsoEma` converts normalized image units → metres.
6. Differentiated **velocity** `(vx, vy)` and **acceleration**;  
   `speed = hypot(vx, vy)`, `force = legKg * accel`.
7. Track **`swingPeak`** = max force during the swing.

### 6.3 Kick acceptance

A kick fires only when the caller sets `canKick` (match `phase == "shoot"` **or** `calibrationSwing`) **and**:

| Gate | Rule |
|---|---|
| Speed | `speed > kickMs` for **`SWING_FRAMES = 3`** consecutive frames (~100 ms) |
| Path (“short”) | Must show real travel: **lift** (`restY − y > 0.04 m`) **OR** **forward** (`|vx| > 1.2·|vy|`) **OR** displacement `≥ 0.18 m`. Else `lastReject = "short"` |
| Soft near-miss | `speed > kickMs * 0.55` for 3 frames without full accept → `lastReject = "soft"` |

**Important:** Wrists are **ignored** during kick validation so aim/balance arm motion cannot veto a legitimate swing.

### 6.4 Threshold floors

| Constant | Value | Meaning |
|---|---|---|
| `FLOOR_MS` | `1.7f` | Hard floor for saved match `kickMs` |
| `PRACTICE_FLOOR_MS` | `1.5f` | Floor while measuring practice swings |
| Default `kickMs` | `3.0f` | Pre-calibration |
| Practice personalization | `max(FLOOR_MS, medianPeakSpeed * 0.55)` | From 3 validated swings |

### 6.5 `KickEvent` enrichment

After a valid swing:

- `forceN = round(min(fMax * 1.5, max(swingPeak, force)))`
- `power = min(1, forceN / fMax)`
- `foot` = side with greater peak contribution
- `height` = `H` / `L` from foot elevation band
- `spin` ∈ [−1, 1] from lateral velocity cue
- `strike` = `chip` if strongly upward (`vy < −2.0` and `|vx| < 0.55·speed`), else `drive`
- `dirDeg` = elevation angle cue for TV ball arc
- `zone` = current aim L/C/R (hand), not the foot

HUD coaching strings:

- soft → “Harder — swing through the ball”
- short → “Follow through — kick the ball”

### 6.6 Calibration vs match

- Practice swings may temporarily lower the detection floor (`PRACTICE_FLOOR_MS`) so the phone can measure the player’s natural kick.
- Match play uses the saved `kickMs` (≥ `FLOOR_MS`) plus full-body streak.

---

## 7. Calibration — step by step

**Source:** `calibrate/CalibrationSession.kt`  
**Entry:** first launch without profile, or **CALIBRATE** (blocked mid-match).

| Step | Player action | Capture rules |
|---|---|---|
| `BIOMETRICS` | Enter height (120–230 cm) & weight (35–160 kg); say **next** / tap | Seeds `torsoM = heightM × 0.288` |
| `TPOSE` | Say **ready**, then hold arms out | Hold **`TPOSE_HOLD_MS = 2500` ms**; refine torso from pose; READY gate required |
| `AIM_L` / `AIM_C` / `AIM_R` | Say **ready**, raise hand above shoulder into zone | Hold **`AIM_HOLD_MS = 1500` ms**; wrist above mid-shoulder; must leave zone ≥300 ms before re-arm; personalizes aim thresholds |
| `PRACTICE` | Say **ready**, then **3** hard leg swings | Validated ForcePose kicks; median peak → `kickMs`; majority foot → `dominantFoot` |
| `DONE` | Persist profile → play | `PlayerProfileStore.save` |

Default aim envelope before personalization:

- L: `aimLMax = 0.34`
- C: `aimCMin = 0.40`, `aimCMax = 0.60`
- R: `aimRMin = 0.66`

Voice gates (**READY** / **NEXT** / **SKIP**) prevent accidental auto-accept of a wrong pose. Coach tells the player exactly what to fix until the hold fills.

**Ball note:** ForcePose is **pose-only**. A real football, tennis ball, or air-kick all work; the camera tracks the **leg**, not the ball. UI copy may say “kick the real ball” to encourage a full follow-through.

---

## 8. Pose → aim → kick (runtime loop)

Landmark indices used throughout (MediaPipe 33-body):

| Joints | Indices |
|---|---|
| Shoulders | 11, 12 |
| Wrists | 15, 16 |
| Hips | 23, 24 |
| Ankles | 27, 28 |
| Feet | 31, 32 |

**Aim**

1. Choose highest visible wrist (`vis > 0.4`) above hip.
2. Mirror X: `wx = 1 − x` so the player’s left is L on screen.
3. Map through profile thresholds with hysteresis → zone `L` | `C` | `R`.
4. `GameClient.sendAim` throttled (~200 ms) during live phases.

**Kick arming**

```
canKick = (phase == "shoot" || calibrationSwing)
       && (calibrationSwing || bodyOkStreak >= BODY_OK_FRAMES)  // BODY_OK_FRAMES = 8
       && (now - lastKickAt > 900)
```

**Skeleton for TV**

After a kick, recent pose frames (~1.4 s window) are downsampled and sent as:

```json
{ "type": "skel", "kick": <n>, "frames": [ { "t": <ms relative>, "p": [[x,y,z], ...] }, ... ] }
```

Used by the laptop TV for bullet-time orbit replay. Still **not** video — landmark samples only.

---

## 9. WebSocket protocol (phone ↔ laptop)

**Canonical doc:** [`docs/phone_protocol.md`](docs/phone_protocol.md)  
**Default URL:** `ws://<host>:8080/ws`  
**Client:** OkHttp · connect timeout 5 s · ping 20 s · auto-reconnect ~1.5 s

### 9.1 Client → server

**`hello`**
```json
{ "type": "hello", "client": "phone" }
```

**`aim`**
```json
{ "type": "aim", "zone": "L" }
```
`zone` ∈ `L` | `C` | `R`

**`kick`**
```json
{
  "type": "kick",
  "zone": "L",
  "power": 0.82,
  "force": 210,
  "dirDeg": 12,
  "height": "H",
  "spin": -0.35,
  "strike": "drive",
  "foot": "R"
}
```

**`skel`** — see §8.

**`start`** — spoken READY in lobby while connected can start the match from the phone.

### 9.2 Server → client

**`state`** drives HUD phase machine:

`lobby` | `announce` | `countdown` | `shoot` | `resolve` | `end`

Includes `kick`, `kicksTotal`, `score`, `line`, `timerMs`, `last` (force / result), etc.

### 9.3 HOST URL rules (`GameClient`)

- Accepts `10.0.0.1:8080`, `ws://…`, etc.
- Normalizes to `ws://host:port/ws`
- Rejects mangled prefs (glued IPs, missing host, bad port)
- Prefs key `host_url` under `gf_net`

---

## 10. Voice stack detail

| Stage | Implementation |
|---|---|
| Capture | 16 kHz mono PCM; start RMS ≈ **600**, stop RMS ≈ **280**; end on ~500 ms silence or **4 s** max; muted while TTS speaks |
| Features | `MelSpectrogram` → 80×3000 FP features |
| ASR | Whisper Tiny encoder/decoder on HTP; prompt tokens SOT / EN / TRANSCRIBE / NO_TIMESTAMPS; max ~32 new tokens |
| Intents (`VoiceCoach`) | `READY`, `LEFT`, `CENTER`, `RIGHT`, `NEXT`, `SKIP`, `TRASH` |
| Match READY | If lobby + HOST connected → `sendStart()`; else acknowledge + optional coach line |
| Aim by voice | LEFT / CENTER / RIGHT inject aim zone like the hand |
| Coach (`QwenCoach`) | Keeps ≤8 recent kicks; prefers Genie `files/qwen` if present; else grounded one-liners; backend badge `QWEN` / `COACH` |

Without Whisper weights the app still plays; voice badge shows **NO MODEL**. Push models and force-stop + relaunch.

---

## 11. Permissions & platform requirements

From `AndroidManifest.xml`:

| Permission / feature | Why |
|---|---|
| `CAMERA` + front camera required | Pose |
| `RECORD_AUDIO` | Whisper |
| `INTERNET` | WebSocket to laptop |
| `VIBRATE` | Haptics on events |
| `WAKE_LOCK` | Keep session alive |
| `FOREGROUND_SERVICE` + `MEDIA_PROJECTION` | Debug REC |
| `libcdsprpc.so` required | Hexagon HTP / QNN rpcmem (Samsung vendor) |
| `usesCleartextTraffic` + network security config | LAN `ws://` without TLS |

Activity: portrait, `keepScreenOn`, exported launcher `MainActivity`.

---

## 12. Build, install, model push

Paths in repo docs may reference another machine’s JDK/SDK; on this laptop use your local Android Studio / SDK locations.

```powershell
cd C:\Users\qc_de\SentinelMesh\android
.\gradlew.bat :app:assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk

# Optional — ASR
..\tools\push_whisper_models.ps1

# Optional — GenieX coach weights
..\tools\push_qwen_models.ps1 -Source <extracted_geniex_folder>
```

**Play checklist**

1. Phone + laptop on the **same Wi‑Fi / hotspot**.
2. Laptop: `python laptop/server.py` → open TV.
3. Phone: open Gesture Football → grant camera + mic.
4. HOST = laptop LAN IP `:8080` → tap **HOST** (green pill / TV PHONE LED).
5. Calibrate once if prompted → step back until **FULL BODY ✓**.
6. TV → **START MATCH** (or say “ready” in lobby).

---

## 13. How to play (phone UX)

| Action | How |
|---|---|
| Aim | Raise a hand → L / C / R (or say left / center / right) |
| Kick | On **KICK!**, swing the kicking leg through — ~380 N ≈ full power reference |
| Feint | Hold a fake corner, switch late, then swing |
| Voice | “ready”, trash-talk → private TTS coach |
| Delegate demo | Tap DELEGATE — watch POSE ms jump NPU ↔ GPU ↔ CPU |
| Privacy demo | Airplane mode still coaches; only the match link needs LAN |

---

## 14. Privacy boundary (hard rule)

**Never leaves the phone**

- Camera / video frames
- `player_profile.json` (biometrics, torso, aim envelope, kick threshold, dominant foot)
- Whisper transcripts
- Qwen / grounded coach lines
- NEURAL LOAD numbers, DELEGATE choice, predictability HUD

**May leave the phone (LAN WebSocket only)**

- `hello`, `aim`, `kick` scalars/enums, `skel` landmark samples, `start`

On-device AI (pose / Whisper / coach) works **offline**. Only the match connection needs the laptop on the same LAN.

---

## 15. Troubleshooting (phone)

| Symptom | Likely fix |
|---|---|
| HOST Failed / TV PHONE red | Same Wi‑Fi; correct LAN IP; `server.py` running; tap HOST again |
| `VOICE · NO MODEL` | `tools/push_whisper_models.ps1` then force-stop + relaunch |
| HOLD LIKE A MIRROR / STEP BACK | Full body in outline — shoulders **and** ankles; finish calib for larger preview |
| Kicks not registering | Faster swing, full-body streak, follow-through; or recalibrate (lowers personal threshold carefully) |
| Soft / short hints | Swing harder / longer path — ForcePose path gate |
| NPU badge errors | Ensure Snapdragon device with `libcdsprpc.so`; GPU/CPU delegates still play via MediaPipe |
| Cleartext / connect refused | Use `ws://` LAN IP, not `https://` for the native app |

---

## 16. Related docs

| Doc | Scope |
|---|---|
| [`android/README.md`](android/README.md) | Short native-app overview |
| [`docs/phone_protocol.md`](docs/phone_protocol.md) | Wire schema |
| [`tools/models_on_phone.md`](tools/models_on_phone.md) | AI Hub bundle inventory |
| [`README.md`](README.md) | Full stack (phone + laptop + TV) |
| [`laptop/NEURAL_FX.md`](laptop/NEURAL_FX.md) | Laptop stadium FX (not phone) |

---

## 17. File checklist — what “ready end to end” means on Galaxy S25 Ultra

| Item | Location | Required for demo? |
|---|---|---|
| APK installed | `com.sentinelmesh.gesturefootball` | **Yes** |
| NPU pose assets | APK → `filesDir/npu/` | **Yes** (or use GPU/CPU MediaPipe) |
| MediaPipe lite task | APK assets | **Yes** for GPU/CPU fallback |
| Calibration profile | `filesDir/player_profile.json` | **Yes** (created on first calib) |
| Whisper Tiny QNN | `files/whisper/` | Strongly recommended for voice demo |
| Qwen GenieX | `files/qwen/` | Optional (coach works without) |
| HOST → laptop | prefs `gf_net`/`host_url` | **Yes** for match |

When those boxes are checked, the Galaxy S25 Ultra is a complete Player 1 edge-AI station: **pose on Hexagon, force in Newtons, voice on Hexagon, coach private, wire tiny.**
