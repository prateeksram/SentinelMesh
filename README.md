# QPlay (SentinelMesh)

Body-controlled **football · darts · basketball** on a Snapdragon **Copilot+ PC** stadium TV.

Your body is the controller: a **leg swing** takes the penalty, a **hand throw** launches the dart, a **jump shot** sends the basketball. Motion is measured on-device (Snapdragon phone Hexagon NPU and/or Arduino **UNO Q**), only tiny JSON crosses the LAN, and the laptop renders a broadcast-style venue — floodlit stadium, wood-panelled darts hall, or indoor arena — with an AI goalkeeper, ring targets, commentary, replays, Neural FX, and a **GenieX-driven campaign** that redesigns the venue and raises difficulty after every match.

**No camera video leaves the striker device.** The game runs on a local Wi‑Fi / hotspot. Internet is optional (GenieX commentary, Cloud AI100 post-match art).

| | |
|---|---|
| **Repository** | https://github.com/prateeksram/SentinelMesh |
| **Primary host** | Windows Copilot+ PC (Snapdragon X Elite) |
| **Optional striker** | Android phone (Galaxy S25 Ultra / Snapdragon 8 Elite) or browser phone page |
| **Optional edge** | Arduino UNO Q pose pipeline |
| **License** | [MIT](LICENSE) |

---

## Application description

**QPlay** is a commercially ready, open-source multi-sport interactive entertainment app for Qualcomm Snapdragon Copilot+ PCs. It turns the laptop into a stadium broadcast surface while a phone or edge board acts as the body-controlled striker.

### What you can do

- Play **three sports** from one lobby: football (hybrid geometry + AI keeper **THE WALL**), darts (ring scoring), basketball (ring scoring).
- Aim with your hand and kick / throw on cue — **5 attempts** per match, with replays and sport-true athlete animation on the TV.
- Progress through a **campaign**: after full time, SceneEngine + GenieX design the next venue and raise difficulty (keeper IQ, ring scale, shoot window).
- Run **on-device AI** where it belongs:
  - **Laptop (Copilot+ / Snapdragon X Elite):** match engine, stadium TV, optional Depth-Anything-V2 Neural FX via ONNX/QNN, GenieX venue design & commentary.
  - **Phone (optional):** Hexagon NPU pose, ForcePose (Newtons), Whisper ASR, private coach — camera frames stay on device.
- Optionally generate a **post-match report** (PNG/PDF + QR) via the AI100 subsystem under `ai100/`.

### Intended deployment

The application is open source under MIT and can be cloned, installed, and run from this repository on a Copilot+ PC. The stadium TV is a web client served by the Python host; the Android companion can be built and sideloaded as an APK. This is the distribution path for judges and users (GitHub download / clone), suitable for further packaging to app stores or other open platforms.

---

## Team

**Team name:** The Child in Us

| Name | Email |
|---|---|
| Prateek Shantharama | prateeksram@gmail.com |
| Benaka Surya T Y | |
| Anvisha Saxena | |
| Parth Shinde | |
| Ananya Bhargavi Kodali | |


---

## License

This project is released under the **MIT License**. See [`LICENSE`](LICENSE).

Copyright (c) 2026 prateeksram and contributors.

---

## Architecture

```
                    ┌─────────────── COPILOT+ PC (laptop) ────────────────┐
UNO Q pose pipeline │                                                     │
(snapkick / raw)    │  snapkick_bridge.py ──ws "unoq"──┐                  │
 ──UDP───────────────►  (optional UDP → WebSocket)     ▼                  │
                    │                            server.py :8080          │
Phone (Android app  │                            · match engine           │
or public/phone.html│  ──ws "phone"─────────────► · hybrid referee        │
in a browser)       │                            · AI keeper (THE WALL)   │
                    │                            · SceneEngine / GenieX   │
                    │                            · Neural FX              │
                    │                                   │ ws "tv"         │
                    │                                   ▼                 │
                    │                        public/tv.html (stadium TV)  │
                    └─────────────────────────────────────────────────────┘
```

Everything runs on one Wi‑Fi / hotspot. No internet is required for core play.

