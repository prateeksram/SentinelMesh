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

## On-device only (never on the wire)

- Player calibration profile (`player_profile.json`)
- Whisper transcripts / Qwen coach lines
- Predictability HUD / NEURAL LOAD strip
- Delegate toggle (CPU / GPU / NPU)
