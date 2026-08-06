# Handoff: Server, Isolation, and Device Kit

> **[corrected — status, 2026-08-06]** Two divergent workspaces now exist. D1–D4 were built and
> pass acceptance in **this** repo (`laptop/server/`, `laptop/engine/`, `tools/unoq_sim.py`,
> `laptop/test_devices.py`). The **`updated/SentinelMesh`** workspace contains **none of them** —
> it kept the 700-line monolith and instead built what §2 explicitly deferred: GenieX integration
> (Desk + SceneEngine, Qwen3-4B) and an ORT/QNN Depth-Anything-V2 path in `neural_fx.py`.
> Neither branch is a superset of the other; they need a merge decision before further work.

**To:** coding agent
**Read first:** `docs/server-architecture.md` (validated — takes precedence on protocol),
`docs/system-plan.md` (design plan — several sections corrected below).
**Assume:** target hardware is provisioned. Snapdragon X Elite laptop with QAIRT, GenieX, dual
Python (ARM64 for runtime, x64 for tooling), ARM64 Chrome. Galaxy S25. Do not write setup or
capability-detection code for these.

---

## 1. What you are building

A device server that owns transport, sessions, and telemetry — and knows nothing about football.
Plus the kit that lets a device that does not exist yet join it.

Four deliverables:

| # | Deliverable | Why now |
|---|---|---|
| D1 | Server core: registry, sessions, capability negotiation, transport abstraction | The foundation everything else attaches to |
| D2 | Engine boundary: server and match logic in separate processes | The decoupling requirement; also makes D1 testable |
| D3 | Telemetry channel + dashboard | Verifies every later change for free. Build it early, not last. |
| D4 | UNO Q device kit: protocol conformance + simulator | Proves the registry handles N devices, and becomes the firmware spec |

## 2. What you are NOT building

Scope guards. If you find yourself here, stop and flag it.

- **No UDP yet.** Build the transport abstraction so UDP slots in later; implement the WebSocket
  adapter only.
- **No GenieX integration, no shader work, no phone pose split.** Those light compute units and come
  after this handoff. Leave clean seams for them.
- **No UNO Q firmware.** The board does not exist. D4 is a simulator plus a spec.
- **No classifier, no dynamic game generation, no 5-kick refactor.** Out of scope entirely.
- **Do not remove the WebSocket path.** The demo works today. Nothing in this handoff may regress it.

---

## 3. Process topology and ownership

Four processes. The line that erodes is the second one — guard it.

| Process | Owns | Must never |
|---|---|---|
| **Server** | Device registry, sessions, capability negotiation, transport, snapshot fan-out, telemetry aggregation, static file serving | Know what a penalty, a kick, or a score is |
| **Engine** | Match state, phases, scoring, keeper logic, commentary orchestration | Open a socket or parse a packet |
| **Inference** | Model session, localhost HTTP, its own NPU telemetry | Block the game loop |
| **Browser** | Stadium render, hero plate, confetti; reports its own GPU timestamps | — |

**Ownership by device — make this explicit in code, not just docs:**

- **Phone owns:** camera frames, ASR audio, pose inference, transcripts. Frames and transcripts
  **never leave the device** (`README_GalaxyS25.md` §14). Emits landmarks (`skel`), aim, discrete kick
  events, and telemetry.
- **Laptop owns:** everything downstream of landmarks. Match state, rendering, commentary, registry.
- **UNO Q will own:** IMU sampling, dive detection, haptics/LEDs. Emits discrete `dive` events and
  telemetry. Receives broadcast commands.

**Engine link.** `InProcLink` is the default (one process, in-memory queue). `TcpLink` exists for CI
and for proving the boundary is real. Same interface, selected by config. Rationale is in
A§16 risk 2 — two processes on stage is a risk, but an unenforced boundary is a worse one.

---

## 4. D1 — Server core

### Registry and sessions

Implement the lifecycle from A§6:

```
REGISTERING → ACTIVE → DEGRADED → LOST → REAPED
                 ↑         |
                 +---------+
```

- `DEGRADED` on missed heartbeats; hold the session and player slot through a grace window. A brief
  network hiccup must not eject a player mid-match.
- Reconnect with the same `device_id` resumes the same session from `DEGRADED`.
- **`HELLO` must be idempotent.** A retry after a lost `WELCOME` returns the existing `session_id`,
  never a new one.

### Capability negotiation

Parse the full descriptor (roles, streams, compute, net, proto_version). Reply with the
**intersection** of what the device offers and what the game needs. A device advertising 60 Hz can be
told 30.

Do not stub this. It is the mechanism behind the next two items.

### Per-device snapshot filtering — fixes a real defect

The registry knows which streams each device declared interest in. Use it: build the outbound
snapshot **per device**, including only what that device subscribed to.