---

## Repository layout

| Path | What it is |
|---|---|
| `server.py` | Match host: WebSocket hub, game engine, hybrid referee, AI keeper, commentary |
| `public/tv.html` | Stadium TV UI (all three sports) |
| `public/phone.html` | Browser striker fallback |
| `start-game.bat` | One-step Windows supervisor (recommended) |
| `snapkick_bridge.py` / `snapkick_sim.py` | UNO Q bridge and no-hardware kick simulator |
| `neural_fx.py` / `NEURAL_FX.md` | TV hero-plate FX (procedural or NPU depth) |
| `scene_engine.py` / `SCENE_ENGINE.md` | GenieX venue design + campaign difficulty |
| `android/` | Native **QPlay** Android striker (Hexagon NPU, ForcePose, Whisper, coach) |
| `ai100/` | Optional Cloud AI100 post-match report engine |
| `unoq/` | Edge pose streamer for Arduino UNO Q |
| `laptop/` | Optional SceneEngine assets / agentic venue pipeline (not a match host) |
| `docs/` | Protocols and deep-dive setup guides |
| `tools/` | Model fetch/push and launcher helpers |
| `models/` | Optional `hero_depth.onnx` for Neural FX |
| `classifier/` | Future object→sport switcher (not wired yet) |

---

## Requirements

### Hardware (intended)

| Role | Device | Notes |
|---|---|---|
| **Host / TV (required)** | Windows **Copilot+ PC** with Snapdragon X Elite | Runs `server.py` + Edge/Chrome stadium TV |
| **Striker (recommended)** | Android phone with camera (Galaxy S25 Ultra preferred) | Native QPlay app with Hexagon NPU |
| **Striker (fallback)** | Same laptop or any phone browser | `public/phone.html` (HTTPS for camera) |
| **Edge (optional)** | Arduino UNO Q + camera | Raw pose on UDP 9999 or SnapKick on UDP 5005 |

**Minimum demo (no phone / no UNO Q):** Copilot+ PC + Python + browser TV + `snapkick_sim.py`.

### Software

- **Windows 11** (Copilot+ PC)
- **Python 3.13** (`py -3.13` on Windows)
- Modern browser (Microsoft Edge recommended) for the stadium TV
- For Android companion: **JDK 17**, **Android SDK**, `adb`
- Optional: OpenSSL (browser-phone HTTPS certs), Qualcomm AI Hub token (model fetch), GenieX local LLM

---

## Setup instructions (from scratch)

### 1. Clone the repository

```powershell
git clone https://github.com/prateeksram/SentinelMesh.git
cd SentinelMesh
```

### 2. Install Python dependencies

```powershell
py -3.13 -m pip install -r requirements.txt
```

Core dependency: `aiohttp>=3.9`.

Optional extras:

```powershell
# Post-match AI100 reports (laptop path)
py -3.13 -m pip install -r ai100\requirements.txt
Copy-Item ai100\.env.example ai100\.env
# Edit ai100\.env and set AI100_API_KEY if you have one

# Optional Neural FX NPU upgrade (see NEURAL_FX.md)
# py -3.13 -m pip install onnxruntime
# Set QAI_HUB_API_TOKEN then run:
# .\fetch_aihub_models.ps1
```

macOS / Linux hosts: `python3 -m pip install -r requirements.txt` (game logic works; Copilot+ NPU paths are Windows/Snapdragon-oriented).

### 3. Firewall

Allow inbound:

| Port | Purpose |
|---|---|
| **TCP 8080** | Stadium TV, phone WebSocket, match host |
| **UDP 9999** | Raw UNO Q pose (if used) |
| **UDP 5005** | Optional SnapKick bridge |
| **TCP 8443** | HTTPS browser-phone camera (optional) |

### 4. Optional HTTPS certs (browser phone only)

