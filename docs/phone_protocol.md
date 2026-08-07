# Wire protocol - WebSocket and UDP

Complete message reference for the root host ([`server.py`](../server.py)). Strikers speak tiny JSON; the server tolerates and ignores unknown fields, so clients may extend messages freely.

**WebSocket URL:** `ws://<host>:8080/ws` (or `wss://<host>:8443/ws` when certs are installed).

---

## 1. Roles and permissions

Every client introduces itself once:

```json
{ "type": "hello", "client": "phone" }
```

| `client` | Who | May send |
|---|---|---|
| `phone` | Android app or `phone.html` | `aim`, `kick`, `skel`, `start`, `telem` |
| `unoq` | `snapkick_bridge.py` | `aim`, `kick`, `skel`, `telem` |
| `bridge` | alias - treated as `unoq` | same |
| `tv` | `tv.html` | `sport`, `start`, `again`, `abort`, `telem` |
| `dashboard` | telemetry viewers | receives `telem_state` only |

Unknown `client` values are silently ignored (the socket stays open but is never registered). Only `phone` and `unoq` are **strikers**; the first striker `kick` in each shoot window counts.

---

## 2. Client → server

### `sport` - lobby only

```json
{ "type": "sport", "sport": "darts" }
```

`sport` ∈ `football` | `darts` | `basketball`. Accepted only in the `lobby` phase **and** when no match task is still winding down; otherwise silently dropped. The host does **not** role-check this message today - any connected client may send it during the lobby (the TV's sport buttons are the intended sender). Football is the keeper duel; darts/basketball score metric rings (no keeper).

### `start` / `again` / `abort` - match control

| Type | Valid phase | Effect |
|---|---|---|
| `start` | `lobby`, striker connected | Begin the match |
| `again` | `end` | Rematch - campaign level and difficulty are **kept** |
| `abort` | any phase except `lobby` | Cancel and reset - campaign level is **wiped** |

`start` may come from the TV or from a striker (the phone sends it on a spoken "ready").

### `aim`

```json
{ "type": "aim", "zone": "L" }
```

`zone` ∈ `L` | `C` | `R`. Send at most ~5 Hz. The keeper samples the aim trail `keeperReaction` seconds before the strike, so late zone changes (feints) beat it.

### `kick`

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
  "foot": "R",
  "goalX": -2.1,
  "goalZ": 1.6,
  "apexM": 2.2,
  "speed": 18.5,
  "kickState": { "schema": "sentinel.kick.state.v1", "...": "..." },
  "trajectory": { "schema": "sentinel.trajectory.v1", "...": "..." }
}
```

Accepted only from a striker during the `shoot` phase; the first kick wins.

| Field | Type / range | Required | Notes |
|---|---|---|---|
| `zone` | `L`\|`C`\|`R` | yes | Aim zone at release |
| `power` | float 0…1 | no (0.5) | Normalized effort |
| `force` | int (N) | no | ForcePose Newtons |
| `dirDeg` | int | no | Launch elevation cue |
| `height` | `H`\|`L` | no | High / low band |
| `spin` | float −1…1 | no | Lateral spin cue |
| `strike` | `chip`\|`drive` | no | Trajectory class |
| `foot` | `L`\|`R` | no | Swinging foot |
| `goalX` | float −12…12 (m) | no | Metric impact, lateral at the goal plane (− = left) |
| `goalZ` | float 0…10 (m) | no | Metric impact height |
| `apexM` | float 0…12 (m) | no | Predicted apex (animation) |
| `speed` | float 1…45 (m/s) | no | Launch speed (animation) |

**Referee selection:** when `goalX`/`goalZ` are present the server referees **hybrid** - geometry decides wide/post (football) or ring points (darts/basketball), and the AI keeper contests only on-target football shots at the true impact point. Zone-only kicks fall back to the probabilistic referee (football) or a synthesized impact (target sports). Flat `goalX`/`goalZ`/`apexM`/`speed` take priority over trajectory-derived values.

#### `kickState` sub-object (`sentinel.kick.state.v1`)

Validated kinematics at the swing peak - same shape from ForcePose, HandThrow, and EdgeKick engines:

| Field | Range | Required |
|---|---|---|
| `peakFootSpeedMps` | 0…15 | yes |
| `confidence` | 0…1 | yes |
| `lateralVelocityMps`, `upwardVelocityMps` | ±15 | no |
| `pathDisplacementM` | 0…3 | no |
| `liftM` | 0…2 | no |
| `swingDurationMs` | 0…2500 | no |
| `source` | string ≤ 24 chars | no | 

#### `trajectory` sub-object (`sentinel.trajectory.v1`)

Visualization-only ballistic path (see [`trajectory_pipeline.md`](trajectory_pipeline.md)):

| Field | Range / shape | Notes |
|---|---|---|
| `confidence` | 0…1 | Below 0.25 the TV falls back to the legacy arc |
| `flightTimeS` | 0.1…3 | |
| `launchSpeedMps` | 1…45 | |
| `launchVelocity` | exactly 3 floats, forward component ≥ 0 | |
| `goalX`, `goalZ`, `apexM` | as above | |
| `points` | ≤ 48 samples of `[t, lateral, forward, height]` | `t` strictly increasing, forward non-regressing; **≥ 2 valid points or the whole trajectory is rejected** |

### `skel` - bullet-time replay

```json
{ "type": "skel", "kick": 2, "frames": [ { "t": -120, "p": [[x, y, z]] } ] }
```

`kick` must match the current kick number. Frames are landmark samples (~1 s window before the strike), truncated server-side to 40; total JSON must stay under 200 000 chars. Landmarks only - never video.

### `telem` - silicon duty cycle (~1 Hz)

```json
{
  "type": "telem",
  "unit": "npu",
  "source": "pose",
  "busy_pct": 42.5,
  "metric": { "pose_ms": 18, "fps": 28 },
  "state": "pose:NPU"
}
```

| Field | Notes |
|---|---|
| `unit` | `cpu` \| `gpu` \| `npu` (UNO Q may use `mcu`) |
| `source` | workload id - phone uses `pose` / `asr` / `llm` |
| `busy_pct` | 0…100 self-reported duty cycle |
| `metric` | opaque scalars (`pose_ms`, `asr_ms`, `llm_ms`, `backend`, …) |
| `state` | short HUD label |
| `temp_c` | optional |

Sender roles are remapped to device keys: `tv` → `laptop`, `bridge` → `unoq`. No transcripts, frames, or profile data ride on `telem`.

---

## 3. Server → client

### `state` - the full snapshot (broadcast on every change)

```json
{
  "type": "state",
  "phase": "shoot",
  "sport": "football",
  "kick": 2,
  "kicksTotal": 5,
  "score": 1,
  "saves": 0,
  "shotmap": [ { "zone": "L", "keeperZone": "C", "force": 210, "result": "goal" } ],
  "aimLive": "L",
  "timerMs": 0,
  "last": { "force": 210, "result": "goal" },
  "replay": { "kick": 1, "frames": [] },
  "line": "THE WALL dives...",
  "connected": { "phone": true, "unoq": false },
  "level": 2,
  "scene": { "atmosphere": {}, "difficulty": {} },
  "genProgress": 0,
  "sceneMetrics": { "source": "template" },
  "ringScale": 0.9,
  "postGameReport": { "status": "ready", "qrUrl": "..." }
}
```

- `phase` ∈ `lobby` | `announce` | `countdown` | `shoot` | `resolve` | `generating` | `end`
- `last.result` ∈ `goal` | `save` | `post` | `wide` | `miss` | `hit` | `over` (`over` = shoot window expired)
- `postGameReport.status` ∈ `generating` | `ready` | `error` (AI100 report card; `qrUrl`, `landingUrl`, `pngUrl`, `pdfUrl` when ready)

### `edge_pose` - forwarded UNO Q landmarks (phone clients only)

The server relays every valid `sentinel.edge.pose.v1` UDP packet to connected `phone` clients as `{"type": "edge_pose", ...}` so the app's UNO Q mode can run its kick detector against the remote camera. TV and dashboards never receive it.

### `telem_state` - merged telemetry (~1 Hz, to `tv` + `dashboard`)

```json
{ "type": "telem_state", "stale_ms": 3000, "devices": { "phone": { "units": {} } } }
```

**Client caution:** strikers and TVs receive multiple message types - always dispatch on `type` rather than assuming every frame is a `state`.

---

## 4. UDP inputs

### `sentinel.edge.pose.v1` → server UDP 9999

One JSON datagram per inference from [`unoq/sentinel_pose_streamer.py`](../unoq/sentinel_pose_streamer.py):

Packets may also include a `telemetry` object sampled at 1 Hz from Linux
procfs/sysfs: board CPU, pose-process CPU, memory, temperature, and an optional
real Adreno utilization counter. The host validates these scalars, maps them to
the `unoq` telemetry device, and publishes liveness/metrics to the TV at 1 Hz.
GPU utilization is nullable because not every UNO Q kernel exposes KGSL/devfreq
counters to the unprivileged `arduino` user.

```json
{
  "schema": "sentinel.edge.pose.v1",
  "seq": 120,
  "t_capture_ns": 123456789,
  "frame": { "width": 640, "height": 480, "rotation": 0, "mirrored": true },
  "landmarks": [[0.51, 0.42, 0.0, 0.97]],
  "motion": { "t_ns": 123456789, "fps": 27.0, "left": { "vx": -0.3, "confidence": 0.9, "samples": 4 } },
  "diagnostics": { "fps": 9.8, "inference_ms": 41.0, "backend": "uno-q-mediapipe-onnx-opencv" },
  "telemetry": { "cpu_pct": 72.5, "process_cpu_pct": 188.0, "memory_pct": 43.0, "temperature_c": 58.0, "gpu_pct": 7.5, "gpu_source": "kgsl" }
}
```

`landmarks` must contain **exactly 0 or 33** entries of `[x, y, z, visibility]`. Packets are dropped unless `t_capture_ns` increases (or the stream was silent > 2 s). `motion` carries per-foot Lucas–Kanade optical-flow velocities that let the phone's `EdgeKickEngine` bridge the board's low pose FPS.

### `snapkick.pose.v1` → `snapkick_bridge.py` UDP 5005

Pre-solved kick packets (SnapKick project format): `seq`, `people[]` with `score`, `kick_candidate` + `kick_confidence` (gate ≥ 0.60, 0.8 s cooldown per `track_id`), and a `trajectory` block whose `predicted_goal_x` / `predicted_goal_z` (metres at the goal plane) become the kick's metric impact. The bridge converts these into striker `kick` messages; [`snapkick_sim.py`](../snapkick_sim.py) fakes them for hardware-free demos.

---

## 5. On-device only (never on the wire)

- Player calibration profile (`player_profile.json`: biometrics, torso scale, aim envelope, kick thresholds, dominant foot)
- Camera frames and audio; Whisper transcripts; coach lines
- Predictability HUD copy (only telemetry *scalars* leave, via `telem`)
