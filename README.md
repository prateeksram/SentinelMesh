# GESTURE ARENA

Body-controlled **football · darts · basketball** on one stadium TV.

Your body is the controller: a **leg swing** takes the penalty, a **hand
throw** launches the dart, a **jump shot** sends the basketball. Motion is
measured on-device (Arduino **UNO Q** pose pipeline and/or a Snapdragon
phone), only tiny JSON crosses the LAN, and the laptop renders a
broadcast-style venue that follows the sport — floodlit stadium, wood-panelled
darts hall, or indoor arena with a parquet court — with an AI goalkeeper,
ring targets, commentary, replays, and a **GenieX-driven campaign** that
redesigns the venue and raises the difficulty after every match.

This folder is the **merged, canonical project** — it combines the original
`ball-game` (UNO Q snapkick trajectory pipeline + object classifier),
`SentinelMesh-prateek` (match server, AI keeper, stadium TV, Android striker)
and the SceneEngine branch (GenieX venue design + campaign difficulty).

---

## Architecture

```
                    ┌──────────────────────── LAPTOP ────────────────────────┐
UNO Q pose pipeline │                                                        │
(snapkick.pose.v1)  │  snapkick_bridge.py ──ws "unoq"──┐                     │
 ──UDP :5005──────────►  (UDP → WebSocket)             ▼                     │
                    │                            server.py :8080             │
Phone (Android app  │                            · match engine (5 attempts) │
or public/phone.html│  ──ws "phone"─────────────► · hybrid referee           │
in a browser)       │                            · AI keeper (THE WALL)      │
                    │                            · AI commentary desk        │
                    │                                   │ ws "tv"            │
                    │                                   ▼                    │
                    │                        public/tv.html (stadium TV)     │
                    └────────────────────────────────────────────────────────┘
```

Everything runs on one Wi-Fi / hotspot. No internet needed for the game
(the optional LLM commentary desk is the only cloud-touching feature).

## Folder layout

| Path | What it is |
|---|---|
| `server.py` | Match host: WebSocket hub, game engine, hybrid referee, AI keeper, commentary |
| `snapkick_bridge.py` | UNO Q adapter: `snapkick.pose.v1` UDP :5005 → striker client over WebSocket |
| `snapkick_sim.py` | Fake UNO Q — schema-faithful packets, a kick every ~4 s (test without hardware) |
| `public/tv.html` | The stadium TV: all three sports, sport-true striker animation, replays, VFX |
| `public/phone.html` | Browser striker fallback (MediaPipe pose + ForcePose in the phone browser) |
| `neural_fx.py` / `NEURAL_FX.md` | Hero-plate FX for the TV: procedural, or Depth-Anything-V2 via ONNX/QNN if a model is in `models/` |
| `scene_engine.py` / `SCENE_ENGINE.md` | SceneEngine: GenieX designs the next venue + difficulty after full time |
| `geniex_client.py` | Shared async GenieX client (commentary desk + SceneEngine) |
| `test_combined.py` | End-to-end test: all three sports + campaign progression through bridge + server (self-launching) |
| `test_match.py` | Original phone-striker regression test |
| `test_scene_gen.py` / `e2e_sim.py` | SceneEngine smoke test / scripted match simulator |
| `docs/phone_protocol.md` | The WebSocket protocol (phone / unoq / tv clients) |
| `android/` | Native Android striker app (Hexagon NPU pose, ForcePose, Whisper voice, private coach) |
| `tools/` | Model push/fetch scripts for the Android app + legacy UNO Q IMU sender |
| `classifier/` | **Future layer**: phone object classifier (show a ball → arena switches). Not wired in yet |
| `models/` | Drop `hero_depth.onnx` here to upgrade TV neural FX (optional) |

---

## Setup (laptop, one-time)

Needs **Python 3.13** (`py -3.13` on Windows) and one dependency:

```powershell
cd gesture-arena
py -3.13 -m pip install -r requirements.txt
```

macOS / Linux: `python3 -m pip install -r requirements.txt`.

Firewall: allow Python inbound on **TCP 8080** (TV/phone/WS) and **UDP 5005**
(UNO Q). For the *browser* phone striker's camera you also need HTTPS :8443 —
generate certs next to `server.py`:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 -subj "/CN=gesture-arena"
```

(The native Android app and the TV do **not** need HTTPS.)

## Run it

**Terminal 1 — match host + TV:**
```powershell
py -3.13 server.py
```
Open `http://localhost:8080/tv.html` on the TV/laptop screen.

