# Game Server Architecture

**Status:** Validated against the codebase as of 2026-08-06 (`laptop/server.py` 588 lines, `android/` app v0.1.0, no VCS).
**Scope:** Device transport, session management, and load shedding. Explicitly *not* game mechanics.
**Supersedes:** [`docs/phone_protocol.md`](docs/phone_protocol.md), which describes the transport this design replaces.

> This document was previously a design draft carrying six open questions. Those questions are now
> answered from the code in §2, and every section below has been re-checked against what actually
> ships. Sections the draft got wrong are marked **[corrected]**; sections that no longer earn their
> keep are marked **[cut]** with the reason. The scope ledger in §14 is the summary.

---

## 1. Goals and non-goals

### Goals

- Devices self-register with the server, declaring role and capabilities.
- Transport is UDP **for sensor devices**. Packet loss is tolerated by design, not eliminated.
- The server is fully decoupled from the game engine — it can be developed, tested, restarted, and
  reasoned about independently.
- Bounded per-device memory, so device count scales linearly.
- Resolve the four transport defects catalogued in §13. These are the concrete, non-speculative
  justification for the work.

### Non-goals

- The server does not know about players, scores, rules, or game state.
- The server does not implement gesture estimation or pose detection. It transports and timestamps
  their output.
- The server does not guarantee delivery of continuous sensor streams.
- **The server does not offload inference.** See §2.3 and §12 — there is nothing to offload.

---

## 2. Ground truth: the system as built

The draft could not answer these. The code can. Every design decision downstream depends on them,
so they lead.

### 2.1 Engine language and runtime

**Python 3 / asyncio.** [`laptop/server.py`](laptop/server.py) is single-threaded aiohttp with one
module-level `Game` singleton. There is no second language in the host stack; `requirements.txt` is
`aiohttp>=3.9` and nothing else.

**Consequence:** the server/engine boundary is **loopback TCP**, not shared memory and not FFI.
Unix domain sockets are ruled out because the development machine is Windows and asyncio's
`ProactorEventLoop` does not support them — a portable boundary cannot use UDS or named pipes.
Loopback TCP costs ~50–100 µs per round trip, four orders of magnitude inside any budget here.

### 2.2 What the pose and gesture modules emit

**There is no continuous pose stream.** This is the single most important correction in this
document, because the draft's central abstraction assumed one. What the phone actually emits:

