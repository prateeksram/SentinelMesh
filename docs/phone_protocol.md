# Phone / UNO Q ↔ host WebSocket protocol

Strikers speak tiny JSON to the match host. TV / UNO Q can ignore unknown fields.

**Default URL:** `ws://<host>:8080/ws`

**Striker clients:** `phone` (browser / Android app) and `unoq`
([snapkick_bridge.py](../snapkick_bridge.py), which translates the UNO Q's
`snapkick.pose.v1` UDP packets on port 5005 into this protocol).

**Host:** root [`server.py`](../server.py) + [`public/`](../public/) (canonical).

## Client → server

### `hello`
```json
{ "type": "hello", "client": "phone" }
```
`client` ∈ `phone` | `unoq` | `tv` | `bridge` | `dashboard`  
Optional `roles: ["dashboard"]` also registers a telemetry subscriber.

### `sport`
```json
{ "type": "sport", "sport": "darts" }
```
`sport` ∈ `football` | `darts` | `basketball`. Lobby only. The host does **not**
role-check today — any connected client may send it while `phase == lobby`.

Football is the keeper duel; darts / basketball score metric rings around a
target (no keeper).

### `aim`
```json
{ "type": "aim", "zone": "L" }
```
`zone` ∈ `L` | `C` | `R`. Accepted from striker roles during
`announce` | `countdown` | `shoot`.

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
  "goalX": -1.1,
  "goalZ": 1.4,
  "apexM": 2.1,
  "speed": 18.5,
  "kickState": {
    "schema": "sentinel.kick.state.v1",
    "source": "FORCEPOSE",
    "peakFootSpeedMps": 7.2,
    "lateralVelocityMps": -0.4,
    "upwardVelocityMps": 1.1,
    "pathDisplacementM": 0.55,
    "liftM": 0.12,
    "swingDurationMs": 280,
    "confidence": 0.91
  },
  "trajectory": {
    "schema": "sentinel.trajectory.v1",
    "model": "android",
    "confidence": 0.88,
    "launchVelocity": [1.2, 14.0, 2.0],
    "launchSpeedMps": 14.2,
    "flightTimeS": 0.85,
    "goalX": -1.1,
    "goalZ": 1.4,
    "apexM": 2.1,
    "points": [[0.0, 0.0, 0.0, 0.0], [0.4, -0.5, 8.0, 1.8]]
  }
}
```

| Field | Type | Notes |
|---|---|---|
| `zone` | string | `L` / `C` / `R` (required) |
| `power` | float | 0…1 normalized; missing/`null`/invalid → `0.5` |
| `force` | int | Newtons (ForcePose) |
| `dirDeg` | int | launch elevation cue |
| `height` | string | `H` high / `L` low (optional) |
| `spin` | float | −1…1 lateral cue (optional) |
| `strike` | string | `chip` \| `drive` (optional) |
| `foot` | string | `L` \| `R` swinging foot (optional) |
| `goalX` | float | metric impact at the goal plane, metres lateral, − = left (optional) |
| `goalZ` | float | metric impact height in metres (optional) |
| `apexM` | float | predicted apex in metres (optional; animation) |
| `speed` | float | launch speed m/s (optional; animation) |
| `kickState` | object | `sentinel.kick.state.v1` lower-body metrics (optional; validated server-side) |
| `trajectory` | object | `sentinel.trajectory.v1` sampled path (optional; validated server-side) |

When `trajectory` is present and flat `goalX`/`goalZ`/`apexM`/`speed` are omitted,
the host copies those fields from the trajectory. Flat metric fields take priority
when both are sent (UNO Q / bridge path).

When `goalX`/`goalZ` are present the server referees **hybrid**: geometry
decides wide / post (football) or ring points (darts / basketball); the AI
keeper only contests on-target football shots at the true impact point.
Zone-only kicks fall back to the original probabilistic referee.

### `skel`
Bullet-time skeleton for TV orbit replay:
```json
{
  "type": "skel",
  "kick": 2,
  "frames": [ { "t": -120, "p": [[x,y,z], ...] }, ... ]
}
```

### `telem`
Self-reported silicon duty cycle (~1 Hz while connected). Ingested into
`TelemetryStore` and fanned out as `telem_state` to dashboards / TV.
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

| Field | Type | Notes |
|---|---|---|
| `unit` | string | `cpu` \| `gpu` \| `npu` (UNO Q may also use `mcu`) |
| `source` | string | Workload id; phone uses `pose` / `asr` / `llm` |
| `busy_pct` | number | 0…100 self-reported duty cycle |
| `metric` | object | Opaque scalars (e.g. `pose_ms`, `asr_ms`, `llm_ms`, `backend`) |
| `state` | string | Short label for HUD / dashboard |
| `temp_c` | number | Optional; rarely populated |

Phone mapping: pose lands on the active delegate unit; Whisper ASR → `npu` /
`asr`; coach LLM → `npu` if `QWEN` else `cpu` / `llm`. No transcripts, frames,
or profile data.

### Match controls
```json
{ "type": "start" }
{ "type": "again" }
{ "type": "abort" }
```
- `start` / `again` — begin a match from lobby when a striker is connected.
- `abort` — cancel an in-flight match / campaign generation and return to lobby.

## Server → client

### `state`
Full match snapshot (TV source of truth). Important fields:

```json
{
  "type": "state",
  "phase": "shoot",
  "sport": "football",
  "kick": 2,
  "kicksTotal": 5,
  "score": 1,
  "line": "THE WALL dives…",
  "timerMs": 0,
  "last": { "force": 210, "result": "goal" },
  "shotmap": [],
  "scene": null,
  "postGameReport": null
}
```

`phase` ∈ `lobby` | `announce` | `countdown` | `shoot` | `resolve` | `generating` | `end`

`last.result` / shotmap `result`:
- football: `goal` | `save` | `post` | `miss` | `wide` | `over`
- darts / basketball: `hit` | `miss` (plus `points` on hits)

### `edge_pose`
Server → phone rebroadcast of UNO Q / edge pose packets (compact landmarks).
Phone may use this for on-device coaching when the board owns the camera.

```json
{ "type": "edge_pose", "seq": 12, "landmarks": [ ... ] }
```

### `telem_state`
~1 Hz fan-out from [`telemetry_store.py`](../telemetry_store.py):

```json
{
  "type": "telem_state",
  "stale_ms": 5000,
  "devices": {
    "phone": {
      "device": "phone",
      "label": "phone",
      "units": {
        "npu": { "busy_pct": 42.5, "metric": {}, "temp_c": null, "state": "pose:NPU", "age_ms": 120 }
      }
    }
  }
}
```

## On-device only (never on the wire)

- Player calibration profile (`player_profile.json`)
- Whisper transcripts / Qwen coach lines
- Predictability HUD copy (the strip stays local; **scalars** may leave via `telem`)
- Delegate toggle UI (the chosen unit is implied by which `telem.unit` is reported)
