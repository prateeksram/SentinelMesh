# Phone ↔ laptop WebSocket protocol

Phone (Player 1) speaks tiny JSON to the match host. TV / UNO Q can ignore unknown fields.

**Default URL:** `ws://<host>:8080/ws`

## Client → server

### `hello`
```json
{ "type": "hello", "client": "phone" }
```

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
  "foot": "R",
  "kickState": {
    "schema": "sentinel.kick.state.v1",
    "source": "UNO Q",
    "peakFootSpeedMps": 4.2,
    "lateralVelocityMps": -1.3,
    "upwardVelocityMps": 0.8,
    "pathDisplacementM": 0.31,
    "liftM": 0.12,
    "swingDurationMs": 360,
    "confidence": 0.82
  },
  "trajectory": {
    "schema": "sentinel.trajectory.v1",
    "model": "sentinel.pose-ballistic.v1",
    "confidence": 0.72,
    "launchVelocity": [-1.8, 17.1, 4.1],
    "launchSpeedMps": 17.68,
    "flightTimeS": 0.71,
    "goalX": -2.1,
    "goalZ": 1.2,
    "apexM": 1.35,
    "points": [[0.0, 0.0, 0.0, 0.11], [0.06, -0.1, 1.0, 0.34]]
  }
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
| `kickState` | object | source-neutral peak swing state (optional) |
| `trajectory` | object | sampled visualization trajectory (optional) |

`kickState.source` is diagnostic metadata (`NPU`, `GPU`, `CPU`, or `UNO Q`).
All remaining state fields use the same coordinate convention and units for
every backend: lateral velocity is positive toward image right, upward velocity
is positive up, and lengths are torso-calibrated metres.

Trajectory points are compact arrays `[timeSeconds, lateralX, forwardY,
heightZ]`, with metres for every spatial coordinate. The goal plane is 11 m
forward. `trajectory.confidence` describes pose/state quality; the current
server uses the path for visualization only and retains legacy scoring.

Older hosts may ignore every optional field and continue using
`zone` / `power` / `force` / `dirDeg`.

### `skel`
Bullet-time skeleton for TV orbit replay:
```json
{
  "type": "skel",
  "kick": 2,
  "frames": [ { "t": -120, "p": [[x,y,z], ...] }, ... ]
}
```

## Server → client

### `edge_pose` (optional UNO Q source)

When the phone selects `UNO Q`, the host forwards full 33-point MediaPipe
frames received on UDP `9999` (landmark array abbreviated below):

```json
{
  "type": "edge_pose",
  "schema": "sentinel.edge.pose.v1",
  "seq": 42,
  "t_capture_ns": 123456789,
  "frame": { "width": 640, "height": 480, "rotation": 0, "mirrored": true },
  "landmarks": [[0.50, 0.12, -0.03, 0.99]],
  "motion": {
    "t_ns": 123456999,
    "fps": 29.4,
    "left": {
      "vx": -0.22, "vy": -0.31,
      "peak_vx": -0.81, "peak_vy": -0.55,
      "dx": -0.06, "dy": -0.04,
      "confidence": 0.88, "samples": 3
    }
  },
  "diagnostics": { "fps": 12.0, "inference_ms": 72.4, "backend": "uno-q" }
}
```

`landmarks` contains either zero entries (camera alive, no person) or exactly
33 entries in MediaPipe index order. Each point is `[x, y, z, visibility]`.
`motion` is optional and backward-compatible. Its velocities and displacement
are normalized image units relative to the pelvis, measured by optical flow
between pose anchors. The phone aspect-corrects and torso-scales them before
using them as a high-rate supplement to landmark motion.
The matching latest-frame camera endpoints are `/edge/frame.jpg` and
`/edge/camera.mjpg`. Match clients that do not recognize this message ignore
it.

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
  "last": { "force": 210, "result": "goal" },
  "postGameReport": { "status": "generating" }
}
```

`phase` ∈ `lobby` | `announce` | `countdown` | `shoot` | `resolve` | `end` | `generating`

During `announce` / `countdown`, phone streams live `aim`. When phase becomes `shoot`,
aim is **locked** (server freezes `aimLive` / `aimLocked`; phone stops updating zone for
kick). Feints after the whistle no longer change the shot corner.

`last.result` ∈ `goal` | `save` | `post` | `miss` (and synonyms)

At full time, `postGameReport.status` moves from `generating` to `ready`.
The ready object contains short-lived `landingUrl`, `pngUrl`, `pdfUrl`, and
`qrUrl` paths plus a small preview. Report statistics are calculated from the
server's accumulated `shotmap`; no extra phone payload or camera upload is
required.

## On-device only (never on the wire)

- Player calibration profile (`player_profile.json`)
- Whisper transcripts / Qwen coach lines
- Predictability HUD / NEURAL LOAD strip
- Delegate toggle (CPU / GPU / NPU)