| Traffic | Shape | Rate | Nature |
|---|---|---|---|
| `aim` | one enum, `L`/`C`/`R` | ≤5 Hz (200 ms throttle, [`MainActivity.kt:902-906`](android/app/src/main/java/com/sentinelmesh/gesturefootball/MainActivity.kt#L902-L906)) | continuous-ish, latest-wins |
| `kick` | 8 scalars | once per kick | discrete, must not be lost |
| `skel` | ≤26 frames × 33 joints × 3 floats | once per kick, sent **450 ms after** it | bulk, latency- and loss-tolerant |

Payload sizing follows from this table, not from a 60 Hz keypoint assumption. `skel` at float32 is
~10 KB — 8× over the datagram cap in §8 — and is the reason §4 needs a fourth channel.

### 2.3 Where inference runs

**Entirely on the phone.** Two-stage BlazePose on the Hexagon NPU via ONNX Runtime QNN, with a
MediaPipe GPU/CPU fallback ladder; Whisper Tiny on the same HTP path; a grounded coach with an
optional Qwen3 0.6B backend. The laptop runs **no ML at all** — `laptop/neural_fx.py` is pure-Python
per-pixel splatting, and its ONNX/QNN branch is a labelled placeholder that returns the procedural
plate regardless.

**Consequence:** there is no GPU/NPU utilization signal to sample and no local workload to shed.
§12 is reduced accordingly.

### 2.4 The remote inference endpoint

**Unknown, and unreachable.** No endpoint, no URL, no credentials, no client code exist anywhere in
the repository. Rung 4 cannot be specified, let alone built. See §12.

### 2.5 Target device count

**Three:** phone (Player 1), Arduino UNO Q (Player 2), TV. Design for eight. Not arbitrary. The
draft's sharding and `io_uring` provisions are sized for a system three orders of magnitude larger
than this one.

### 2.6 UNO Q: Linux side or MCU side?

**Still unknown — and this design is written so it does not matter.** There is no `.ino`, no
firmware directory, no code of any kind. The discovery mechanism in §6 is a fixed-layout broadcast
datagram precisely so that either side of the UNO Q can speak it in ~20 lines, without needing a
CBOR decoder or an mDNS responder on an MCU.

### 2.7 The TV is a browser **[corrected]**

The draft's architecture omits the TV entirely. [`laptop/public/tv.html`](laptop/public/tv.html) is
1,834 lines of Canvas2D stadium running in a browser, and **a browser cannot open a UDP socket.**

This is not a gap to be closed. The TV's requirements are the *opposite* of a sensor device's: it is
a display sink that wants reliable, ordered, complete state at ~10 Hz — which is precisely what TCP
provides for free. The escapes (WebTransport needs HTTP/3 plus a certificate a browser will accept;
WebRTC DataChannel needs signalling, ICE and SCTP) buy nothing and cost days.

**Decision: the TV speaks WebSocket permanently.** The server carries two north-bound adapters over
one session core. See §3.

---

## 3. System overview

| Component | Runs on | Transport | Responsibility |
|---|---|---|---|
| Phone (P1) | Galaxy S25 Ultra | UDP (+ WS fallback) | Gesture estimation, pose detection, ASR, coach. All on-device. |
| Arduino UNO Q (P2) | — | UDP | Action/pose detection. Not yet built. |
| TV / browser striker | Browser | **WebSocket** | Display sink. Cannot speak UDP. |
| Game server | Laptop | — | Registry, transport, snapshot assembly, routing. |
| Game engine | Laptop, separate process | loopback TCP | Match phases, keeper, referee, commentary. |

---

## 4. Layered architecture **[corrected — adapters added]**

```
   Phone          Arduino UNO Q        TV / phone.html
  (P1, UDP)         (P2, UDP)            (browser, WS)
      |                 |                      |
      +------ UDP ------+                      | WebSocket
                |                              |
  +-------------v------------------------------v-------------+
  |  GAME SERVER (laptop, Python/asyncio)                     |
  |                                                           |
  |   udp_adapter        ws_adapter        http_adapter       |
  |   header parse       JSON + binary     /v1/replay         |
  |   seq, dedup         frames            /fx/*              |
  |         \                |                 /              |
  |          +---------------+----------------+               |
  |                          |                                |
  |  SESSION CORE (adapter-agnostic)                           |
  |    Control plane            |  Data plane                  |
  |      registry               |    latest-wins slots         |
  |      capability negotiation |    + bounded history rings   |
  |      heartbeat / lifecycle  |    event ring + dedup        |
  |      time sync              |                              |
  |                                                            |
  |  Snapshot builder                                          |
  +--------------------------|---------------------------------+
                             | loopback TCP (NDJSON)
                       Game engine
                    (separate process)
```

`Broadcast(target="role:display")` routes to `ws_adapter`. `Broadcast(target="slot:1")` routes to
whichever adapter that session is currently bound to. **The engine never learns which transport a
device uses.** That is the concrete payoff for the boundary in §9, and the reason the adapter split
belongs in the server rather than the engine.

---

## 5. Channel model **[corrected — four channels, not three]**

The single most important split in the design. Reliability semantics are chosen per channel, not
globally.

| Channel | Examples | Transport | Reliability | Server-side storage |
|---|---|---|---|---|
| **Control** | HELLO, WELCOME, heartbeat, config change, time sync | UDP / WS | ACK + retry with backoff, sequenced | Session record |
| **Continuous stream** | `aim` zone, orientation | UDP / WS | Fire-and-forget, latest-wins **by `seq`, not by arrival** | Latest slot **+ bounded 64-entry history ring** |
| **Discrete event** | `kick`, `start`, `abort`, button press | UDP / WS | Sender retransmits until ACK; server dedups by `event_id` | Bounded ring buffer |
| **Bulk burst** | `skel` replay frames | **HTTP POST** | TCP's, inherited | Written straight to the replay store |

### 5.1 Why the split

Dropped packets being "the device's problem" is correct for continuous streams and wrong for the
others:

- A dropped `HELLO` means the device never joins and has no way to learn why. Registration needs
  server-side acknowledgement.
- A discrete event cannot be interpolated. Losing a kick is a gameplay bug, not a visual hiccup —
  and today it is a live one (§13).
- A dropped aim sample costs nothing if the game reads *state* rather than consuming a *queue* — the
  next sample supersedes it anyway.

### 5.2 Latest-wins alone breaks the keeper **[corrected]**

The draft mandated "a single slot per device, overwritten in place." Applied literally, that
destroys the AI keeper.

`keeper_pick()` ([`laptop/server.py:264-282`](laptop/server.py#L264-L282)) reads the striker's aim
**as it was `GF_KEEPER_REACTION` (0.45 s) before the kick**:

```python
cutoff = kick_t - KEEPER_REACT
for t, z in reversed(self.aim_trail):
    if t <= cutoff:
        seen = z
        break
```

That reaction lag is why a late feint beats the keeper — the game's most interesting mechanic. A
latest-wins slot has no history to look back through and would silently reduce the keeper to a coin
flip.

**Fix:** the registry keeps a bounded **64-entry timestamped history ring per stream** alongside the
latest slot. This is still O(1) memory per device — the actual scaling requirement — and it is still
transport-level data (timestamped samples, no interpretation). *What 450 ms means* stays in the
engine, where §9's invariant says rules belong.

### 5.3 Bulk burst is a fourth channel **[corrected]**

`skel` is ~10 KB, latency-tolerant (already sent 450 ms after the kick), loss-tolerant (a cosmetic
TV orbit replay), and out-of-band. It has none of the three realtime profiles above and is 8× over
the datagram cap in §8.

**Decision: `POST /v1/replay`, permanently.** This is correct layering, not a workaround, and there
is precedent — [`tv.html:268-299`](laptop/public/tv.html#L268-L299) already POSTs the same skeleton
data to `/fx/hero`. Fuse them: one endpoint that stores the replay and triggers the hero plate.

Send it as **float32 binary rather than JSON** — ~10 KB instead of ~90 KB. That win is available on
the current WebSocket path today, independent of everything else here.

App-level fragmentation over a `CH_BULK` datagram channel (flags bit 0 = `MORE_FRAGMENTS`, a 4-byte
sub-header `{blob_id:u16, frag_idx:u8, frag_count:u8}`, bounded reassembly with a 2 s timeout, drop
on gap) is **designed but deferred**. Build it only if the replay ever needs to arrive without a TCP
stack present.

---

## 6. Registration, capabilities, and discovery

### 6.1 Capability descriptor

Structured and versioned. Not a bag of strings.

```
device_id      stable UUID, persisted on device, survives reconnect
roles          [pose_source, gesture_source, display, haptic_sink]
streams        [{name, schema, rate_hz, dtype, payload_bytes}]
compute        {has_npu, local_inference_ladder, tops_est}
net            {mtu, measured_rtt, packet_loss_est}
proto_version  for negotiation
```

`compute.local_inference_ladder` replaces the draft's `can_accept_offload`. The phone already
carries an NPU→GPU→CPU fallback ladder
([`PoseAnalyzer.kt:111-134`](android/app/src/main/java/com/sentinelmesh/gesturefootball/pose/PoseAnalyzer.kt#L111-L134));
the descriptor's job is to make that *visible* to the server, not to solicit work for it (§12).

### 6.2 Handshake

```
device -> HELLO   {device_id, roles, streams, compute, net, proto}
server -> WELCOME {session_id, tick_rate, heartbeat_interval,
                   negotiated_streams, data_port, t_rx, t_tx}
```

The server replies with the **intersection** of what the device offers and what the game currently
needs. A device advertising 5 Hz aim can be told to send 2 Hz. This is the cheapest load-shedding
lever in the system, and it exists only because there is a capability registry — see §12.

`HELLO` must be **idempotent on the server**. If the reply was lost and the device retries, return
the existing session for that `device_id` rather than minting a new one. §7 depends on this being
free to spam.

Note that device identity does not exist today: roles are self-declared and unverified
([`laptop/server.py:437-441`](laptop/server.py#L437-L441) — any socket may claim `"client":"phone"`).
Sessions plus `device_id` fix that incidentally.

### 6.3 Session lifecycle

```
REGISTERING -> ACTIVE -> DEGRADED -> LOST -> REAPED
                  ^          |
                  +----------+
```

`DEGRADED` is load-bearing. A 2-second WiFi hiccup must not eject a player mid-match. Hold the
session and the player slot through a grace window; a device resuming with the same `device_id`
re-enters `ACTIVE` with its queued events intact.

### 6.4 Discovery: UDP broadcast beacon, not mDNS **[corrected — the draft offered both]**

Pick one, and pick broadcast. The reasons are specific to this stack:

1. mDNS on Android requires `CHANGE_WIFI_MULTICAST_STATE` plus a held `WifiManager.MulticastLock`,
   and Samsung drops multicast aggressively under power-save — on a device already thermally
   stressed by a continuous NPU pipeline.
2. `NsdManager`'s resolve step is racy below API 34. `minSdk` here is **28**.
3. The default host baked into the docs is `172.20.10.2` — an iPhone-hotspot subnet. Hotspots and
   guest WiFi frequently disable multicast while leaving broadcast working.
4. The UNO Q can emit a fixed-layout 24-byte datagram from either its Linux or its MCU side in ~20
   lines. It cannot comfortably run mDNS from the MCU side — which is exactly how §2.6 stops being a
   blocking question.

**Design.** The server binds UDP `:8079` and answers a 20-byte header with `msg_type = DISCOVER` by
replying **unicast** with `ANNOUNCE {name, ws_url, udp_port, proto}`. The device broadcasts to the
directed subnet address every 1 s for 5 s at startup.

**Android manifest additions:** `ACCESS_WIFI_STATE` and `ACCESS_NETWORK_STATE` — needed to derive
the directed subnet broadcast address from `LinkProperties`/`DhcpInfo`, because some APs drop
`255.255.255.255` while passing the directed form. Notably **not**
`CHANGE_WIFI_MULTICAST_STATE`; avoiding that permission is part of the argument.

**The manual HOST field stays permanently.** Discovery *prefills* it; it never replaces it. That
field and its `normalizeUrl`/`isValidWsUrl` guards are the demo-day escape hatch — and the fact that
someone already had to write a regex catching `ws://192.168.1.65:8080172.20.10.2:8080/ws` records
how much pain it has absorbed.

---

## 7. Time synchronization

**Build it, but be honest about when it matters.** With a single phone, the entire match runs on the
server's `time.monotonic()` and clock skew is structurally invisible — there is no second timebase to
fuse against. Cristian's algorithm earns its keep the moment the UNO Q becomes a second pose source
and you need to know whether P2's action preceded P1's. **The UNO Q has zero lines of code today.**
If P2 slips, this section is dead code, and it should be called dead rather than counted as progress.

### 7.1 Cristian's algorithm

```
T1  device clock, moment HELLO / sync request is sent
T2  server clock, moment it arrives
T3  server clock, moment the reply is sent
T4  device clock, moment the reply arrives

rtt    = (T4 - T1) - (T3 - T2)
offset = ((T2 - T1) + (T3 - T4)) / 2
```

T2 and T3 travel back inside the reply body. The device already holds T1 (it wrote it into its own
header) and stamps T4 the instant `recvfrom` returns, so **all four values are available from the
registration handshake for free** — no separate sync exchange at join time. Because `HELLO` is
idempotent (§6.2), sending eight of them costs nothing: take 8, keep the 3 lowest-RTT, median the
offset.

### 7.2 Rules

- **Assumes symmetric network delay.** Error is bounded by `± rtt/2`; real error equals half the path
  asymmetry.
- **Sample many, keep the fast ones.** A high-RTT sample sat in a queue, and queuing is rarely
  symmetric.
- **Never step the clock.** Timestamps going backwards break ordering. Keep `device_timestamp_us`
  untouched in the packet and apply the offset at read time on the server.
- **Re-sample every few seconds** via the heartbeat, carrying the same four stamps. Cheap crystals
  drift, and phones drift differently as they heat up.
- **Pipeline latency is separate.** Sensor-to-packet delay is a per-device constant you must measure
  and subtract independently. Cristian only handles the clock.

### 7.3 Jitter buffer **[cut]**

The draft recommended 1–2 frames. At the real `aim` rate of 5 Hz that is **200–400 ms of
deliberately added latency**, injected into the exact mechanic the game is built around (§5.2). It
would make the game measurably worse.

The recommendation assumes a 30–60 Hz stream that does not exist here (§2.2). Ordering by `seq` in
the slot is the entirety of what is needed. Revisit only if continuous pose streaming ever ships.

---

## 8. Wire format

Unchanged from the draft — it was correct — and reproduced in full because §8.5's hex dump doubles as
a conformance vector.

### 8.1 Fixed header — 20 bytes, every packet

```
offset  size  field
  0      1    version          <- must stay at offset 0 forever
  1      1    msg_type
  2      1    flags            <- spare bits are the escape hatch
  3      1    channel
  4      4    session_id       big-endian
  8      4    seq              big-endian
 12      8    device_timestamp_us  big-endian
```

There is **no delimiter** between header and body. Both sides agree the header is 20 bytes
out-of-band. Off by one and the body parser reads garbage.

Byte 0 is the version and is first deliberately: it is the one byte whose meaning can never change,
and it is the only migration path if the header layout ever needs to grow.

**Cap total datagram at ~1200 bytes.** One lost IP fragment kills the whole datagram.

Python: one module-level `struct.Struct(">BBBBIIQ")`. Kotlin:
`ByteBuffer.allocate(20).order(ByteOrder.BIG_ENDIAN)` — no dependency required, ~40 lines.

### 8.2 Control plane — CBOR (RFC 8949)

Self-describing binary. A decoder handles messages it has never seen, skipping unknown keys via their
length prefixes. Adding a field does not break older firmware.

Use **string keys**, not integer keys. Integer keys save ~35% but cost you the ability to read a
capture. Control messages fire once per device per session; the size is irrelevant and the
debuggability is not.

**Libraries.** Python: `cbor2>=5.6` — actively maintained, MIT, pure-Python fallback with an optional
C accelerator, ships Windows wheels. Not `cbor`, which was abandoned in 2016. This becomes the second
runtime dependency after `aiohttp`. Android: `com.upokecenter:cbor` — pure Java, ~300 KB, no
reflection, no Jackson transitive pull-in, and `CBORObject.NewMap().Add("sid", 41337)` maps 1:1 onto
the string-keyed maps below. Embedded (UNO Q, if the MCU side is ever used): TinyCBOR, QCBOR,
cn-cbor — encode into a stack buffer; the encoder returns `CborErrorOutOfMemory` rather than
overflowing.

Resist hand-rolling. The *encoder* is easy — six major types. The *decoder* has to skip unknown keys
by length prefix, which is the entire reason for choosing CBOR, and that is where the bugs live.

### 8.3 Data plane — fixed-offset structs

Same shape many times a second per device. Reading it should be a cast, not a parse.

Today that means the aim struct, which is 5 bytes:

```c
typedef struct __attribute__((packed)) {
    uint8_t zone;              // 0=L 1=C 2=R
    float   confidence;
} aim_body_t;                  // 5 bytes, always
```

The draft's 208-byte 17-joint pose body is defined but **carries no live traffic** — the phone does
not stream pose (§2.2). Keep the definition so the UNO Q has something to target; do not present it
as current.

```c
typedef struct __attribute__((packed)) {
    uint16_t joint_count;
    uint16_t flags;
    float    kp[17][3];        // x, y, confidence
} pose_body_t;                 // 208 bytes, always  -- reserved, not in use
```

**The encoding rule:** fixed and fast where the shape never varies (header, data bodies);
self-describing and flexible where it does (control messages). Three rates of change, three
encodings.

### 8.4 Receive path

```
1. length >= 20?              else drop
2. byte 0 == known version?   else drop
3. byte 1 -> dispatch on msg_type
4. session_id valid + token check
5. hand bytes 20..n to the body decoder
```

Drop silently on any failure. The sender's retry timer handles it.

### 8.5 Worked example — WELCOME on the wire

69 bytes. **Use this as a golden test vector**: `pack_welcome(...)` must reproduce it byte-for-byte
on both the Python and Kotlin sides. It costs nothing and catches every endianness and offset error
instantly.

```
0000  01 02 00 00 00 00 A1 79 00 00 00 01 00 06 3B B4
0010  ED 68 61 15 A6 63 73 69 64 19 A1 79 64 74 69 63
0020  6B 18 3C 62 68 62 19 07 D0 62 68 7A 18 1E 64 70
0030  6F 72 74 19 BA C3 65 74 5F 73 72 76 1B 00 06 3B
0040  B4 ED 68 61 15
```

Header (bytes 0–19):

```
01                        version = 1
02                        msg_type = WELCOME
00                        flags
00                        channel = CONTROL
00 00 A1 79               session_id = 41337
00 00 00 01               seq = 1
00 06 3B B4 ED 68 61 15   ts_us
```

CBOR body (bytes 20–68):

```
A6                        map, 6 pairs
  63 73 69 64             "sid"
  19 A1 79                41337
  64 74 69 63 6B          "tick"
  18 3C                   60
  62 68 62                "hb"
  19 07 D0                2000
  62 68 7A                "hz"
  18 1E                   30
  64 70 6F 72 74          "port"
  19 BA C3                47811
  65 74 5F 73 72 76       "t_srv"
  1B 0006 3BB4 ED68 6115  1754498123456789
```

The device had asked for `"hz": 60` (`18 3C`); the server returned `"hz": 30` (`18 1E`). Capability
downgrade, one byte on the wire.

---

## 9. Server / engine boundary

Enforced by a **process boundary**, not discipline. The server runs as its own process; the engine
links a thin client SDK over loopback TCP (§2.1).

### 9.1 Contract

```python
@dataclass(frozen=True, slots=True)
class DeviceState:            # continuous channel
    device_id: str
    slot: int                 # player slot; the server owns the MAPPING
    stream: str               # "aim"
    seq: int
    device_ts_us: int         # device clock, NEVER rewritten
    server_ts_us: int         # server-corrected timebase
    data: dict

@dataclass(frozen=True, slots=True)
class Event:                  # discrete channel
    event_id: str             # f"{device_id}:{evt_seq}"  <- the dedup key
    device_id: str
    slot: int
    kind: str                 # "kick" | "start" | "again" | "abort"
    device_ts_us: int
    server_ts_us: int
    data: dict

@dataclass(frozen=True, slots=True)
class InputFrame:
    tick_id: int
    server_time_us: int
    devices: list[DeviceState]
    events: list[Event]
    joined: list[str]         # device_ids that became ACTIVE this frame
    left: list[str]           # device_ids that went DEGRADED / LOST

@dataclass(frozen=True, slots=True)
class Broadcast:
    payload: dict
    target: str               # "all" | "role:display" | "slot:1" | "<device_id>"
    channel: str              # "state" | "control" | "haptic"
```

The engine never sees a socket.

### 9.2 The tick model is relaxed **[corrected]**

The draft said the engine calls `poll(tick)` and consumes tick-aligned frames. For *this* engine that
is a downgrade:

- Phases are **seconds** long — `GF_ANNOUNCE_S=3.5`, `GF_COUNTDOWN_S=3.0`, `GF_RESOLVE_S=3.8`. There
  is no physics integration and nothing that wants a 16.6 ms budget.
- `Game.run_match()` ([`laptop/server.py:320-425`](laptop/server.py#L320-L425)) is a working
  straight-line coroutine already guarded at 11 points by `_alive(gen)` against a mid-flight abort.
  Slicing it into a tick-driven state machine makes that generation-guard problem strictly worse and
  buys nothing observable.

**Design:** `tick_id` is a monotonic sequence stamped by a 30 Hz server-side sampler. The engine
consumes `InputFrame`s from an `asyncio.Queue` whenever it awaits. The snapshot builder is still
tick-aligned and still real — the engine simply is not written as `for tick in ticks:`.

Three call-site rewrites, not a rewrite:

| Today | After |
|---|---|
| `await self.kick_evt.wait()` | `await link.next_event(kind="kick", slot=1)` |
| `self.aim` | `link.state(slot=1, stream="aim").data["zone"]` |
| `self.aim_trail` in `keeper_pick` | `link.history(slot=1, stream="aim", since_us=...)` |

### 9.3 Two Link implementations, one interface

This is the mitigation for the largest delivery risk in the plan — that "decouple the engine" becomes
"the demo now needs two processes and one of them didn't start."

- **`InProcLink`** — direct in-memory queues; the engine runs as a task inside the server's loop.
  **This is the default and what runs on demo day.** One process to start, one thing to crash.
- **`TcpLink`** — the real process boundary, NDJSON over `127.0.0.1:8899`. Used in development and
  CI, swappable to length-prefixed CBOR later behind the same interface.

Run the whole test suite both ways.

### 9.4 The invariant

The server knows about **devices, sessions, streams, and routing**. It knows nothing about players,
scores, or rules. The moment a game rule appears in the server, the boundary is gone.

Grey area: device-to-player-slot mapping. Keep the *mapping* in the server (it is session state);
keep *what a slot means* in the engine. Likewise §5.2's history ring: the server stores timestamped
samples, the engine decides that 450 ms of them is what a keeper can react to.

---

## 10. Suggested module layout

```
laptop/
  server/                    # knows nothing about football
    main.py                  # process entry; starts adapters + core
    core.py                  # fan-out, snapshot assembly, dirty-flag coalescing
    registry.py              # sessions, latest-wins slots, history rings, event dedup
    contract.py              # InputFrame / DeviceState / Event / Broadcast   <- shared
    wire.py                  # 20-byte header, CBOR codec, fixed-offset structs
    ws_adapter.py            # today's /ws + static files
    udp_adapter.py           # phone, UNO Q
    http_adapter.py          # /v1/replay, /fx/*
    engine_link.py           # server side of the boundary
  engine/                    # knows nothing about sockets
    main.py
    match.py                 # run_match + full_time         (from server.py :307-433)
    keeper.py                # predict / keeper_iq / keeper_pick / referee  (:241-305)
    desk.py                  # Desk                          (:58-121)
    templates.py             # ZW / T / tline                (:124-168)
    config.py                # GF_* env knobs                (:41-54)
    link.py                  # engine side; the thin client SDK
  neural_fx.py               # unchanged
  server.py                  # ~20-line launcher shim; `python server.py` keeps working
```

---

## 11. Testing

| Artifact | Purpose |
|---|---|
| `laptop/test_match.py` | **The regression gate. Do not modify it** during the split. It must pass byte-for-byte against both `InProcLink` and `TcpLink`. Its undocumented `GF_SHOOT_WINDOW > 0` requirement becomes a pytest fixture rather than a manual env export. |
| `tests/test_engine.py` | Drives `engine/match.py` through `InProcLink` with a scripted `InputFrame` stream. Inject a `Clock` so the pacing constants fast-forward: a real 5-kick match costs ~52 s of wall clock, which is exactly why nobody runs one. With a fake clock it is <100 ms, and you can seed-sweep the referee across 1,000 matches to check the score/saves invariants. |
| `tests/test_reliability.py` | A `FlakyLink` dropping N% of client→server frames. Assert every kick lands exactly once at 0/10/30% loss. |
| `tests/test_wire.py` | Golden vector: reproduce §8.5's 69 bytes exactly. Fuzz the receive path — 10,000 random byte strings must only ever drop, never raise. |
| `tests/test_udp_loss.py` | A `DatagramProtocol` proxy with configurable drop / duplicate / reorder / latency. Full match at 0/5/20/50% loss. **Out-of-order case:** deliver seq 5, 3, 4 — assert the slot holds seq 5 and that seq 3 never overwrites it. |
| `tests/test_session_resume.py` | Kill the transport mid-`shoot`, reconnect with the same `device_id`; assert same `session_id`, no phase reset, queued kick lands. |
| `tests/test_hello_idempotent.py` | 8 HELLOs, one session (§6.2). |
| `android/app/src/test/` | **Does not exist today — there is no test source set at all.** Creating it is a Phase 1 prerequisite, not a Phase 2 discovery. |

---

## 12. Load shedding **[corrected — this was the offload manager]**

§2.3 removes the premise: all inference is already on the phone, and the laptop runs no ML. There is
no utilization signal to sample and no local workload to shed. What survives is the capability lever.

| Rung | Draft's plan | Verdict |
|---|---|---|
| 1 | Drop stream rate via the capability channel | **Build.** `WELCOME` returns a negotiated `aim_hz`; the server drops the phone 5 Hz → 2 Hz when the broadcast queue backs up. Cheap, real, visible in a capture — and the thing that justifies having a registry at all. |
| 2 | Smaller local model / lower input resolution | **Already exists**, as the phone's NPU→GPU→CPU ladder. Surface it in the capability descriptor; build nothing. |
| 3 | Peer offload to a device advertising spare compute | **Cut — the premise is inverted.** The draft said "the phone has real silicon sitting idle." It does not: two-stage BlazePose on HTP, plus Whisper Tiny, plus optionally Qwen3 0.6B. The **laptop** is the idle one. It also collides with the privacy boundary in `README_GalaxyS25.md` §14, which forbids frames and transcripts leaving the device. |
| 4 | Remote offload for latency-tolerant work | **Cut — no endpoint exists (§2.4), and it is already solved.** The one latency-tolerant workload in the system, LLM commentary, already implements the draft's entire "remote client requirements" list: async and off the tick path, an 8 s hard deadline, silent fallback to templates, stale-guarded by a key counter ([`laptop/server.py:58-121`](laptop/server.py#L58-L121), [`:227-239`](laptop/server.py#L227-L239)). It needs a circuit breaker — trip to templates after 3 consecutive failures, retry after 60 s — not a rebuild. |

**The laptop's one genuine compute problem is not an offload problem.** `neural_fx.hero()` runs
pure-Python per-pixel splatting synchronously on the event loop, stalling match timing and every
socket for tens of milliseconds per request. **[corrected]** The resolution is the browser-side GPU
port (system-plan U§5.2 Change A — the splat inverts to a stateless gather plus one max-reduction
pass), which deletes this endpoint's hot path entirely; the `ProcessPoolExecutor` suggested here
earlier is the fallback if the shader port stalls, not the fix. Deferred with the server build —
noted at the `/fx/hero` handler in `laptop/server/app.py`. No manager, no ladder, no registry
involved either way.

---

## 13. Defects this design must resolve

Non-speculative, reproducible, and present in shipping code. These justify the work.

| Defect | Location | Section that addresses it |
|---|---|---|
| **Lost kick.** `ws?.send()`'s boolean return is discarded, and `ws` is null during the reconnect window. A kick taken then vanishes silently while the striker sees their force reading — and with `GF_SHOOT_WINDOW=0` (the default) the server then waits *forever*. | [`GameClient.kt:202-244`](android/app/src/main/java/com/sentinelmesh/gesturefootball/net/GameClient.kt#L202-L244) | §5 discrete-event channel: `event_id`, sender retransmit, server dedup. At-least-once delivery plus server-side dedup is effectively-once. |
| **Reconnect churn.** Fixed 1500 ms retry on a bare `Thread` — no backoff, no jitter, no cap. A wrong saved IP loops for the app's lifetime, and every reconnect looks broken for a 1.5 s minimum. | [`GameClient.kt:193-199`](android/app/src/main/java/com/sentinelmesh/gesturefootball/net/GameClient.kt#L193-L199) | §6.3 session lifecycle. Replace with `min(5000, 250 · 1.5^n)` plus ±20% jitter, under a `DEGRADED` grace window. |
| **Event-loop stall.** `neural_fx.hero()` runs synchronously on the asyncio loop, pausing match timing and every socket. | `/fx/hero` handler, now `laptop/server/app.py` | §12 **[corrected]** — resolved by the browser GPU port (U§5.2 Change A), not a `ProcessPoolExecutor`; deferred until that ships. |
| **Broadcast amplification.** The full snapshot — including `shotmap` and up to 40×33×3 replay floats — is re-serialized and fanned out on *every* mutation, including every 200 ms aim update. | [`laptop/server.py:218-224`](laptop/server.py#L218-L224) | §5 channel split. Interim fix needs no client change: dirty flag, 50 ms coalescing debounce, drop byte-identical consecutive payloads. A true delta needs one line at [`tv.html:334-335`](laptop/public/tv.html#L334-L335), where `prev=S; S=st` is a wholesale replace. |

---

## 14. Scope ledger

| Section | Status | Reason |
|---|---|---|
| §5 channel model | **In** — extended to 4 channels | The core value of this design. Three of the four defects in §13 are channel-semantics bugs. |
| §5.2 history ring | **In** — new | Latest-wins alone breaks the keeper. |
| §6 registry, capabilities, idempotent HELLO | **In** | Prerequisite for sessions, identity, and rung 1. |
| §6.3 `DEGRADED` grace window | **In** | Directly fixes reconnect churn. |
| §6.4 discovery | **In** — broadcast beacon | Highest user-visible value in the whole design. The draft gave it one line. |
| §7 time sync | **In, gated** | Correct and free, but inert until a second pose source exists. |
| §7.3 jitter buffer | **Cut** | 200–400 ms of added latency into the game's core mechanic, at a stream rate that does not exist. |
| §8 wire format | **In** | Unchanged from the draft; §8.5 doubles as a test vector. |
| §9 process boundary | **In** — tick model relaxed | Correct; the tick requirement was not. |
| §12 rung 1 | **In** | The cheapest lever, and the registry's justification. |
| §12 rung 2 | **In** — already built | Surface in the descriptor only. |
| §12 rung 3 (peer offload) | **Cut** | Premise inverted: the phone is saturated, the laptop is idle. Also collides with the privacy boundary. |
| §12 rung 4 (remote offload) | **Cut** | No endpoint exists; the one candidate workload already satisfies the requirements. |
| `io_uring` / `epoll` | **Cut** | 3 devices at ~10 pkt/s. Four orders of magnitude oversized. |
| Lock-free per-device slots | **Cut** | Meaningless under the GIL; a dict write is already atomic. |
| Sharding | **Cut** | Targets a device count that does not exist. |
| Bounded per-session memory | **In** | Free, and good hygiene regardless of scale. |
| DTLS / per-packet HMAC / key exchange / cert pinning | **Cut** | Days of work to protect a penalty shootout. See §15. |

---

## 15. Scaling and security

**Scaling.** Single socket, `asyncio.DatagramProtocol`, bounded memory per session. Rate-limit per
session so one misbehaving device cannot starve others. Design for 8 devices, not for arbitrary
count — see the ledger for what was cut and why.

**Security — proportionate to a LAN demo.** An open UDP port on a shared LAN with no auth means
anyone can inject pose data for any player. Ship exactly this:

1. `session_id` — the header's existing 4 bytes — is the bearer token, **randomly generated, never
   sequential**. 32 bits of blind-guess entropy for a ten-minute demo on a hotspot is proportionate.
2. Per-session rate limit (200 pkt/s).
3. Replay rejection: a sliding 64-bit window over `seq`. Accept `seq > last_seen - 64`; reject
   duplicates and ancient packets.
4. **Reject datagrams > 1400 bytes, and unknown version bytes, before parsing.** Two lines each, and
   they are the real attack surface.

---

## 16. Phase map

Design altitude. Phases, goals, and cut-lines — not task lists.

**The sequencing insight.** The draft conflated three independent changes: a process boundary, a
binary wire format, and UDP transport. They are usually shipped together because people assume they
are coupled. They are not — **WebSocket carries binary frames.** The entire §8 format can be built
and validated against the real Android client and the real Snapdragon pipeline *before* the socket is
swapped underneath. That decouples the riskiest change (UDP, which the TV can never speak) from the
highest-value one (framing and versioning), and no phase requires a flag-day.

| Phase | Goal | Independently shippable? | Rough size |
|---|---|---|---|
| **1. Process boundary + reliable events** | Split `server.py` per §10. Fix the lost kick and reconnect churn using §5's discrete-event semantics — **still on WebSocket**. Coalesce broadcasts. Move `hero()` to an executor. Create the Android test source set. | Yes. No protocol change; `test_match.py` passes unmodified. | 3–4 d |
| **2. Wire format, still over WebSocket** | §8 header, CBOR control plane, fixed-offset structs — as **binary WS frames**, alongside JSON, never replacing it. `skel` moves to float32 over HTTP. | Yes | 2–3 d |
| **3. UDP data plane for devices** | `udp_adapter` alongside `ws_adapter`. Android dual-stack behind a toggle, **defaulting to WS**, with mandatory auto-fallback if no `WELCOME` arrives within 2 s. The TV never moves. | Yes | 4–5 d |
| **4. Discovery + time sync** | §6.4 broadcast beacon. §7 Cristian — **gated on the UNO Q existing.** | Discovery yes; time sync only with P2 | 3–4 d |
| **5. Rung 1** | Negotiated `aim_hz` in `WELCOME`. Circuit breaker on the commentary desk. | Yes | 1 d |

**Total to full coverage minus the cuts: ~14–17 dev-days.**

**Cut-line.** If time is short: Phase 1 in full, plus `skel`-as-binary from Phase 2, plus discovery
from Phase 4. Stop there. That subset fixes the lost kick, kills the broadcast storm, removes the
manual-IP misery, and leaves a real test suite — without ever touching the transport.

**Delivery risks.**

1. *UDP regresses a working demo.* → WebSocket is never removed; UDP is opt-in behind a toggle;
   auto-fallback on a missing `WELCOME`. Hotel and enterprise APs block client-to-client UDP
   routinely, and the demo must not depend on it.
2. *Two processes means two things to fail on stage.* → `InProcLink` is the default (§9.3); `TcpLink`
   is for CI. Same code path, tested both ways.
3. *No Android test infrastructure exists at all.* → A Phase 1 prerequisite, not a Phase 2 surprise.
