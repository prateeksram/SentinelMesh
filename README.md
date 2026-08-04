# GESTURE FOOTBALL ⚽

Body-controlled penalty shootout on Snapdragon silicon.

**Hand aims. Leg kicks. ForcePose measures Newtons on-device.**

Branch: `prateek` · Phone: Galaxy S25 (Snapdragon 8 Elite)

---

## End architecture (target)

| Role | Device | Job |
|---|---|---|
| **Player 1** | Phone (this repo's `android/` app) | Hand → direction, leg → kick, all AI on-device |
| **Player 2** | Arduino UNO Q (teammates) | Same protocol — hand aim + leg kick |
| **Host** | Laptop | Match engine, referee, TV stadium, WebSocket hub |

The phone is **not** the host in the final design. It is Player 1’s edge-AI station.
It only sends tiny JSON (`aim`, `kick`, optional extras). No video leaves the phone.

> **Today’s interim:** laptop (or Termux on the phone) hosts a *solo* you-vs-THE-WALL match so Player 1 can be built and demoed before UNO Q lands. Same WebSocket contract either way.

---

## What's already built

### Playable stack
| Piece | Where | Status |
|---|---|---|
| Match server + AI keeper + WebSocket hub | `laptop/server.py` | Done (solo / THE WALL) |
| TV stadium | `laptop/public/tv.html` | Done |
| Browser striker (fallback) | `laptop/public/phone.html` | Done |
| **Native Android striker** | `android/` → `com.sentinelmesh.gesturefootball` | Done (NPU pose + GPU fallback) |
| ForcePose (arXiv:2503.22363) — Newtons | Browser + `ForcePoseEngine.kt` | Done |
| Bullet-time 3D skeleton → TV orbit | Phone world landmarks | Done |
| Headless match test | `laptop/test_match.py` | Done |

### Native app (on the S25 now)
- CameraX front camera
- **Hexagon NPU pose** — AI Hub `pose_landmark_detector` via ONNX Runtime QNN HTP (assets in `android/app/src/main/assets/npu/`)
- MediaPipe PoseLandmarker **GPU fallback** if QNN/HTP fails
- On-device calibration → `player_profile.json`
- Hand aim + leg-kick ForcePose → same JSON as `phone.html`
- HUD: `BODY AI · ON-DEVICE` · `FORCEPOSE · N` · `DELEGATE · NPU · xx ms`
- Default server URL: `ws://127.0.0.1:8080/ws` (change to laptop IP for final host)

### AI Hub models

| Model | Runtime | Status |
|---|---|---|
| MediaPipe Pose (8 Elite Galaxy) | QNN / precompiled ONNX | **Wired into app** (NPU path) |
| Whisper Tiny | QNN / precompiled ONNX | **Verified on S25** — push via `tools/push_whisper_models.ps1` |
| Qwen3 0.6B | GenieX QAIRT w4a16 | **Wired coach path** — grounded offline always; GenieX weights via `tools/push_qwen_models.ps1` |

---

## Phone build backlog (everything we want on Player 1)

This is the full phone-side scope. Laptop TV polish and UNO Q are **out of scope here** — teammates own those; phone only speaks the protocol.

### A — Per-user on-device calibration (do early)
20-second flow → `player_profile.json` on device (never uploaded):

1. Stand still + T-pose → **torso scale in metres** (stop guessing 0.52 m)
2. Enter **height + weight** → personal leg mass for ForcePose Newtons
3. One practice swing → **personal kick threshold** (your fidget vs real strike)
4. Detect **dominant foot**
5. Learn **aim envelope** — how *you* hold L/C/R
6. Optional online head: few practice kicks update aim/kick classifier **on NPU**

Pitch line: *“Every player gets a private biomechanical profile on the Snapdragon.”*

### B — Skill ceiling (richer kick, same wire)
| Feature | Signal | Wire field (extend JSON) |
|---|---|---|
| 6-zone aim | hand X + Y / kick elevation | `zone` + `height` (`H`/`L`) or keep using `dirDeg` |
| Curve / spin | ankle yaw at contact | `spin` ∈ [-1,1] |
| Chip vs drive | foot pitch + swing path | `strike` = `chip`\|`drive` |
| Strong / weak foot | which leg swung | `foot` = `L`\|`R` |
| Run-up quality | steps + plant-foot (pose + IMU) | `plantStability` |
| Kick prediction | extrapolate foot ~80 ms | earlier `kick` event (feel snappier) |

### C — Private HUD (only on the phone — never on TV)
- “You’re predictable” — on-device model on *your* history
- Feint coach timed to keeper reaction window
- Opponent read from broadcast match state (when P2 exists)
- Focus meter — stillness before countdown or power capped
- Anti-cheat framing — reject hand-only close-ups; require full body (+ depth if available)

### D — On-device AI stack (models already downloaded)
1. **NPU pose** — QNN binaries from `~/gf/models` → badge `DELEGATE · NPU`
2. **CPU / GPU / NPU latency toggle** — judge candy
3. **Whisper Tiny** — hear “ready”, name, trash-talk
4. **Qwen 0.6B** — private earpiece coach (not public desk)
5. **Android TTS** — coach speaks immediately (ship before Genie if needed)

### E — Phone-native flex
- Dual-cam / depth → metric ForcePose without torso guess
- IMU + ultrasonic Doppler failover (“cover the lens, still score”)
- rPPG heart-rate → private pressure meter
- Warm-up arena on phone alone (ghost keeper) before joining laptop lobby
- Signature kick — save best skeleton; score similarity next strikes
- Player card at full time (template or on-device image gen)
- Haptics language — countdown / feint window / goal / save patterns
- NEURAL LOAD strip on phone UI (which block, ms)

---

## Tomorrow — start-here build plan (phone)

Work on the **provided laptop** if disk is tight; app lives in `android/`. Deploy to S25 over USB. Point the app at the **laptop host** (`ws://<laptop-ip>:8080/ws`) for the real architecture.

### Morning — foundation
| # | Task | Done when |
|---|---|---|
| 0 | Pull `prateek`, open `android/`, confirm app installs and joins laptop `server.py` | LED green, match starts from TV |
| 1 | **Calibration screen** — height/weight + T-pose + practice swing → `player_profile.json` | ForcePose uses profile scale/mass; kick threshold personal |
| 2 | **NPU pose swap** — load QNN pose from phone storage / pushed assets | Badge shows `NPU` + ms; fallback GPU if load fails | ✅ wired (ORT QNN HTP + AI Hub assets; GPU fallback) |
| 3 | **Latency HUD** — toggle CPU / GPU / NPU on same frame | Side-by-side numbers for judges | ✅ tap `DELEGATE` badge + NEURAL LOAD |

### Afternoon — skill + voice
| # | Task | Done when |
|---|---|---|
| 4 | **6-zone + dirDeg** — high/low from hand or kick elevation | TV or logs show high/low; protocol documented for UNO Q | ✅ `height` + `docs/phone_protocol.md` |
| 5 | **Curve / chip / foot** fields on `kick` message | Server accepts extras (ignore if solo); UNO Q can match later | ✅ |
| 6 | **TTS coach** — speak line on announce / miss / goal | You hear the phone talk | ✅ |
| 7 | **Whisper** — “ready” / wake word / short trash-talk → coach reply | Mic path works offline | ✅ |
| 8 | **Qwen coach** — grounded on profile + last kicks | Private lines, airplane-mode OK | ✅ |

### Evening — differentiators + glue
| # | Task | Done when |
|---|---|---|
| 9 | **Private predictability HUD** | Warning before repeat patterns | ✅ |
| 10 | **Anti-cheat full-body gate** | Close-up hand cannot fire kicks | ✅ |
| 11 | **IMU kick assist / failover** | Kick still registers on brief occlusion | stretch |
| 12 | **Protocol freeze doc** — `docs/phone_protocol.md` | Teammates can build UNO Q P2 against it | ✅ |
| 13 | **Host mode switch** — setting: laptop IP vs localhost | Final arch: laptop hosts, phone is P1 only | ✅ |

### Stretch (if time)
- Depth-cam metric scale · rPPG pressure · warm-up ghost · signature kick · player card · sonar Doppler

### Do *not* block on tomorrow (teammates / later)
- UNO Q Player 2 implementation
- Laptop 3D stadium / broadcast package (nice, but not phone)
- Llama 3.2 (license export); Qwen is the ready LLM

---

## Quick start (today)

### A) Native app + server (dev)
1. Laptop or Termux: `cd laptop && python server.py`
2. Phone: open **Gesture Football** app (allow camera)
3. Laptop browser: `http://localhost:8080/tv.html` → START MATCH  
   If server is on the phone: `adb reverse tcp:8080 tcp:8080` then open TV via reverse, **or** run server on the laptop (preferred for final arch).
4. Prop phone 2–3 m → **FULL BODY ✓** → play

### B) Rebuild app
```
cd android
.\gradlew.bat :app:assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
```
See `android/README.md` for JDK / SDK paths.

### C) Browser fallback
`http://localhost:8080/phone.html` (USB) or `https://<host-ip>:8443/phone.html` with certs (below).

---

## The ForcePose engine

On-device implementation of [ForcePose (arXiv:2503.22363)](https://arxiv.org/abs/2503.22363):

1. Pose → 33 landmarks  
2. Savitzky–Golay foot smoothing  
3. Torso-normalized metres (**tomorrow: replace with per-user calibration**)  
4. Velocity + acceleration  
5. **F = m_leg × a_peak** (Winter tables; paper BiLSTM weights not public)

Browser: `phone.html?kg=82` · Native: calibration profile weight.

## How to play

- **Aim** — raised hand → L / C / R  
- **Kick** — leg swing on **KICK!** · ~380 N ≈ full power  
- **Feint** — hold a fake corner, switch late, then swing  
- Today: 5 kicks vs THE WALL · Target: P1 phone vs P2 UNO Q on laptop host  

## Phone camera & HTTPS (browser only)

```
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem \
  -days 365 -subj "/CN=gesture-football"
```
Native app uses CameraX — **no HTTPS needed**.

## Knobs (server env)

| var | default | meaning |
|---|---|---|
| `GF_KICKS` | 5 | kicks per match |
| `GF_SHOOT_WINDOW` | 4.0 | seconds to swing or ski |
| `GF_KEEPER_REACTION` | 0.45 | feint window (s) |
| `GF_KEEPER_IQ` | 0.75 | 0 = random · 1 = psychic |
| `GF_ANNOUNCE_S` / `GF_COUNTDOWN_S` / `GF_RESOLVE_S` | 2.2 / 3.0 / 3.8 | pacing |

Kick sensitivity: `KICK_MS` / `F_MAX` in `phone.html` or `ForcePoseEngine.kt`.

## Troubleshooting

| symptom | fix |
|---|---|
| START greyed out | phone WebSocket connected? |
| STEP BACK | full body — shoulders **and** ankles |
| App can’t find server | laptop `server.py` + phone on same Wi‑Fi; set host IP in app |
| CAMERA BLOCKED (Chrome) | HTTPS or USB localhost |
| kicks not registering | faster swing or lower `KICK_MS` |

## Dev

```
$env:GF_ANNOUNCE_S="0.1"; $env:GF_COUNTDOWN_S="0.2"; $env:GF_SHOOT_WINDOW="1.0"; $env:GF_RESOLVE_S="0.1"
python laptop/server.py
python laptop/test_match.py
```

Optional public desk: `ANTHROPIC_API_KEY` or `GF_LLM_URL` (Ollama). Phone coach (Qwen) is separate and private.
