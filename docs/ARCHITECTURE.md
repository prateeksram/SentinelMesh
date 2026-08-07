# Architecture reference

Single source of truth for components, ports, environment variables, HTTP routes, and message flow.

---

## 1. Component map

| Component | Process / device | Entry point | Talks to |
|---|---|---|---|
| **Match host** | Copilot+ PC | [`server.py`](../server.py) | Everything (WebSocket hub + HTTP + UDP) |
| **Stadium TV** | Browser on the PC | [`public/tv.html`](../public/tv.html) | `ws://host:8080/ws` as `tv` |
| **Browser striker** | Any phone browser | [`public/phone.html`](../public/phone.html) | `wss://host:8443/ws` as `phone` |
| **Native striker** | Android phone | [`android/`](../android/) app (QPlay) | `ws://host:8080/ws` as `phone` |
| **Edge pose streamer** | Arduino UNO Q | [`unoq/sentinel_pose_streamer.py`](../unoq/sentinel_pose_streamer.py) | UDP 9999 (landmarks) + HTTP POST `/edge/frame` (preview JPEG) |
| **SnapKick bridge** | Copilot+ PC | [`snapkick_bridge.py`](../snapkick_bridge.py) | UDP 5005 in → `ws` as `unoq` out |
| **SnapKick simulator** | Any machine | [`snapkick_sim.py`](../snapkick_sim.py) | UDP 5005 out (fake kicks) |
| **AI100 report engine** | in-process with the host | [`ai100/report_engine.py`](../ai100/report_engine.py), [`ai100/web.py`](../ai100/web.py) | Optional HTTPS to Cirrascale AI Suite |
| **SceneEngine** | in-process with the host | [`scene_engine.py`](../scene_engine.py) + [`geniex_client.py`](../geniex_client.py) | Optional HTTP to GenieX at `GF_GENIEX_URL` |
| **Neural FX** | in-process with the host | [`neural_fx.py`](../neural_fx.py) | Optional ONNX Runtime (QNN/CPU) |
| **Supervisor** | Copilot+ PC | [`start-game.bat`](../start-game.bat) → [`tools/start_game.ps1`](../tools/start_game.ps1) | Launches/kills all of the above + SSH to UNO Q |

**There is exactly one match host:** root `server.py` + `public/`. The [`laptop/`](../laptop/) directory is **not a server** - it retains the experimental agentic scene generator (GenieX-drafted HTML venue pages verified against a contract extracted from the golden `tv.html`) as a library/CLI plus brand assets. See [`laptop/README.md`](../laptop/README.md).

---

## 2. Ports

| Port | Protocol | Bound by | Purpose |
|---|---|---|---|
| **8080** | TCP | `server.py` (hardcoded) | TV, WebSocket `/ws`, all HTTP routes |
| **8443** | TCP | `server.py`, **only if** `cert.pem` + `key.pem` sit next to it | HTTPS mirror (browser-phone camera needs a secure origin) |
| **9999** | UDP | `server.py` (`GF_EDGE_POSE_PORT`) | `sentinel.edge.pose.v1` raw landmarks from the UNO Q |
| **5005** | UDP | `snapkick_bridge.py` (`--udp-port`), **not** the server | `snapkick.pose.v1` pre-solved kick packets |
| **18181** | TCP | GenieX (external, optional) | OpenAI-compatible LLM for scenes + commentary |

---

## 3. Environment variables (complete)

All read in [`server.py`](../server.py) unless noted. "-" means unset by default.

### Match pacing & difficulty

| Variable | Default | Meaning |
|---|---|---|
| `GF_KICKS` | `3` | Attempts per match |
| `GF_SHOOT_WINDOW` | `60` | Seconds allowed in the shoot phase; timeout → result `over` (skied) |
| `GF_MIN_SHOOT_WINDOW` | `60` | Hard floor applied to every shoot window. **Consequence:** the SceneEngine's level-3+ `shootWindow` values (2.2–3.0 s) are floored away and currently have no effect. Set both vars low (e.g. `5`) for fast test runs. |
| `GF_KEEPER_REACTION` | `0.45` | Keeper reads the aim this many seconds *before* the strike (feint window) |
| `GF_KEEPER_IQ` | `0.75` | 0 = random dives · 1 = near-psychic |
| `GF_ANNOUNCE_S` | `3.5` | Announce phase length (s) |
| `GF_COUNTDOWN_S` | `3.0` | Countdown phase length (s) |
| `GF_RESOLVE_S` | `3.8` | Resolve phase length (s) |

Not env-tunable: `POWER_BEAT = 0.82` (power needed to beat a correct-guess keeper; overridden per campaign level by the scene difficulty).

### Edge relay

