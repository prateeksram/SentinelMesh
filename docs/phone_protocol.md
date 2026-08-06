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
  "last": { "force": 210, "result": "goal" },
  "postGameReport": { "status": "generating" }
}
```

At full time, `postGameReport.status` moves from `generating` to `ready`.
The ready object contains short-lived `landingUrl`, `pngUrl`, `pdfUrl`, and
`qrUrl` paths plus a small preview. Report statistics are calculated from the
server's accumulated `shotmap`; no extra phone payload or camera upload is
required.

`phase` ∈ `lobby` | `announce` | `countdown` | `shoot` | `resolve` | **`generating`** | `end`  
`last.result` ∈ `goal` | `save` | `post` | `over`

**[corrected]** This doc had drifted from its own server (`laptop/server.py`):

- The four-pillar server inserts a **`generating`** phase between the last kick and `end`
  while the SceneEngine designs the next venue. Both phone clients now handle it
  (`phone.html`, `MainActivity.kt onState` — "NEXT VENUE" screen).
- The `state` snapshot also carries `saves`, `shotmap`, `aimLive`, `replay`, `llm`,
  `connected`, and the campaign keys **`level`, `scene`, `report`, `genProgress`,
  `sceneMetrics`**. Unknown fields must be ignored. Since the registry landed, the
  phone's snapshot is **filtered**: it never carries `replay`, `shotmap`, `report`,
  or `genProgress`.
- Clients may also send `start` (the app's spoken "ready" does), `again`, and `abort`;
  this doc previously omitted all three. Devices with a `device_id` in their hello get
  a `welcome` (session resume, negotiation) — see [`device-protocol.md`](device-protocol.md).
- `last.result` is `over` for a missed window, not `miss`.

## On-device only (never on the wire)

- Player calibration profile (`player_profile.json`)
- Whisper transcripts / Qwen coach lines
- Predictability HUD / NEURAL LOAD strip
- Delegate toggle (CPU / GPU / NPU)
