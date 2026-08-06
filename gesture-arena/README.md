# GESTURE ARENA

Body-controlled **football · darts · basketball** on one stadium TV.

Your body is the controller: a **leg swing** takes the penalty, a **hand
throw** launches the dart, a **jump shot** sends the basketball. Motion is
measured on-device (Arduino **UNO Q** pose pipeline and/or a Snapdragon
phone), only tiny JSON crosses the LAN, and the laptop renders a broadcast-
style stadium with an AI goalkeeper, ring targets, commentary and replays.

This folder is the **merged, canonical project** — it combines the original
`ball-game` (UNO Q snapkick trajectory pipeline + object classifier) with
`SentinelMesh-prateek` (match server, AI keeper, stadium TV, Android striker).

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
| `neural_fx.py` / `NEURAL_FX.md` | On-device hero-plate FX for the TV (procedural, optional ONNX/QNN upgrade) |
| `test_combined.py` | End-to-end test: all three sports through bridge + server (self-launching) |
| `test_match.py` | Original phone-striker regression test |
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
```

`test_combined.py` asserts: football geometry gates (wide/post) + keeper
outcomes, exact dart/basketball ring points, sport switching, and the metric
fields surviving the full UDP → bridge → server → state round trip.

## Server knobs (env vars)

| var | default | meaning |
|---|---|---|
| `GF_KICKS` | 5 | attempts per match |
| `GF_SHOOT_WINDOW` | 0 | seconds to act; ≤ 0 waits forever |
| `GF_KEEPER_REACTION` | 0.45 | keeper reads aim this many s before the strike |
| `GF_KEEPER_IQ` | 0.75 | 0 = random · 1 = psychic |
| `GF_ANNOUNCE_S` / `GF_COUNTDOWN_S` / `GF_RESOLVE_S` | 3.5 / 3.0 / 3.8 | pacing |
| `ANTHROPIC_API_KEY` or `GF_LLM_URL` | — | optional live AI commentary desk |

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
