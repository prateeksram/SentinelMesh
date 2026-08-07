# GESTURE FOOTBALL

Body-controlled penalty shootout on Snapdragon silicon.

**Hand aims. Leg kicks. ForcePose measures Newtons on-device. Whisper hears you. A private coach talks back — no video leaves the phone.**

Branch: `prateek` · Phone: Galaxy S25 Ultra (Snapdragon 8 Elite)

---

## Architecture

| Role | Device | Job |
|---|---|---|
| **Player 1** | Phone (`android/` app) | Aim, kick, all AI on-device |
| **Player 2** | Arduino UNO Q (teammates) | Same WebSocket protocol |
| **Host / TV** | Host (`server.py`) | Match engine, THE WALL, stadium UI |

The phone is **not** the match host. It only sends tiny JSON (`aim`, `kick`, `skel`). No camera frames leave the device.

Laptop and phone need the **same Wi‑Fi / hotspot**, not internet. On-device AI works offline (airplane mode still coaches; only the match link needs LAN).

---

## Phone capabilities now (`prateek`)

The native app `com.sentinelmesh.gesturefootball` is the Player 1 edge-AI station. Current capability:

### Sees you
- CameraX front camera (camera-first layout after calibration)
- **Hexagon NPU pose** — AI Hub `pose_landmark_detector` via ONNX Runtime QNN HTP
- **Tap `DELEGATE`** to cycle **NPU → GPU → CPU** (MediaPipe for GPU/CPU)
- Live ms on the badge + **NEURAL LOAD** strip (`POSE / ASR / LLM`)

### Knows you
- One-time **calibration** → `player_profile.json` on device only:
  - height / weight → leg mass for ForcePose
  - T-pose → torso scale in metres
  - aim L / C / R envelope
  - practice swing → personal kick threshold
  - dominant foot
- Profile never uploaded

### Measures you
- **ForcePose** (arXiv:2503.22363) — Savitzky–Golay → torso metres → `F = m_leg × a_peak`
- Hand aim → `L` / `C` / `R`
- Kick JSON extras: `height` (`H`/`L`), `spin`, `strike` (`chip`/`drive`), `foot`, plus `force` / `power` / `dirDeg`
- Bullet-time skeleton frames for TV orbit replay
- Protocol: [`docs/phone_protocol.md`](docs/phone_protocol.md)

### Hears you
- **Whisper Tiny on Hexagon** (push models — not in APK)
- Commands: “ready”, left / center / right, trash-talk
- Badge: `VOICE · LISTENING`

### Talks back
- Android TTS on voice commands and match events (announce / countdown / goal / miss)
- **Private coach** grounded on profile + recent kicks (always offline)
  - Default backend: on-device `COACH`
  - Optional GenieX Qwen3 0.6B weights via `tools/push_qwen_models.ps1` → `QWEN` on NEURAL LOAD

### Protects the demo
- Full-body frame streak required before kicks fire (anti close-up cheat)
- “You’re predictable — mix it” when you loop the same corner
- In-app **HOST** field → laptop IP (`ws://<ip>:8080/ws`), saved in prefs

### Pitch line
*“Snapdragon hears you, measures your kick in Newtons, coaches you privately, and only sends a tiny JSON shot to the TV.”*

---

## AI Hub models

| Model | Runtime | Status |
|---|---|---|
| Pose landmark (8 Elite Galaxy) | QNN / precompiled ONNX | **In APK** `android/app/src/main/assets/npu/` |
| Whisper Tiny | QNN / precompiled ONNX | **Push** `tools/push_whisper_models.ps1` → app `files/whisper/` |
| Qwen3 0.6B | GenieX QAIRT w4a16 | **Optional push** `tools/push_qwen_models.ps1`; coach works without it |

Whisper / Qwen weight bins are **not** committed to git (~100 MB–750 MB).

### Measured on S25 Ultra (indicative)

| Block | Delegate | Typical |
|---|---|---|
| Pose | QNN HTP | ~15–40 ms / frame (live badge) |
| Whisper Tiny | QNN HTP | ~1–10 s / utterance |
| Private coach | grounded / Qwen | &lt;50 ms grounded |

Details: [`android/README.md`](android/README.md).

---

## Rest of the stack (already built)

| Piece | Where | Status |
|---|---|---|
| Match server + AI keeper + WebSocket | `server.py` | Done (solo / THE WALL) |
| TV stadium | `laptop/public/tv.html` | Done |
| Browser striker (fallback) | `laptop/public/phone.html` | Done |
| Headless match test | `laptop/test_match.py` | Done |

---

## Quick start

