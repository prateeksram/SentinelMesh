# Phone / UNO Q ↔ laptop WebSocket protocol

Strikers speak tiny JSON to the match host. TV / UNO Q can ignore unknown fields.

**Default URL:** `ws://<host>:8080/ws`

**Striker clients:** `phone` (browser / Android app) and `unoq`
(`laptop/snapkick_bridge.py`, which translates the UNO Q's
`snapkick.pose.v1` UDP packets on port 5005 into this protocol).

## Client → server

### `hello`
```json
{ "type": "hello", "client": "phone" }
```
`client` ∈ `phone` | `unoq` | `tv`

### `sport` (TV, lobby only)
```json
{ "type": "sport", "sport": "darts" }
```
`sport` ∈ `football` | `darts` | `basketball`. Football is the keeper duel;
darts / basketball score metric rings around a target (no keeper).

### `aim`
```json
{ "type": "aim", "zone": "L" }
```
`zone` ∈ `L` | `C` | `R`

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
  "foot": "R"
}
```

| Field | Type | Notes |
|---|---|---|
| `zone` | string | `L` / `C` / `R` |
| `power` | float | 0…1 normalized |
| `force` | int | Newtons (ForcePose) |
| `dirDeg` | int | launch elevation cue |
| `height` | string | `H` high / `L` low (optional) |
| `spin` | float | −1…1 lateral cue (optional) |
| `strike` | string | `chip` \| `drive` (optional) |
| `foot` | string | `L` \| `R` swinging foot (optional) |
| `goalX` | float | metric impact at the goal plane, metres lateral, − = left (optional; UNO Q) |
| `goalZ` | float | metric impact height in metres (optional; UNO Q) |
| `apexM` | float | predicted apex in metres (optional; animation) |
| `speed` | float | launch speed m/s (optional; animation) |

Older hosts may ignore `height` / `spin` / `strike` / `foot` / metric fields.

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
`asr`; coach LLM → `npu` if `QWEN` else `cpu` / `llm`. No transcripts or
calibration samples are sent.

### `body_profile`
One small calibration message is sent after connecting and recalibration:
```json
{
  "type": "body_profile",
  "schema": "sentinel.body.profile.v1",
  "heightCm": 180,
  "weightKg": 80,
  "torsoM": 0.52
}
```
Only dimensions needed to scale the display rig leave the phone. Kick
thresholds, aim thresholds, dominant-foot history, and calibration samples
remain on-device.

### `pose_state`
Live display pose, throttled to at most 12.5 Hz by the Android app:
```json
{
  "type": "pose_state",
  "schema": "sentinel.pose.state.v1",
  "timestampMs": 1770000000000,
  "source": "UNO Q",
  "points": [[0.0, 0.0, 0.0], "... 33 MediaPipe joints total ..."]
}
```
The contract is source-neutral. Phone NPU/GPU/CPU and UNO Q all enter through
`PoseAnalyzer.onSkeleton` and send this same payload.

## Server → client

### `state`
```json
{
  "type": "state",
  "phase": "shoot",
  "kick": 2,
  "kicksTotal": 5,
  "score": 1,
  "line": "THE WALL dives…",
  "timerMs": 0,
  "last": { "force": 210, "result": "goal" }
}
```

`phase` ∈ `lobby` | `announce` | `countdown` | `shoot` | `resolve` | `end`  
`last.result` ∈ `goal` | `save` | `post` | `miss` (and synonyms)

### `retarget_state` (TV only)
The laptop converts `pose_state` into a `sentinel.retarget.v1` metric skeleton.
It includes `source`, `backend`, `profile`, `space: "canonical_m"`, and the 33
constrained points in `p`. The TV interpolates it and falls back to recorded
`skel`, then the existing procedural athlete, when stale.

## On-device only (never on the wire)

- Full player calibration profile (`player_profile.json`); only height,
  weight, and torso length are sent to scale the display rig
- Whisper transcripts / Qwen coach lines
- Predictability HUD copy (the strip stays local; **scalars** may leave via `telem`)
- Delegate toggle UI (the chosen unit is implied by which `telem.unit` is reported)
