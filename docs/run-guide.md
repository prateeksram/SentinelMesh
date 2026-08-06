# Run Guide — server, game, telemetry, simulator

How to run Gesture Football after the server split. Everything here works on
the **x86-64 dev machine** and unchanged on the **Snapdragon X Elite target** —
the server and engine are pure Python (stdlib + `aiohttp`), no
architecture-specific dependencies.

```
laptop/
  server/     device server — sessions, transport, telemetry, discovery
  engine/     match logic — phases, THE WALL, referee, commentary
  server.py   launcher shim (runs both in one process, like the old monolith)
tools/
  unoq_sim.py UNO Q keeper simulator (Player 2 stand-in)
docs/
  device-protocol.md   the contract for future firmware
```

---

## 0. One-time setup

```powershell
python -m pip install -r requirements.txt      # aiohttp only
```

---

## 1. Run the game (demo mode — one process)

```powershell
cd laptop
python server.py          # or: python -m server
```

You should see:

```
Link  :  inproc — engine embedded in this process
HTTP  :  http://0.0.0.0:8080   (tv.html · phone.html · telemetry.html)
Disco :  udp://0.0.0.0:8079  (DISCOVER -> ANNOUNCE)
Neural FX :  PROCEDURAL · procedural depth-from-skeleton
```

Then:

1. **TV** — open `http://localhost:8080/tv.html` (or just `http://localhost:8080/`).
2. **Phone** — Gesture Football app → HOST field → `<laptop-LAN-ip>:8080` → tap
   HOST. Same Wi-Fi/hotspot; never `localhost` on the phone. Browser fallback:
   `http://localhost:8080/phone.html` over USB, or HTTPS `:8443` with certs.
3. **Telemetry** — open `http://localhost:8080/telemetry` on a second monitor.
   Nine cells; unregistered units stay dark by design.
4. On the TV: **START MATCH**.

Everything the old `python server.py` did still works — same URLs, same phone
protocol, same env knobs (`GF_KICKS`, `GF_KEEPER_IQ`, `GF_SHOOT_WINDOW`,
`GF_ANNOUNCE_S`, `GF_COUNTDOWN_S`, `GF_RESOLVE_S`, `ANTHROPIC_API_KEY` /
`GF_LLM_URL` for the commentary desk).

## 2. Run with the process boundary (two processes)

Terminal 1 — device server (owns sockets, sessions, telemetry):

```powershell
cd laptop
python -m server --link tcp
```

Terminal 2 — match engine (owns the game; connects over loopback :8899):

```powershell
cd laptop
python -m engine
```

**Pacing env vars belong to the engine process** — set `GF_*` where you start
`python -m engine`. Port override: `GF_ENGINE_PORT` (both sides).

You can kill and restart the engine at any time: devices stay connected, their
sessions survive, and the fresh engine re-receives the roster and returns to
the lobby. That behavior is the point of the boundary.

## 3. Player 2 — the UNO Q simulator

With a server running (either mode), from the repo root:

```powershell
python tools/unoq_sim.py                # discovers the server via UDP :8079
python tools/unoq_sim.py --dive auto    # dives L/C/R on a timer
python tools/unoq_sim.py --host 192.168.1.50:8080   # skip discovery
```

- Type `l` / `c` / `r` + Enter to dive that way.
- A dive during the countdown/shoot window makes the simulator **the keeper
  for that kick** — THE WALL steps aside, and the shot resolves against the
  simulated dive (shotmap entry gets `keeperSrc: "device"`).
- After each kick it keeps, the sim prints the received `haptic` broadcast.
- Its three telemetry cells (UNO Q cpu/gpu/mcu) light on `/telemetry`.
- Identity persists in `tools/.unoq_device_id` — kill it, restart it, and it
  resumes the same session.

## 4. Tests

Fast pacing first (the shoot-window default of 0 waits forever; tests need a
timeout to exercise the skied-kick path):

```powershell
cd laptop
$env:GF_ANNOUNCE_S="0.1"; $env:GF_COUNTDOWN_S="0.2"
$env:GF_SHOOT_WINDOW="1.0"; $env:GF_RESOLVE_S="0.1"
python server.py            # leave running; use one terminal per process
```

Then, in another terminal:

```powershell
cd laptop
python test_match.py        # legacy regression gate — unmodified, must pass
python test_devices.py      # acceptance: discovery, idempotent HELLO, dedup,
                            # dive→keeper, haptic rx, session resume,
                            # phone snapshot filtering, telemetry aggregation
```

Both suites also pass in `--link tcp` mode (remember: `GF_*` on the engine).

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| `python server.py` → `ModuleNotFoundError: aiohttp` | `python -m pip install -r requirements.txt` |
| TV PHONE LED red | HOST = laptop LAN IP + same Wi-Fi; tap HOST |
| Sim: "discovery failed" | Server running? UDP :8079 blocked by firewall? Use `--host ip:8080` |
| Sim connects, no WELCOME line | You're on an old server build — WELCOME only comes from the split server |
| `/telemetry` cells all dark except laptop CPU | Correct: only workloads that exist report. Phone cells light when the app grows a telem sender; UNO Q cells need the sim or the board |
| Engine (tcp mode) starts but nothing happens | Server not in `--link tcp` mode, or `GF_ENGINE_PORT` mismatch |
| Match hangs on a skipped kick in tests | `GF_SHOOT_WINDOW` unset (defaults to 0 = wait forever) — set it on the **engine** process |
| Two servers fighting over :8080 / :8079 | Kill stray `python` processes from earlier runs |

## 6. Dev vs target machine

Developed and tested on x86-64 Windows (Intel + NVIDIA — none of the Qualcomm
stack is testable here). Deploys unchanged to the Snapdragon X Elite laptop.
The Qualcomm-only work (GenieX commentary, QNN, WebGPU-on-Adreno profiling) is
tagged **[device]** in `docs/system-plan.md` §9 and starts only on the target.
On the target, remember: ARM64 Python for anything that will touch the NPU
later; ARM64 Chrome for the TV.
