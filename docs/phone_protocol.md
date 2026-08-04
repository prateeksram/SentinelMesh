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

Older hosts may ignore `height` / `spin` / `strike` / `foot`.

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
