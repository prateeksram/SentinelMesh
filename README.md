# GESTURE FOOTBALL ⚽ — solo edition

A penalty shootout you play with your body. Your phone's camera tracks you:
your **raised hand aims** the shot (left / centre / right) and a **fast leg
swing kicks** it. **THE WALL** — an AI goalkeeper — watches your hand, studies
your shot history, and dives.

The twist: the keeper reads your aim with human-like reaction lag
(~0.45 s before the kick). Hold a fake direction, switch your hand at the
last moment, then swing — and send the machine the wrong way.

Branch: `prateek` · Target silicon: Galaxy S25 (Snapdragon 8 Elite) + laptop TV.

---

## What's built (today)

### Playable stack
| Piece | Where | Status |
|---|---|---|
| Match server + AI keeper + WebSocket hub | `laptop/server.py` (also runs on-phone via Termux) | Done |
| TV stadium (scoreboard, aim reticle, keeper dive, ball flight) | `laptop/public/tv.html` | Done |
| Browser striker (fallback) | `laptop/public/phone.html` | Done |
| **Native Android striker app** | `android/` — installed on S25 as `com.sentinelmesh.gesturefootball` | Done (GPU path) |
| ForcePose (arXiv:2503.22363) — kicks in real Newtons | Browser + native (`ForcePoseEngine.kt`) | Done |
| Bullet-time 3D skeleton replay on TV | Phone captures world landmarks → TV orbits | Done |
| Headless match test | `laptop/test_match.py` | Done |

### Native app (Phase 1 — live on device)
- CameraX front camera
- MediaPipe PoseLandmarker (**GPU** delegate, ~30–40 ms/frame)
- Hand aim + leg-kick ForcePose → same WebSocket JSON as the browser
- HUD: `BODY AI · ON-DEVICE`, `FORCEPOSE · N`, `DELEGATE · GPU · xx ms`
- Connects to `ws://127.0.0.1:8080/ws` (Termux server on the same phone)

### AI Hub models already on the phone (`~/gf/models`, ~1 GB)
Staged for tomorrow — **not wired into the app yet**:

| Model | Runtime | Use |
|---|---|---|
| MediaPipe Pose (8 Elite Galaxy) | QNN context + precompiled ONNX | Hexagon NPU pose |
| Whisper Tiny | QNN / precompiled ONNX | On-device speech recognition |
| Qwen3 0.6B | GenieX QAIRT w4a16 | On-device LLM for THE WALL |

---

## What to build tomorrow

Priority order for the hackathon demo:

1. **NPU pose swap (Phase 2)** — load the QNN pose binaries from `~/gf/models` via LiteRT/QNN (or ONNX Runtime QNN EP). Badge should read `DELEGATE · NPU` with a CPU/GPU/NPU latency toggle for judges.
2. **THE WALL speaks** — Android TTS first (fast), then Whisper Tiny for hearing trash-talk, then Qwen 0.6B Genie for on-device commentary (move the desk off the laptop).
3. **Spectacle polish** — selfie segmentation (you composited into the stadium), 6-zone shots from kick elevation (`dirDeg` already sent), NEURAL LOAD HUD on the TV.
4. **Optional stretch** — learning keeper (trains on your run-up tells mid-match), rPPG pressure meter, Stable Diffusion match poster, Llama 3.2 export (license-gated; Qwen is the ready substitute).
5. **Teammate integration** — freeze the WebSocket JSON contract; UNO Q / X Elite stations plug into the same hub.

Build the APK on the provided laptop if this machine is out of disk; project + toolchain notes are in `android/README.md`.

---

## Quick start (recommended: native app + phone-hosted server)

1. **On the phone (Termux):**
   ```
   cd ~/gf/laptop && python server.py
   ```
2. **Open the native app** — *Gesture Football* (not Chrome). Allow camera.
3. **On the laptop TV:** `http://localhost:8080/tv.html`  
   (USB: `adb reverse tcp:8080 tcp:8080` if the server runs on the phone; or run `server.py` on the laptop instead.)
4. Prop the phone 2–3 m away → **FULL BODY ✓** → **START MATCH** on the TV.

### Browser fallback
- Phone: `http://localhost:8080/phone.html` (USB reverse) or `https://<laptop-ip>:8443/phone.html` with certs (see below).

### Rebuild / reinstall the Android app
```
cd android
.\gradlew.bat :app:assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

---

## The ForcePose engine

Kick power isn't a made-up number — the phone measures your kick in **real
Newtons**, implementing the pipeline from
[ForcePose (arXiv:2503.22363)](https://arxiv.org/abs/2503.22363) on-device:

1. MediaPipe pose → 33 landmarks per frame
2. Savitzky–Golay temporal smoothing of the foot trajectory
3. Torso-normalized metric scale (pixels → metres)
4. Central-difference velocity + acceleration
5. Force head: **F = m_leg × a_peak** (Winter tables; paper's BiLSTM weights aren't public)

Pass your weight for calibrated numbers: `phone.html?kg=82` (default 70).

## How to play

- **Aim** — raise a hand; L / C / R steers the TV reticle.
- **Kick** — swing your leg on **KICK!** · ~380 N = full power.
- **Feint** — point one way, snap your hand late, then swing.
- 5 kicks per match. Beat the machine.

## Phone camera & HTTPS (browser only)

Off `localhost`, browsers block the camera on plain HTTP. Next to `server.py`:
```
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem \
  -days 365 -subj "/CN=gesture-football"
```
Then open `https://<laptop-ip>:8443/phone.html` and accept the warning once.
The **native app does not need HTTPS** (it uses CameraX).

## Knobs (env vars)

| var | default | meaning |
|---|---|---|
| `GF_KICKS` | 5 | kicks per match |
| `GF_SHOOT_WINDOW` | 4.0 | seconds to swing before the kick is skied |
| `GF_KEEPER_REACTION` | 0.45 | feint window (s before kick that the keeper sees) |
| `GF_KEEPER_IQ` | 0.75 | 0 = random, 1 = near-psychic |
| `GF_ANNOUNCE_S` / `GF_COUNTDOWN_S` / `GF_RESOLVE_S` | 2.2 / 3.0 / 3.8 | phase pacing |

Kick sensitivity: `KICK_MS` / `F_MAX` in `phone.html` or `ForcePoseEngine.kt`.

## Troubleshooting

| symptom | fix |
|---|---|
| START greyed out | phone must be connected — check PHONE LED |
| Native app dark / no body | step back until **FULL BODY ✓**; grant camera |
| Server not found in app | Termux `server.py` running; same-device `127.0.0.1:8080` |
| "CAMERA BLOCKED" in Chrome | use HTTPS or USB `localhost` |
| kicks not registering | swing faster, or lower `KICK_MS` |
| keeper saves everything | lower `GF_KEEPER_IQ`, or feint harder |

## Dev

```
$env:GF_ANNOUNCE_S="0.1"; $env:GF_COUNTDOWN_S="0.2"; $env:GF_SHOOT_WINDOW="1.0"; $env:GF_RESOLVE_S="0.1"
python laptop/server.py     # terminal 1
python laptop/test_match.py # terminal 2
```

Optional AI Desk (templates work without it): `ANTHROPIC_API_KEY` or `GF_LLM_URL` for Ollama.