| Variable | Default | Meaning |
|---|---|---|
| `GF_EDGE_POSE_PORT` | `9999` | UDP port for raw edge pose |
| `GF_EDGE_FRAME_STALE_S` | `2.0` | Preview JPEG / MJPEG staleness cutoff (s) |

### Commentary desks (priority: GenieX > local > cloud > templates)

| Variable | Default | Meaning |
|---|---|---|
| `GF_GENIEX` | `1` | `0`/`false`/`no` disables the GenieX desk |
| `GF_GENIEX_URL` | `http://127.0.0.1:18181/v1` | OpenAI-compatible base (in `geniex_client.py`) |
| `GF_GENIEX_MODEL` | `qualcomm/Qwen3-4B-Instruct-2507:W4A16` | Served model id (in `geniex_client.py`) |
| `GF_LLM_URL` | - | Anthropic-shaped local endpoint (e.g. Ollama proxy); used when GenieX is off |
| `ANTHROPIC_API_KEY` | - | Cloud desk (used when GenieX off and no `GF_LLM_URL`) |
| `GF_MODEL` | `llama3.2:3b` (local) / `claude-haiku-4-5-20251001` (cloud) | Desk model id |

### SceneEngine (campaign)

| Variable | Default | Meaning |
|---|---|---|
| `GF_SCENE_MAX_LEVEL` | `5` | Campaign level cap (in `scene_engine.py`) |
| `GF_SCENE_TIMEOUT_S` | `90` | Scene JSON generation timeout (in `scene_engine.py`) |

### Neural FX

| Variable | Default | Meaning |
|---|---|---|
| `QNN_SDK_ROOT` | - | If set to an existing path, ONNX Runtime prepends the QNN Execution Provider (in `neural_fx.py`) |

### AI100 reports ([`ai100/`](../ai100/))

| Variable | Default | Meaning |
|---|---|---|
| `AI100_API_KEY` | - | Cirrascale/Cloud AI100 key; without it, artwork is procedural (report still renders) |
| `AI100_BASE_URL` | `https://aisuite.cirrascale.com/apis/v2` | API base; `…/images/generations` is derived |
| `AI100_IMAGE_ENDPOINT` | - | Full endpoint override (takes priority over `AI100_BASE_URL`) |
| `AI100_MODEL` | `stabilityai/sdxl-turbo` | Image model (aliases normalized on Cirrascale) |
| `AI100_IMAGE_SIZE` | `512x512` | Generated artwork size |
| `AI100_TIMEOUT_SECONDS` | `240` (floor 30) | Artwork request timeout |
| `AI100_ENV_FILE` | `ai100/.env`, then `<repo>/.env` | Dotenv path override |
| `GF_PUBLIC_BASE_URL` | derived LAN IP | Base URL baked into report QR links (set to an ngrok origin for off-LAN demos) |
| `GF_ENABLE_REPORT_SIM` | `0` | `1` allows `POST /api/report/simulate` from non-localhost |

`.env` loading never overwrites variables already set in the launching shell.

---

## 4. HTTP routes (root `server.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | 302 → `/tv.html` |
| GET | `/ws` | WebSocket hub (all roles) |
| GET | `/tv.html`, `/phone.html`, … | Static files from `public/` |
| POST | `/edge/frame` | UNO Q preview JPEG in (max 2 MB) |
| GET | `/edge/frame.jpg` | Latest preview (supports `?after=<seq>` long-poll → 204, `X-Edge-Seq` header) |
| GET | `/edge/camera.mjpg` | MJPEG stream of the UNO Q preview |
| POST | `/edge/source/frame` | Laptop USB-camera JPEG in (relay mode) |
| GET | `/edge/source/camera.mjpg` | MJPEG of the relay camera (the UNO Q reads this in laptop-camera mode) |
| GET | `/edge/status` | Liveness JSON: server/camera/pose states, seqs, ports |
| GET | `/fx/status` | Neural FX backend: `procedural` / `cpu` / `qnn` |
| POST | `/fx/hero` | Render a hero plate from skeleton frames or a still (returns PNG data-URL) |
| GET | `/scene/status` | Campaign level, generation progress, scene metrics |
| GET | `/hw/status` | Desk mode, FX backend, GenieX config, AI100 configured flag |
| GET | `/api/report` | Current post-match report card (or `{"status":"idle"}`) |
| POST | `/api/report/simulate` | Generate a demo report (localhost-only unless `GF_ENABLE_REPORT_SIM=1`) |
| GET | `/report/{token}` | Mobile landing page (unguessable token, 30-min TTL) |
| GET | `/report/{token}.png` / `.pdf` | Report assets (`?download=1` → attachment) |
| GET | `/report/{token}/qr.png` | QR code pointing at the landing page |

---

## 5. Message flow

Wire schemas are specified field-by-field in [`phone_protocol.md`](phone_protocol.md). Summary:

### WebSocket roles (`hello.client`)

`phone` and `unoq` are **strikers** (may send `aim` / `kick` / `skel`). `tv` drives the match (`sport` / `start` / `again` / `abort`) and receives everything, including 1 Hz `telem_state` telemetry. `bridge` is an alias of `unoq`; `dashboard` receives only `telem_state`. Any client may send `telem` self-reports.

### A kick's life

1. Striker sends `kick` during the `shoot` phase (first one wins).
2. Referee:
   - metric fields present (`goalX`/`goalZ`) → **hybrid referee**: geometry decides wide/post (football) or ring points (darts/basketball); the AI keeper only contests on-target football shots.
   - zone-only kick → probabilistic referee (football) or a synthesized impact point (target sports).
3. Result lands in the `state` snapshot (`shotmap`, `last`, `score`), the TV animates the ball, requests a `/fx/hero` plate, and plays the bullet-time `skel` replay.
4. After the final kick: SceneEngine generates the next venue (phase `generating`), then `end` - and the AI100 report is queued and broadcast as `postGameReport` when ready.

### UDP inputs

- **Raw pose** (`sentinel.edge.pose.v1`, UDP 9999 → server): 33 normalized landmarks + optional optical-flow motion; the server forwards them to `phone` clients as `edge_pose`, where the app's `EdgeKickEngine` detects the kick.
- **SnapKick** (`snapkick.pose.v1`, UDP 5005 → `snapkick_bridge.py`): pre-solved kicks with predicted goal-plane impact; the bridge converts them to striker `kick` messages with metric fields.

---

## 6. The match engine (root `server.py`)

- **Phases:** `lobby → announce → countdown → shoot → resolve` (× `GF_KICKS`) `→ generating → end`. `again` (from `end`) keeps the campaign level; `abort` resets it.
- **AI keeper "THE WALL":** reads the aim trail at `kick_time − keeperReaction` (late feints beat it); with probability scaled by IQ dives at the seen aim, else at the most-frequent historical zone (recency-weighted), else randomly. A rubber-band nudges IQ down when the player converts ≤ 34 % and up at ≥ 80 %.
- **Scoring geometry:** goal 7.32 × 2.44 m; equal L/C/R thirds split at x = ±1.22 m (centers −2.44 / 0 / +2.44 m); post margin 0.15 m. Ring tables: darts (1.73 m bull) 0.10/0.30/0.60/0.95 m → 100/60/30/10; basketball (2.0 m hoop) 0.25/0.55/0.95 m → 100/40/10; radii × campaign `ringScale`.
- **Sport switching:** only in the lobby, and only when no match task is alive.

---

## 7. On-device AI inventory

| Model | Runs on | Ships how |
|---|---|---|
| BlazePose two-stage (detector 128², landmarks 256², w8a8 QNN contexts) | Phone Hexagon NPU (ORT + QNN EP) | In the APK (`android/app/src/main/assets/npu/`) |
| MediaPipe PoseLandmarker lite (`.task`) | Phone GPU/CPU fallback | In the APK |
| Whisper Tiny (encoder+decoder QNN contexts, ~112 MB) | Phone Hexagon NPU | `tools/push_whisper_models.ps1` → app `files/whisper/` |
| Qwen3 0.6B w4a16 (GenieX, ~752 MB) | Phone (optional coach) | `tools/push_qwen_models.ps1` → app `files/qwen/` |
| MediaPipe pose (OpenCV Zoo ONNX, float) | UNO Q CPU (OpenCV DNN) | Placed on the board out-of-band (see [`unoq_pipeline.md`](unoq_pipeline.md)) |
| Depth-Anything-V2 ONNX | Laptop (ORT QNN/CPU) | `fetch_aihub_models.ps1` → `models/` |
| Qwen3-4B-Instruct (GenieX serve) | Laptop | `geniex pull ai-hub-models/Qwen3-4B-Instruct-2507` |
| SDXL-Turbo | Qualcomm Cloud AI100 (remote) | API only (`AI100_API_KEY`) |

Everything degrades gracefully: no NPU → GPU/CPU pose; no Whisper → play without voice; no Qwen → grounded offline coach; no GenieX → template venues + template commentary; no depth model → procedural FX; no AI100 key → procedural artwork.

---

## 8. Privacy boundary

Never on the wire: camera frames, audio, Whisper transcripts, coach lines, `player_profile.json` (biometrics, aim envelope, kick thresholds). On the wire (LAN only): `hello`, `aim`, `kick` scalars/enums, `skel` landmark samples, `start`, and 1 Hz `telem` duty-cycle scalars. The UNO Q preview JPEG (`/edge/frame`) is the only image that crosses the LAN, and only in the optional edge-camera mode - it goes laptop↔board↔phone, never to the internet.