This fixes a live bug the code review found — the phone currently receives the full snapshot
including `replay` frames it never reads, over LAN. The TV is on loopback so it barely matters
there; the phone is not.

This is the correct home for the fix and it makes the registry earn its keep immediately.

### Transport abstraction

```
Transport (ABC)
  ├── WebSocketTransport   ← implement
  └── UdpTransport         ← interface only, raise NotImplementedError
```

The server core must not import `aiohttp` types outside the WS adapter. If it does, the UDP work
later becomes a rewrite.

### Discrete event handling

Per A§5: events carry `event_id`, sender retransmits until ACKed, **server dedups**. At-least-once
plus dedup is effectively-once. The engine must see each event exactly once.

---

## 5. D2 — Engine boundary

Extract match logic out of `server.py` into an engine process.

**Contract, both directions:**

```
server → engine    InputFrame(tick_id, server_time, [DeviceState], [Event])
engine → server    broadcast(payload, target)
```

The engine calls `poll(tick)` and gets a consistent snapshot. It never sees a socket, a session ID,
or a packet.

**Test that proves the boundary:** restart the engine process while devices stay connected. Sessions
survive; the match restarts. If sessions die with the engine, the boundary is not real.

---

## 6. D3 — Telemetry

### Measurement approach — decided

**Self-reported duty cycle is the primary signal.** Each workload measures its own busy time and
reports `busy_ms / window_ms`. Rationale: OS-level per-unit, per-process counters attribute to
Chrome's GPU process rather than to the game, and Android gives essentially nothing per-app for
Adreno or Hexagon without root.

One exception: **the browser uses WebGPU timestamp queries** for true GPU nanoseconds per pass. Real
hardware measurement where it is available.

### Message

Control-plane message on the existing session. One per unit per device per second.

```
{"t": "telem", "unit": "npu", "busy_pct": 18,
 "metric": {"tok_s": 11.2, "queue": 0},
 "temp_c": 47, "state": "llama-3.2-3b-w4a16"}
```

`metric` is **opaque** — an arbitrary object the server stores and forwards without interpreting.
Adding a new unit type must never require a server change.

### Dashboard

Separate route `/telemetry`, not an overlay on `tv.html`. It needs a second monitor during a demo,
and embedding it would pollute the GPU numbers it reports.

**Nine cells, not six.** Render a cell for every unit the plan expects, including the three UNO Q
units. Unregistered units render dark with "not registered". Missing hardware should be visible, not
invisible.

Each cell shows four fields:

1. Duty cycle — the light
2. A domain metric proving the work is real (tok/s, ms/pass, fps) — a percentage alone could be a
   spinlock
3. Temperature
4. A state field (active model, active fallback rung, queue depth)

Plus a **staleness dot**: no telemetry for 3 s → grey, "no data". Distinguishing idle from dead
matters more than it sounds.

### Placeholders are fine

This handoff wires the channel and the dashboard. Real NPU and GPU numbers arrive with the workloads
that produce them. Report what exists now (CPU, tick time, message rates) and leave the rest
reporting zero with a `state: "not implemented"`.

---

## 7. D4 — UNO Q device kit

Two artifacts. Neither requires the board.

### `tools/unoq_sim.py`

A standalone script — roughly 150 lines — that behaves exactly as the firmware will:

- Broadcasts `DISCOVER`, handles `ANNOUNCE`
- Sends `HELLO` with a full UNO Q capability descriptor (roles: `keeper_input`; compute: no NPU;
  three units for telemetry)
- Handles `WELCOME`, stores `session_id`, honours any negotiated downgrade
- Heartbeats at the interval it was given
- Emits `dive` events on keypress or timer, with `event_id`, retransmitting until ACKed
- Reports `telem` for three units at 1 Hz with plausible values
- **Prints every message it receives** — `WELCOME`, ACKs, config changes, broadcast haptic/LED
  commands. The rx path must be visibly exercised, not just tx.
- Reconnects with the same `device_id` and exponential backoff with jitter

Run it with `--dive` and a keeper save should appear in the game. That is the end-to-end proof.

### `docs/device-protocol.md`

The contract to hand whoever writes firmware. Extract from A§5–A§9 plus:

1. Generate a UUID once, persist it, never regenerate.
2. `HELLO` is idempotent — a duplicate response is not an error.
3. Broadcast `DISCOVER` on `:8079`; **never hardcode an IP**. A reflash per network change is not
   acceptable.
4. Declare what you have, not what you think the game wants. `WELCOME` may downgrade you; comply.
5. 20-byte header on every packet, version at byte 0.
6. Cap datagrams at 1200 bytes. Never fragment.
7. Timestamp with your own microsecond clock; **do not correct it**. The server converts.
8. Discrete events: `event_id` + retransmit until ACK. Continuous streams: fire-and-forget,
   latest-wins, never retransmit a stale sample.