The native Android app and the TV do **not** need HTTPS. For `phone.html` camera access:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 -subj "/CN=gesture-arena"
```

Place `key.pem` and `cert.pem` next to `server.py`.

### 5. Optional Android striker (QPlay)

```powershell
$env:JAVA_HOME = "<path-to-JDK-17>"
$env:ANDROID_HOME = "<path-to-Android-SDK>"
cd android
.\gradlew.bat :app:assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
cd ..
.\tools\push_whisper_models.ps1
# Optional coach weights:
# .\tools\push_qwen_models.ps1 -Source <extracted_geniex_folder>
```

Details: [`android/README.md`](android/README.md), [`docs/README_GalaxyS25.md`](docs/README_GalaxyS25.md).

---

## Run and usage instructions

### Quick start on Copilot+ PC (recommended)

**Laptop / phone only (no UNO Q):**

```powershell
.\start-game.bat -SkipUnoQ
```

**With UNO Q edge pose:**

```powershell
.\start-game.bat -UnoQIp 192.168.150.72 -CameraIndex 1 -SyncUnoQ
```

See [`docs/one_step_setup.md`](docs/one_step_setup.md) for camera, SSH, and port options.

When ready, the launcher prints the TV URL (typically `http://localhost:8080/tv.html`).

### Manual start

**Terminal 1 — match host:**

```powershell
py -3.13 server.py
```

