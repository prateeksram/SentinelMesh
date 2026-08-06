#!/usr/bin/env python3
"""UNO Q device simulator — behaves exactly as the firmware will (handoff §7).

Run against a live server:

    python tools/unoq_sim.py                 # discover the server, join, idle
    python tools/unoq_sim.py --dive auto     # dive L/C/R on a timer
    python tools/unoq_sim.py --dive R        # one dive zone, on a timer
    python tools/unoq_sim.py --host 10.0.0.5:8080   # skip discovery

Interactive: type  l / c / r  + Enter to dive that way. Every received
message is printed — the rx path must be visibly exercised, not just tx.

What it exercises, in firmware order:
  DISCOVER broadcast -> ANNOUNCE (20-byte header, server addr from source IP)
  HELLO with a full UNO Q capability descriptor -> WELCOME (idempotent)
  heartbeats at the negotiated interval
  `dive` events with event_id, retransmitted until ACKed
  telem for three units (cpu / gpu / mcu) at 1 Hz
  reconnect with the SAME device_id, backoff min(5000, 250*1.5^n) ± 20% jitter

Requires: aiohttp (same as the server). Runs on the dev machine or the target.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import socket
import struct
import sys
import threading
import time
import uuid

import aiohttp

# --- 20-byte header, matching laptop/server/protocol/header.py -----------
HEADER = struct.Struct(">BBBBIIQ")
VERSION = 1
MSG_DISCOVER, MSG_ANNOUNCE = 3, 4
DISCOVERY_PORT = 8079

ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".unoq_device_id")


def device_id() -> str:
    """Generate once, persist, never regenerate (device-protocol rule 1)."""
    try:
        with open(ID_FILE) as f:
            did = f.read().strip()
            if did:
                return did
    except OSError:
        pass
    did = f"unoq-{uuid.uuid4().hex[:12]}"
    try:
        with open(ID_FILE, "w") as f:
            f.write(did)
    except OSError:
        pass
    return did


def now_us() -> int:
    return time.monotonic_ns() // 1000


def log(tag: str, msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {tag:<8} {msg}", flush=True)


# ------------------------------------------------------------- discovery ---
def discover(timeout_per_try: float = 1.0, tries: int = 5) -> str | None:
    """Broadcast DISCOVER, return 'ip:port' from the first ANNOUNCE.
    The server address comes from the packet's SOURCE IP — rule 3: never
    hardcode an IP. (127.0.0.1 is probed too so dev-machine loopback works.)"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout_per_try)
    pkt = HEADER.pack(VERSION, MSG_DISCOVER, 0, 0, 0, 0, now_us())
    targets = [("255.255.255.255", DISCOVERY_PORT), ("127.0.0.1", DISCOVERY_PORT)]
    try:
        for i in range(tries):
            for t in targets:
                try:
                    sock.sendto(pkt, t)
                except OSError:
                    pass
            try:
                data, addr = sock.recvfrom(1400)
            except socket.timeout:
                log("disco", f"no ANNOUNCE yet (try {i + 1}/{tries})")
                continue
            if len(data) < HEADER.size:
                continue
            ver, mtype, *_ = HEADER.unpack_from(data)
            if ver != VERSION or mtype != MSG_ANNOUNCE:
                continue
            try:
                body = json.loads(data[HEADER.size:])
            except json.JSONDecodeError:
                continue
            host = f"{addr[0]}:{body.get('ws_port', 8080)}"
            log("disco", f"ANNOUNCE from {addr[0]} -> {host} ({body.get('name')})")
            return host
    finally:
        sock.close()
    return None