9. Never queue. Overwrite the pending sample.
10. Reconnect with the same `device_id`, backoff `min(5000, 250·1.5ⁿ)` ± 20% jitter.
11. Report `telem` per unit at 1 Hz. The MCU reports main-loop duty cycle — there are no OS counters
    there.
12. Do not assume you are the only device, the first device, or player 2.
13. Do not put game rules in firmware. Emit motion; the server decides it was a dive.

---

## 8. Suggested layout

Adapt if the repo disagrees — this is a sketch, not a mandate.

```
laptop/
  server/
    app.py              entrypoint, wiring
    registry.py         devices, sessions, lifecycle
    capabilities.py     descriptor parsing, negotiation
    snapshot.py         per-device InputFrame assembly
    telemetry.py        aggregation
    link.py             InProcLink / TcpLink
    transport/
      base.py           ABC
      ws.py             WebSocket adapter
      udp.py            interface only
    protocol/
      header.py         20-byte pack/unpack
      messages.py       types + codec
      events.py         ACK, retransmit, dedup
  engine/
    match.py            the shootout, moved out of server.py
    commentary.py       orchestration only
  public/
    tv.html
    telemetry.html
  tools/
    unoq_sim.py
```

---

## 9. Acceptance criteria

Each one is a test you can run.

1. Server starts with zero devices connected and does not crash.
2. Phone registers; appears in the registry; its cells light on `/telemetry`.
3. `unoq_sim.py` registers as a third device; three previously-dark cells light.
4. `unoq_sim.py --dive` produces a keeper save in the game.
5. The simulator prints a received broadcast (haptic or LED command) — rx proven.
6. Kill the simulator: cells grey within 3 s, session goes `DEGRADED` then `LOST`, **the match
   continues**.
7. Restart it with the same `device_id`: resumes the same `session_id`.
8. Send a duplicate `HELLO`: same `session_id` returned, no second session created.
9. Send a duplicate `event_id`: the engine sees the event exactly once.
10. Restart the engine process with devices connected: sessions survive.
11. Inspect the phone's outbound snapshot: **no `replay` frames**.
12. The existing demo still works end to end.

Criterion 12 is not negotiable. Nothing here may regress a working demo.

---

## 10. Corrections to apply

The previous review found these. Apply them and mark the sections `[corrected]` in place, as before.

| Doc | Correction |
|---|---|
| `system-plan.md` §5.2 | Change A resolves **two** things, not three. The hero plate goes over HTTP `POST /fx/hero`, not the WebSocket — broadcast amplification is untouched by it. Also record the round trip worth deleting: phone → WS → server → TV → POST the same frames back. |
| `system-plan.md` §5.2 | Change B's targets are mis-ranked. Crowd and pitch lighting are already baked to offscreen canvases. The ball trail is not separable (three z-slots for occlusion). **The real wins are confetti (~2,400 of ~4,000 calls) and the net (re-tessellated every frame though static).** |
| `system-plan.md` §5.1 | "Loopback, so network cost is negligible" is true for the TV only. The phone is on LAN and receives the full snapshot. Fixed by D1's per-device filtering. |
| `system-plan.md` §9 | Add a tag column: **[dev]** verifiable anywhere, **[device]** needs hardware in hand, **[hw]** needs hardware that may not exist. |
| `server-architecture.md` §13 | The `hero()` event-loop stall is resolved by moving to the GPU, not by `ProcessPoolExecutor`. Note the deferral. |

**Two small bugs, in scope if cheap:**

- `neural_fx.py:185-186` PNG-encodes the same buffer twice — `depthPreview` and `plate` are
  byte-identical. Gate the debug one behind a flag or serve the same bytes.
- `tv.html:298` clears `HERO.pending` before `onload` fires, so a broadcast in that window issues a
  duplicate POST.

---

## 11. Non-negotiables

- The engine never opens a socket. The server never knows what a penalty is.
- Camera frames and ASR transcripts never leave the phone.
- `HELLO` is idempotent; sessions survive a brief disconnect.
- No hardcoded device IPs anywhere, including in the simulator.
- The WebSocket path stays working throughout.

---

## 12. Answer from the code, then proceed

1. Where does match state currently live in `server.py`, and what is the smallest cut that separates
   it without a rewrite?
2. Does the existing WS message shape already carry enough for a capability descriptor, or does the
   Android client need a change to send one?
3. What is currently in the snapshot, and which keys does each client actually read? Needed for
   per-device filtering.
4. Is there any existing test infrastructure, or does criterion 12 have to be verified by hand?

Answer these before writing code. If any answer contradicts this brief, flag it rather than working
around it — the last two reviews both found the docs wrong, and that was the useful outcome.