### 1) Host + TV
```powershell
python server.py
```
Open `http://localhost:8080/tv.html`. Note host LAN IP (e.g. `172.20.10.2`).

### 2) Phone app
1. Install / open **Gesture Football** (camera + mic permissions).
2. If Whisper missing: `.\tools\push_whisper_models.ps1` then relaunch.
3. **HOST** field → `172.20.10.2:8080` → tap **HOST** (PHONE LED green on TV).
4. Calibrate once if prompted → step back until **FULL BODY ✓**.
5. On TV → **START MATCH**.

Same Wi‑Fi / hotspot required. Do **not** use `localhost` on the phone unless the server is on the phone itself.

### 3) Rebuild app
```powershell
$env:JAVA_HOME = "C:\Users\prate\android-dev\jdk\jdk-17.0.20+8"
$env:ANDROID_HOME = "C:\Users\prate\android-dev\sdk"
cd android
.\gradlew.bat :app:assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

### 4) Browser fallback (optional)
`http://localhost:8080/phone.html` (USB) or `https://<host-ip>:8443/phone.html` with certs. Native app does **not** need HTTPS.

---

## How to play

- **Aim** — raised hand → L / C / R (or say “left” / “right” / “center”)
- **Kick** — leg swing on **KICK!** · ~380 N ≈ full power
- **Feint** — hold a fake corner, switch late, then swing
- **Voice** — “ready”, trash-talk → private TTS coach
- Today: 5 kicks vs THE WALL · Target: P1 phone vs P2 UNO Q

### 90s demo beat
1. Tap `DELEGATE` — NPU / GPU / CPU ms change  
2. Calibration — “profile never leaves this Snapdragon”  
3. Full body kick — ForcePose Newtons  
4. Say “ready” — Whisper on Hexagon  
5. Miss — private coach line  
6. Point at **NEURAL LOAD**  

---

## Still optional / stretch (not required for demo)

| Item | Notes |
|---|---|
| Full GenieX Qwen on NPU | Coach works offline today; true Hexagon LLM needs Genie runtime + pushed weights |
| IMU kick failover | Cover lens, still score |
| `plantStability` / kick prediction | Richer wire fields |
| Depth / rPPG / ghost warm-up / signature kick | Phone-native flex |
| UNO Q Player 2 | Teammates |
| Laptop 3D stadium polish | Teammates — Canvas Neural FX shipped; see `laptop/NEURAL_FX.md` |

---

## ForcePose

On-device port of [ForcePose (arXiv:2503.22363)](https://arxiv.org/abs/2503.22363):

1. Pose → landmarks  
2. Savitzky–Golay foot smoothing  
3. Torso-normalized metres (**from calibration profile**)  
4. Velocity + acceleration  
5. **F = m_leg × a_peak** (Winter tables; paper BiLSTM weights not public)

Native: calibration weight / torso. Browser fallback: `phone.html?kg=82`.

---

## Server knobs

| var | default | meaning |
|---|---|---|
| `GF_KICKS` | 5 | kicks per match |
| `GF_SHOOT_WINDOW` | 4.0 | seconds to swing or ski |
| `GF_KEEPER_REACTION` | 0.45 | feint window (s) |
| `GF_KEEPER_IQ` | 0.75 | 0 = random · 1 = psychic |
| `GF_ANNOUNCE_S` / `GF_COUNTDOWN_S` / `GF_RESOLVE_S` | 2.2 / 3.0 / 3.8 | pacing |

Kick sensitivity: `KICK_MS` / `F_MAX` in `ForcePoseEngine.kt` (or browser `phone.html`).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| TV **PHONE** red | App HOST = laptop LAN IP + same Wi‑Fi; tap **HOST** |
| START greyed out | phone WebSocket connected? |
| HOLD LIKE A MIRROR / STEP BACK | full body — shoulders **and** ankles; finish calib for larger preview |
| `VOICE · NO MODEL` | `.\tools\push_whisper_models.ps1` then force-stop + relaunch |
| CAMERA BLOCKED (Chrome) | HTTPS or USB localhost (browser only) |
| Kicks not registering | faster swing, full-body streak, or lower kick threshold |

---

## Dev

```powershell
$env:GF_ANNOUNCE_S="0.1"; $env:GF_COUNTDOWN_S="0.2"; $env:GF_SHOOT_WINDOW="1.0"; $env:GF_RESOLVE_S="0.1"
python server.py
python test_match.py
```

Optional public desk on host: `ANTHROPIC_API_KEY` or `GF_LLM_URL`. Phone coach is separate and private.

On-device stadium VFX (Adreno + optional Hexagon): see [`NEURAL_FX.md`](../NEURAL_FX.md).