# ------------------------------------------------------------------ sim ----
class UnoQSim:
    def __init__(self, host: str, dive_mode: str | None):
        self.host = host
        self.dive_mode = dive_mode
        self.device_id = device_id()
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.session_id: int | None = None
        self.heartbeat_ms = 2000
        self.evt_seq = 0
        self.pending_acks: dict[str, dict] = {}   # event_id -> message (retransmit until ACK)
        self.key_queue: asyncio.Queue[str] = asyncio.Queue()
        self.reconnects = 0

    # -------------------------------------------------------------- hello --
    def hello(self) -> dict:
        return {
            "type": "hello",
            "device_id": self.device_id,          # stable UUID, survives reconnect
            "client": "unoq",
            "device": "unoq",
            "roles": ["keeper_input"],
            "streams": [
                {"name": "dive", "schema": "event", "rate_hz": 0},
                {"name": "telem", "schema": "telem", "rate_hz": 1},
            ],
            "compute": {"has_npu": False, "units": ["cpu", "gpu", "mcu"],
                        "tops_est": 0.1},
            "net": {"mtu": 1500},
            "proto": 1,
        }

    # ---------------------------------------------------------------- run --
    async def run(self) -> None:
        threading.Thread(target=self._stdin_reader, daemon=True).start()
        while True:
            try:
                await self._session()
            except aiohttp.ClientError as e:
                log("net", f"connect failed: {e}")
            except asyncio.CancelledError:
                return
            # Reconnect: same device_id, exponential backoff with jitter (rule 10)
            self.reconnects += 1
            delay = min(5.0, 0.25 * (1.5 ** self.reconnects))
            delay *= random.uniform(0.8, 1.2)
            log("net", f"reconnecting in {delay:.2f}s (same device_id {self.device_id})")
            await asyncio.sleep(delay)

    async def _session(self) -> None:
        url = f"ws://{self.host}/ws"
        log("net", f"connecting {url}")
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(url, heartbeat=20) as ws:
                self.ws = ws
                await ws.send_json(self.hello())
                tasks = [asyncio.create_task(c) for c in (
                    self._rx(ws), self._heartbeat(ws), self._telem(ws),
                    self._retransmit(ws), self._dive_driver(ws))]
                try:
                    done, _ = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for t in tasks:
                        t.cancel()
                    self.ws = None

    # ----------------------------------------------------------------- rx --
    async def _rx(self, ws) -> None:
        """Print EVERY received message — rx visibly exercised (handoff §7)."""
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                break
            try:
                m = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            t = m.get("type")
            if t == "welcome":
                self.session_id = m.get("session_id")
                self.heartbeat_ms = int(m.get("heartbeat_ms", 2000))
                self.reconnects = 0
                log("rx", f"WELCOME session_id={self.session_id} "
                          f"hb={self.heartbeat_ms}ms negotiated={m.get('negotiated')}")
            elif t == "ack":
                eid = m.get("event_id")
                if eid in self.pending_acks:
                    del self.pending_acks[eid]
                    log("rx", f"ACK {eid}")
            elif t == "haptic":
                log("rx", f"HAPTIC pattern={m.get('pattern')} kick={m.get('kick')} "
                          f"zone={m.get('zone')}  *bzzzt* (LEDs: {m.get('pattern')})")
            elif t == "state":
                log("rx", f"state phase={m.get('phase')} kick={m.get('kick')}"
                          f"/{m.get('kicksTotal')} score={m.get('score')}")
            else:
                log("rx", f"{t}: {json.dumps(m)[:120]}")

    # -------------------------------------------------------------- loops --
    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_ms / 1000)
            await ws.send_json({"type": "hb", "ts_us": now_us()})

    async def _telem(self, ws) -> None:
        """Three units at 1 Hz with plausible values (rule 11). The MCU
        reports main-loop duty cycle — there are no OS counters there."""
        while True:
            await asyncio.sleep(1.0)
            base = time.monotonic()
            for unit, busy, metric, temp, state in (
                ("cpu", 9 + 4 * random.random(),
                 {"loop_hz": round(48 + 4 * random.random(), 1)}, 44, "session stack"),
                ("gpu", 0, {"fps": 0}, 41, "not implemented"),
                ("mcu", 20 + 6 * random.random(),
                 {"imu_hz": 208, "duty": "main-loop"}, 39, "dive watch"),
            ):
                await ws.send_json({
                    "type": "telem", "unit": unit,
                    "busy_pct": round(busy, 1), "metric": metric,
                    "temp_c": round(temp + 2 * random.random(), 1),
                    "state": state, "window_ms": 1000, "ts_us": now_us()})
            del base

    async def _retransmit(self, ws) -> None:
        """Discrete events: retransmit until ACKed, 250 ms cadence, 8 tries."""
        while True:
            await asyncio.sleep(0.25)
            for eid, rec in list(self.pending_acks.items()):
                if rec["tries"] >= 8:
                    log("event", f"{eid} gave up after 8 tries")
                    del self.pending_acks[eid]
                    continue
                rec["tries"] += 1
                await ws.send_json(rec["msg"])
                log("tx", f"retransmit {eid} (try {rec['tries']})")

    async def _dive_driver(self, ws) -> None:
        auto_task = None
        if self.dive_mode:
            async def auto():
                while True:
                    await asyncio.sleep(random.uniform(4, 7))
                    zone = (random.choice("LCR") if self.dive_mode == "auto"
                            else self.dive_mode)
                    await self.send_dive(ws, zone)
            auto_task = asyncio.create_task(auto())
        try:
            while True:
                key = await self.key_queue.get()
                if key in ("l", "c", "r"):
                    await self.send_dive(ws, key.upper())
        finally:
            if auto_task:
                auto_task.cancel()

    async def send_dive(self, ws, zone: str) -> None:
        self.evt_seq += 1
        eid = f"{self.device_id}:{self.evt_seq}"
        msg = {"type": "event", "kind": "dive", "event_id": eid,
               "data": {"zone": zone, "g_force": round(2.5 + random.random(), 2)},
               "ts_us": now_us()}
        self.pending_acks[eid] = {"msg": msg, "tries": 1}
        await ws.send_json(msg)
        log("tx", f"DIVE {zone} event_id={eid} (retransmits until ACK)")

    def _stdin_reader(self) -> None:
        for line in sys.stdin:
            k = line.strip().lower()
            if k:
                asyncio.run_coroutine_threadsafe(self.key_queue.put(k[0]), self._loop)

    _loop: asyncio.AbstractEventLoop


async def main() -> None:
    ap = argparse.ArgumentParser(description="UNO Q keeper simulator")
    ap.add_argument("--host", help="ip:port — skips discovery")
    ap.add_argument("--dive", nargs="?", const="auto",
                    choices=["auto", "L", "C", "R"],
                    help="dive on a timer (zone or 'auto' for random)")
    args = ap.parse_args()

    host = args.host
    if not host:
        host = discover()
        if not host:
            print("discovery failed — is the server running? "
                  "(or pass --host ip:8080)", file=sys.stderr)
            sys.exit(1)

    sim = UnoQSim(host, args.dive)
    sim._loop = asyncio.get_running_loop()
    log("sim", f"device_id={sim.device_id}  (type l/c/r + Enter to dive)")
    await sim.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