Open the stadium TV: [http://localhost:8080/tv.html](http://localhost:8080/tv.html)

**Terminal 2 — motion source (pick one):**

| Source | How |
|---|---|
| No hardware (demo) | `py -3.13 snapkick_sim.py` — simulated kick every ~4 s |
| Android QPlay app | Set **HOST** to `<laptop-ip>:8080`, tap HOST, calibrate, play |
| Browser phone | `https://<laptop-ip>:8443/phone.html` (accept cert warning) |
| UNO Q (SnapKick) | `py -3.13 snapkick_bridge.py` and point board UDP at `:5005` |

Phone and UNO Q can be connected at the same time — the first action in each shoot window counts.

### How to play

1. On the **stadium TV**, pick a sport in the lobby (football / darts / basketball).
2. Connect a striker (phone, browser, UNO Q, or simulator) until the TV shows a live connection.
3. On the phone: complete calibration (height/weight → T-pose → aim → practice) if prompted.
4. Tap **START MATCH** on the TV.
5. Aim with your hand; kick or throw on the countdown. You get **5 attempts**.
6. After full time: campaign **NEXT VENUE** redesigns the arena and raises difficulty; or **PLAY AGAIN** / **END MATCH**.

### The three sports

#### Football — hybrid referee

1. Geometry first: outside the 7.32 × 2.44 m goal → **WIDE**; near the frame → **POST**.
2. On-target shots face **THE WALL** (AI keeper): it reads aim ~0.45 s before the strike, studies shot history, and dives. Corners and high power can beat the glove.
3. Scorebug: goals vs saves.

#### Darts — ring geometry

Metric rings around the bull at 1.73 m: **≤0.10 m = 100 · ≤0.30 m = 60 · ≤0.60 m = 30 · ≤0.95 m = 10**, else miss.

#### Basketball — ring geometry

Rings around the hoop at 2.0 m: **≤0.25 m = 100 (swish) · ≤0.55 m = 40 · ≤0.95 m = 10**.

### Campaign & SceneEngine

After every full time the server enters a **NEXT VENUE** phase. GenieX (local OpenAI-compatible endpoint at `GF_GENIEX_URL`, default `http://127.0.0.1:18181/v1`) designs atmosphere and difficulty. Offline, template venues still apply the same difficulty curve (`SCENE · TEMPLATE`).

| Knob | Sport | Effect as levels rise (1 → 5) |
|---|---|---|
| `keeperIq` / `keeperReaction` | football | Keeper reads you better, reacts faster |
| `powerBeat` | football | Harder to beat the glove with raw power |
| `ringScale` | darts / basketball | Scoring rings shrink 1.0× → 0.6× |
| `shootWindow` | all | Level 3+ puts you on the clock |

### Useful environment variables

| Variable | Default | Meaning |
|---|---|---|
| `GF_KICKS` | 5 | Attempts per match |
| `GF_SHOOT_WINDOW` | 0 | Seconds to act; ≤ 0 waits forever |
| `GF_KEEPER_REACTION` | 0.45 | Keeper aim-read lead time (s) |
| `GF_KEEPER_IQ` | 0.75 | 0 = random · 1 = psychic |
| `GF_GENIEX` | 1 | Set `0` to skip GenieX |
| `GF_GENIEX_URL` / `GF_GENIEX_MODEL` | local GenieX / Qwen3 | Venue + commentary endpoint |
| `GF_SCENE_TIMEOUT_S` / `GF_SCENE_MAX_LEVEL` | 90 / 5 | Scene timeout · campaign cap |
| `ANTHROPIC_API_KEY` or `GF_LLM_URL` | — | Fallback commentary desks |
| `GF_PUBLIC_BASE_URL` | LAN address | Base URL for AI100 report QR links |

Protocol details: [`docs/phone_protocol.md`](docs/phone_protocol.md).

---

## Tests and verification (recommended)

```powershell
py -3.13 test_combined.py    # all three sports + campaign (self-launching)
py -3.13 test_match.py       # phone-striker regression (start server.py first)
py -3.13 test_scene_gen.py   # SceneEngine smoke test
```

Optional AI100 unit tests:

```powershell
py -3.13 -m pip install -r ai100\requirements.txt
python -m pytest ai100\test_report_engine.py -q
```

`test_combined.py` checks football geometry gates and keeper outcomes, dart/basketball ring points, sport switching, metric fields through UDP → bridge → server → state, and campaign level / scene generation.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Bridge: `WinError 10048` on UDP 5005 | Kill leftover Python processes holding the port |
| Random kicks you didn't make | A forgotten `snapkick_sim.py` is still running |
| TV UNO Q LED red | Bridge not running, or wrong `--host` |
| START MATCH greyed out | No striker connected (phone **or** UNO Q / sim) |
| Board sends but nothing happens | Firewall / wrong laptop IP — test with `snapkick_sim.py` first |
| Phone browser camera black | Use `https://:8443/phone.html`, not plain `http://` |
| Sport buttons do nothing | Sport can only change in the **lobby** (END MATCH first) |

---

## Notes

- **Privacy:** Camera frames and player biometrics stay on the phone. Only compact aim/kick JSON crosses the LAN.
- **Offline play:** Core match + on-device coach work without internet. GenieX, cloud commentary, and AI100 art are optional upgrades.
- **Single host:** Root `server.py` + `public/` is the only match host. `laptop/` retains optional SceneEngine scene assets; do not run a second server from there.
- **Models not in git:** Whisper / Qwen weight bins and large ONNX depth models are fetched or pushed via scripts under `tools/` and `fetch_aihub_models.ps1` (see `models/README.md`, `android/README.md`).
- **Classifier roadmap:** `classifier/` can recognize a physical ball and switch sports; training/export exists but is not wired into `server.py` yet.

---

## References

- ForcePose — estimating strike force from pose dynamics: https://arxiv.org/abs/2503.22363
- Qualcomm AI Hub — pose, Whisper, Depth-Anything-V2 model export for Snapdragon devices
- Depth-Anything-V2 / ONNX Runtime QNN — optional laptop Neural FX (`NEURAL_FX.md`)
- GenieX (local OpenAI-compatible LLM) — venue design and commentary desk
- Qualcomm Cloud AI100 — optional post-match artwork (`ai100/README.md`)

### Related docs in this repo

- [`docs/one_step_setup.md`](docs/one_step_setup.md) — `start-game.bat` supervisor
- [`docs/phone_protocol.md`](docs/phone_protocol.md) — WebSocket client protocol
- [`docs/README_GalaxyS25.md`](docs/README_GalaxyS25.md) — phone NPU deep dive
- [`android/README.md`](android/README.md) — native QPlay build & demo script
- [`NEURAL_FX.md`](NEURAL_FX.md) — Copilot+ PC stadium FX
- [`SCENE_ENGINE.md`](SCENE_ENGINE.md) — campaign venue generation
- [`ai100/README.md`](ai100/README.md) — post-match reports
