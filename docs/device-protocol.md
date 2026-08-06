# Device Protocol — the contract for firmware

**Audience:** whoever writes the UNO Q firmware (Linux side or MCU side — this
protocol was designed so either can speak it), and any future device.
**Reference implementation:** `tools/unoq_sim.py` behaves exactly as the
firmware should. Run it against a live server and copy what it does.
**Authority:** [`server-architecture.md`](server-architecture.md) §5–§9 takes
precedence on any conflict.

Transport today is **WebSocket + JSON** (`ws://<server>:8080/ws`). The UDP data
plane arrives in a later phase; the 20-byte header below is already live on the
discovery beacon and will carry the data plane unchanged.

---

## The thirteen rules

1. **Generate a UUID once, persist it, never regenerate.** Your `device_id` is
   your identity across reconnects; a new one means a new player slot.
2. **`HELLO` is idempotent.** A duplicate response is not an error. If the
   `WELCOME` was lost, send `HELLO` again — you will get the *same*
   `session_id` back.
3. **Broadcast `DISCOVER` on UDP `:8079`; never hardcode an IP.** Take the
   server's address from the `ANNOUNCE` packet's **source IP**. A reflash per
   network change is not acceptable.
4. **Declare what you have, not what you think the game wants.** `WELCOME` may
   downgrade you (a 60 Hz stream told to send 30); comply.
5. **20-byte header on every datagram, version at byte 0.** (WS messages are
   JSON and carry no header.)
6. **Cap datagrams at 1200 bytes. Never fragment.**
7. **Timestamp with your own microsecond clock; do not correct it.** The server
   converts at read time (Cristian offset from the handshake).
8. **Discrete events** (`dive`, button): attach an `event_id`, retransmit until
   ACKed. **Continuous streams**: fire-and-forget, latest-wins, never
   retransmit a stale sample.
9. **Never queue.** A new sample overwrites the pending one.
10. **Reconnect with the same `device_id`**, backoff `min(5000 ms, 250·1.5ⁿ)`
    ± 20 % jitter.
11. **Report `telem` per unit at 1 Hz.** The MCU reports main-loop duty cycle —
    there are no OS counters there. `metric` is yours; the server stores it
    opaquely.
12. **Do not assume you are the only device, the first device, or player 2.**
13. **Do not put game rules in firmware.** Emit motion; the server side decides
    it was a dive.

---

## 1. Discovery (UDP :8079)

Device → broadcast, header only (20 bytes):

```
byte 0   version   = 1
byte 1   msg_type  = 3 (DISCOVER)
byte 2   flags     = 0
byte 3   channel   = 0
4..7     session_id = 0        (big-endian; none yet)
8..11    seq        = 0
12..19   device_timestamp_us   (your clock, echoed back)
```

Server → unicast reply: same 20-byte header with `msg_type = 4 (ANNOUNCE)`,
your timestamp echoed in `ts_us`, followed by a JSON body:

```json
{"name": "gesture-football", "proto": 1, "ws_port": 8080, "ws_path": "/ws"}
```

Connect to `ws://<announce source ip>:<ws_port><ws_path>`. Broadcast once per
second for ~5 s at boot; also probe the directed subnet broadcast if plain
`255.255.255.255` gets no answer.

## 2. Registration (WebSocket)

Device → server:

```json
{
  "type": "hello",
  "device_id": "unoq-3fa8c2d19b04",
  "client": "unoq",
  "device": "unoq",
  "roles": ["keeper_input"],
  "streams": [
    {"name": "dive",  "schema": "event", "rate_hz": 0},
    {"name": "telem", "schema": "telem", "rate_hz": 1}
  ],
  "compute": {"has_npu": false, "units": ["cpu", "gpu", "mcu"], "tops_est": 0.1},
  "net": {"mtu": 1500},
  "proto": 1
}
```

Server → device (only clients that sent a `device_id` get this):

```json
{
  "type": "welcome",
  "session_id": 501476794,
  "proto": 1,
  "heartbeat_ms": 2000,
  "negotiated": {"dive": {"rate_hz": 0}, "telem": {"rate_hz": 1}},
  "t_rx_us": 123, "t_tx_us": 456
}
```

`t_rx_us`/`t_tx_us` are the Cristian T2/T3 stamps: with your own T1 (send) and
T4 (receive) you have clock offset for free. Send several HELLOs and keep the
lowest-RTT sample — rule 2 makes that safe.

Heartbeat at the given interval: `{"type": "hb", "ts_us": ...}`.
Miss ~3 and your session goes `DEGRADED`; it survives a grace window
(~15 s) and resumes on reconnect with the same `device_id`.

## 3. Discrete events

```json
{"type": "event", "kind": "dive", "event_id": "unoq-3fa8c2d19b04:17",
 "data": {"zone": "L", "g_force": 2.9}, "ts_us": 8123456}
```

- `event_id` = `<device_id>:<monotonic counter>`. Retransmit every ~250 ms
  until you receive `{"type": "ack", "event_id": ...}`; give up after ~8 tries.
- Duplicates are ACKed too (your first ACK may have been lost) but reach the
  game exactly once.
- `dive.zone` ∈ `L | C | R`. A dive during the countdown/shoot window makes the
  device the keeper for that kick.

## 4. Telemetry

One message per unit per second:

```json
{"type": "telem", "unit": "mcu", "busy_pct": 22,
 "metric": {"imu_hz": 208}, "temp_c": 41, "state": "dive watch",
 "window_ms": 1000}
```

`unit` for the UNO Q: `cpu`, `gpu`, `mcu`. The dashboard at
`http://<server>:8080/telemetry` shows your three cells; they grey out after
3 s of silence.

## 5. What you will receive

- `state` — full game snapshot on every change (heavy keys are filtered out
  for your role). Read what you need; **ignore unknown fields and types.**
- `haptic` — `{"type": "haptic", "pattern": "goal|save|post|over", "kick": n,
  "zone": "L|C|R"}` after each kick you kept. Drive the motor and LEDs.
- `ack` — for your events.

## 6. Wire budget

Everything a keeper device sends fits in tens of bytes per second plus a
1 Hz telemetry trickle. If you find yourself sending more, re-read rule 9.