**Terminal 2 — UNO Q bridge:**
```powershell
py -3.13 snapkick_bridge.py
```
Options: `--host <ip>:8080` if the server is on another machine,
`--udp-port` if the board sends elsewhere. The TV's **UNO Q** LED turns
green when the bridge connects.

**Terminal 3 — motion source (pick one):**

| Source | How |
|---|---|
| No hardware (demo/test) | `py -3.13 snapkick_sim.py` — random kick every ~4 s |
| Real UNO Q | Point its pipeline's UDP output at `<laptop-ip>:5005`. No code changes — the bridge accepts `snapkick.pose.v1` as-is |
| Phone (native app) | Install the `android/` app, set HOST to `<laptop-ip>:8080`, tap HOST |
| Phone (browser) | `https://<laptop-ip>:8443/phone.html` (accept the cert warning) |

Phone and UNO Q can be connected **at the same time** — the first action in
each shoot window counts.

**On the TV:** pick a sport in the lobby (⚽ 🎯 🏀), then **START MATCH**.
5 attempts per match; **PLAY AGAIN** re-enters the lobby where you can switch
sport.

## The three sports — gesture, referee, scoring

The UNO Q pipeline always speaks the same `snapkick.pose.v1` schema — whatever
physical gesture its model detects becomes `kick_candidate` + a solved
`trajectory` (impact point in metres at the target plane). The TV mirrors the
gesture: the on-pitch athlete **kicks**, **throws**, or **rises into a jump
shot** to match the sport, and the projectile (ball / dart / basketball)
leaves from the right place — turf, ear height, or overhead.

### ⚽ Football — hybrid referee
1. **Geometry first** (from `goalX`/`goalZ`, the real trajectory impact):
   outside the 7.32 × 2.44 m goal → **WIDE**; within 15 cm of the frame →
   **POST**. The keeper never touches off-target shots.
2. **THE WALL second**: on-target shots face the AI keeper. It read your aim
   hand ~0.45 s before the strike (late feints beat it), studies your shot
   history, and dives at the goal-mouth third your ball is heading for.
   Same-third dives usually save — unless the impact is deep in the corner or
   `power` beats the glove (> 0.82).
3. Scorebug: goals vs saves.

### 🎯 Darts — ring geometry, no keeper
Metric rings around the bull at 1.73 m: **≤0.10 m = 100 · ≤0.30 m = 60 ·
≤0.60 m = 30 · ≤0.95 m = 10**, else miss. Darts fly fast and flat, and stick
in the board. Scorebug: points vs misses.

### 🏀 Basketball — ring geometry, no keeper
Rings around the hoop centre at 2.0 m: **≤0.25 m = 100 (swish) ·
≤0.55 m = 40 · ≤0.95 m = 10**. Scorebug: points vs misses.

Strikers that only send a zone (phone browser / Android today) still work
everywhere: football falls back to the original probabilistic referee, and
target sports synthesise an impact from zone + height + power.

## Campaign & SceneEngine

After every full time the server enters a **NEXT VENUE** phase: SceneEngine
feeds your match stats to **GenieX** (local Qwen3, OpenAI-compatible at
`GF_GENIEX_URL`) which designs the next venue — time of day, sky, floodlights,
crowd energy, a scouting report — **and the difficulty**:

| Knob | Applies to | Effect as levels rise (1 → 5) |
|---|---|---|
| `keeperIq` / `keeperReaction` | football | THE WALL reads you better, reacts faster |
| `powerBeat` | football | harder to beat the glove with raw power |
| `ringScale` | darts / basketball | scoring rings shrink 1.0× → 0.6× — same throw, fewer points |
| `shootWindow` | all sports | level 3+ puts you on the clock (3.0 → 2.2 s) |

Campaign level never regresses on **PLAY AGAIN / NEXT VENUE** (progression is
goals in football, on-target count in darts/basketball); **END MATCH** (abort)
resets the campaign to level 1. With GenieX offline everything still works —
template venues and the same difficulty curve, badge shows `SCENE · TEMPLATE`.

The TV venue also **follows the sport**: floodlit stadium for football, a
wood-panelled darts hall with pendant lamps and a brass bar rail, and an
indoor arena with trusses, light banks and a parquet court for basketball —
with the scene atmosphere (sky, tint, lights, crowd) layered on top.

## Protocol

Full spec: [`docs/phone_protocol.md`](docs/phone_protocol.md). Summary:

- Clients `hello` as `phone`, `unoq`, or `tv`.
- Strikers stream `{"type":"aim","zone":"L|C|R"}` and fire one
  `{"type":"kick", zone, power, force, dirDeg, height, spin, strike, foot,
  goalX, goalZ, apexM, speed}` per shoot window (metric fields optional —
  the UNO Q bridge always includes them).
- TV sends `{"type":"sport"}` (lobby), `start`, `again`, `abort`.
- Server broadcasts full `state` snapshots to everyone.

The bridge's UNO Q input contract is exactly what `snapkick_sim.py` emits:
`kick_confidence ≥ 0.60` gates a kick, 0.8 s per-track cooldown, and
`trajectory.predicted_goal_x/z` (metres; x lateral, − = left; z = height)
is authoritative.

## Testing

```powershell
py -3.13 test_combined.py    # all three sports end-to-end (launches its own server + bridge)
py -3.13 test_match.py       # phone-striker regression (start server.py first, fast GF_* env)
py -3.13 test_scene_gen.py   # SceneEngine smoke: score 1/5 vs 3/5 → different venues/difficulty
```

`test_combined.py` asserts: football geometry gates (wide/post) + keeper
outcomes, exact dart/basketball ring points, sport switching, the metric
fields surviving the full UDP → bridge → server → state round trip, **and the
campaign** — level from goals / on-target count, scene generated at full time,
`ringScale` applied to the target-sport referee.

## Server knobs (env vars)

| var | default | meaning |
|---|---|---|
| `GF_KICKS` | 5 | attempts per match |
| `GF_SHOOT_WINDOW` | 0 | seconds to act; ≤ 0 waits forever |
| `GF_KEEPER_REACTION` | 0.45 | keeper reads aim this many s before the strike |
| `GF_KEEPER_IQ` | 0.75 | 0 = random · 1 = psychic |
| `GF_ANNOUNCE_S` / `GF_COUNTDOWN_S` / `GF_RESOLVE_S` | 3.5 / 3.0 / 3.8 | pacing |
| `GF_GENIEX` | 1 | set 0 to skip the GenieX desk (falls to local/cloud/templates) |
| `GF_GENIEX_URL` / `GF_GENIEX_MODEL` | `http://127.0.0.1:18181/v1` / Qwen3-4B W4A16 | GenieX endpoint + model id |
| `GF_SCENE_TIMEOUT_S` / `GF_SCENE_MAX_LEVEL` | 90 / 5 | scene generation timeout · campaign cap |
| `ANTHROPIC_API_KEY` or `GF_LLM_URL` | — | fallback AI commentary desks |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Bridge: `WinError 10048` on UDP 5005 | Something else owns the port — usually a leftover `game_server.py` from the old ball-game. Kill stray `python` processes |
| Random kicks you didn't make | A forgotten `snapkick_sim.py` is still running somewhere — kill it |
| TV UNO Q LED red | Bridge not running, or wrong `--host` |
| START MATCH greyed out | No striker connected (need phone **or** UNO Q green) |
| Board sends but nothing happens | Firewall blocking UDP 5005, wrong laptop IP on the board — test with `snapkick_sim.py` on the laptop first |
| Phone browser camera black | You used `http://` — camera needs the `https://:8443` page (or use the native app) |
| Kicks double-count / ghost kicks | Raise the cooldown / confidence constants at the top of `snapkick_bridge.py` |
| Sport buttons do nothing | Sport can only change in the **lobby** (END MATCH first) |

## Roadmap (agreed next steps)

- **Object classifier layer** (`classifier/`): phone camera recognises the
  physical ball (football / basketball / dart) and switches the arena
  automatically — the training + TF.js export pipeline is ready in the
  folder; it needs a `POST /api/object` route on `server.py` and a
  `sport` broadcast, mirroring how the TV lobby buttons work today.
- Android app: send `goalX`/`goalZ` from its ForcePose trajectory for
  hybrid-refereed phone kicks; sport-aware gesture detection (throw / shot).
