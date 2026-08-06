#!/usr/bin/env python3
"""Acceptance tests for the device server (handoff §9).

Covers, against a live server on 127.0.0.1:8080:

  #3  extended device registers, telemetry cells light
  #4  a `dive` event decides the keeper's zone in the game
  #5  the device receives a broadcast (haptic) — rx path proven
  #7  reconnect with the same device_id resumes the same session_id
  #8  duplicate HELLO returns the same session_id, no second session
  #9  duplicate event_id reaches the engine exactly once
  #11 the phone's outbound snapshot carries no replay/shotmap
  (+) discovery: DISCOVER datagram -> ANNOUNCE
  (#6/#12 style) the match runs to completion throughout

Run with fast pacing, like test_match.py:

  GF_ANNOUNCE_S=0.1 GF_COUNTDOWN_S=0.2 GF_SHOOT_WINDOW=1.0 GF_RESOLVE_S=0.1 \
      python server.py &
  python test_devices.py
"""

import asyncio
import json
import random
import socket
import struct
import sys
import time

import aiohttp

URL = "ws://127.0.0.1:8080/ws"
HEADER = struct.Struct(">BBBBIIQ")
CHECKS: list[str] = []


def ok(name: str) -> None:
    CHECKS.append(name)
    print(f"  PASS  {name}")


# --------------------------------------------------------------- discovery --
def check_discovery() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2.0)
    s.sendto(HEADER.pack(1, 3, 0, 0, 0, 0, 12345), ("127.0.0.1", 8079))
    data, addr = s.recvfrom(1400)
    ver, mtype, *_rest = HEADER.unpack_from(data)
    assert ver == 1 and mtype == 4, f"bad ANNOUNCE header {ver}/{mtype}"
    body = json.loads(data[HEADER.size:])
    assert body.get("ws_port") == 8080, body
    s.close()
    ok("discovery: DISCOVER -> ANNOUNCE with ws_port")


# ------------------------------------------------------------------ clients --
async def tv(state: dict) -> None:
    async with aiohttp.ClientSession() as s, s.ws_connect(URL) as ws:
        await ws.send_json({"type": "hello", "client": "tv"})
        started = False

        async def starter():
            # Poll instead of waiting on rebroadcasts: identical snapshots are
            # (correctly) suppressed per session, so lobby state arrives once.
            nonlocal started
            while not started:
                await asyncio.sleep(0.25)
                st = state.get("tv_last")
                if not st:
                    continue
                if st["phase"] == "end":
                    await ws.send_json({"type": "again"})
                    state["tv_last"] = None
                elif (st["phase"] == "lobby" and st["connected"]["phone"]
                      and state.get("unoq_ready")):
                    started = True
                    await ws.send_json({"type": "start"})

        poll = asyncio.create_task(starter())
        try:
            async for m in ws:
                st = json.loads(m.data)
                if st.get("type") != "state":
                    continue
                state["phase"] = st["phase"]
                state["tv_last"] = st
                if st["phase"] == "end" and started:
                    state["final"] = st
                    return
        finally:
            poll.cancel()


async def phone(state: dict) -> None:
    async with aiohttp.ClientSession() as s, s.ws_connect(URL) as ws:
        await ws.send_json({"type": "hello", "client": "phone"})
        kicked = 0
        async for m in ws:
            st = json.loads(m.data)
            if st.get("type") != "state":
                continue
            # criterion 11 (+ handoff-2 P1) — the phone snapshot must never
            # carry the heavy or TV-only keys
            for key in ("replay", "shotmap", "report", "genProgress"):
                assert key not in st, f"phone snapshot leaked {key!r}: {list(st)}"
            state["phone_filtered_seen"] = True
            if st["phase"] in ("announce", "countdown", "shoot"):
                await ws.send_json({"type": "aim", "zone": random.choice("LCR")})
            if st["phase"] == "shoot" and kicked != st["kick"]:
                kicked = st["kick"]
                await asyncio.sleep(0.45)          # let the keeper dive first
                await ws.send_json({
                    "type": "kick", "zone": random.choice("LCR"),
                    "power": round(random.uniform(0.4, 1.0), 2),
                    "force": random.randint(80, 400),
                    "dirDeg": random.randint(-30, 45)})
                # bullet-time replay for this kick (exercises tv-side replay path)
                frames = [{"t": -400 + i * 40, "p": [[0.0, 0.0, 0.0]] * 33}
                          for i in range(10)]
                await ws.send_json({"type": "skel", "kick": kicked, "frames": frames})
            if st["phase"] == "end" and kicked >= 1:
                return


async def unoq(state: dict) -> None:
    device_id = f"unoq-test-{random.randrange(16**6):06x}"
    state["unoq_device_id"] = device_id
    hello = {"type": "hello", "device_id": device_id, "client": "unoq",
             "device": "unoq", "roles": ["keeper_input"],
             "streams": [{"name": "dive", "rate_hz": 0}],
             "compute": {"has_npu": False, "units": ["cpu", "gpu", "mcu"]},
             "proto": 1}
    acks, haptics = [], []
    async with aiohttp.ClientSession() as s, s.ws_connect(URL) as ws:
        await ws.send_json(hello)

        first_sid = None
        dove_kick = 0

        async def rx():
            nonlocal first_sid
            async for m in ws:
                msg = json.loads(m.data)
                t = msg.get("type")
                if t == "welcome":
                    if first_sid is None:
                        first_sid = msg["session_id"]
                        state["unoq_sid"] = first_sid
                    else:
                        # criterion 8 — duplicate HELLO, same session
                        assert msg["session_id"] == first_sid, \
                            f"dup HELLO minted new session {msg['session_id']} != {first_sid}"
                        ok("idempotent HELLO: duplicate returned the same session_id")
                        state["unoq_ready"] = True
                elif t == "ack":
                    acks.append(msg["event_id"])
                elif t == "haptic":
                    haptics.append(msg)
                elif t == "state":
                    state["unoq_phase"] = (msg["phase"], msg["kick"])

        rx_task = asyncio.create_task(rx())
        await asyncio.sleep(0.3)
        await ws.send_json(hello)                  # duplicate HELLO (criterion 8)

        # telemetry for three units — lights the cells (criterion 3)
        for unit in ("cpu", "gpu", "mcu"):
            await ws.send_json({"type": "telem", "unit": unit, "busy_pct": 21,
                                "metric": {"imu_hz": 208}, "temp_c": 40,
                                "state": "test"})

        # dive on kick 1's shoot phase: same event_id sent twice with DIFFERENT
        # zones — dedup means the engine must keep the FIRST (criterion 9)
        while state.get("unoq_phase", ("", 0)) != ("shoot", 1):
            await asyncio.sleep(0.02)
        eid = f"{device_id}:1"
        await ws.send_json({"type": "event", "kind": "dive", "event_id": eid,
                            "data": {"zone": "L"}})
        await ws.send_json({"type": "event", "kind": "dive", "event_id": eid,
                            "data": {"zone": "R"}})    # duplicate — must be dropped
        await asyncio.sleep(0.5)
        assert acks.count(eid) == 2, f"expected 2 ACKs (dup ACKed too), got {acks}"
        ok("duplicate event ACKed both times (sender's lost-ACK case covered)")

        # wait for kick 1 to resolve, then check the haptic arrived (criterion 5)
        for _ in range(100):
            if haptics:
                break
            await asyncio.sleep(0.05)
        assert haptics and haptics[0]["kick"] == 1, f"no haptic broadcast: {haptics}"
        assert haptics[0]["zone"] == "L", \
            f"dedup failed: keeper took the duplicate's zone {haptics[0]}"
        ok("dive decided the keeper zone; duplicate event_id seen exactly once")
        ok("device received a targeted broadcast (haptic) — rx path proven")
        state["haptic"] = haptics[0]

        rx_task.cancel()
    # --- reconnect with the same device_id (criterion 7) --------------------
    async with aiohttp.ClientSession() as s, s.ws_connect(URL) as ws:
        await ws.send_json(hello)
        async for m in ws:
            msg = json.loads(m.data)
            if msg.get("type") == "welcome":
                assert msg["session_id"] == state["unoq_sid"], \
                    f"reconnect minted new session {msg['session_id']}"
                ok("reconnect with same device_id resumed the same session_id")
                return


async def dashboard(state: dict) -> None:
    async with aiohttp.ClientSession() as s, s.ws_connect(URL) as ws:
        await ws.send_json({"type": "hello", "client": "dashboard",
                            "roles": ["dashboard"], "device": "laptop",
                            "device_id": "dash-test"})
        deadline = time.monotonic() + 20
        async for m in ws:
            msg = json.loads(m.data)
            if msg.get("type") == "telem_state":
                devs = msg["devices"]
                if "unoq" in devs and "mcu" in devs["unoq"]["units"] \
                        and "laptop" in devs and "cpu" in devs["laptop"]["units"]:
                    assert devs["laptop"]["units"]["cpu"]["state"], "no server self-report"
                    ok("telemetry: unoq mcu/cpu/gpu cells + laptop self-report aggregated")
                    return
            if time.monotonic() > deadline:
                raise AssertionError("telem_state never carried unoq units")


async def main() -> None:
    check_discovery()
    state: dict = {}
    await asyncio.wait_for(asyncio.gather(
        tv(state), phone(state), unoq(state), dashboard(state)), timeout=90)

    final = state["final"]
    assert final["phase"] == "end", "match did not finish"
    assert len(final["shotmap"]) == final["kicksTotal"]
    k1 = final["shotmap"][0]
    assert k1["keeperZone"] == "L" and k1.get("keeperSrc") == "device", k1
    ok("match ran to completion; kick 1 keeper was the device (zone L)")
    assert state.get("phone_filtered_seen")
    ok("phone snapshots filtered throughout the match (no replay/shotmap/report/genProgress)")
    # four pillars survived the split
    assert final.get("scene") and final["scene"].get("difficulty"), "scene missing on end"
    assert (final.get("sceneMetrics") or {}).get("source") in ("geniex", "template")
    assert final.get("level", 0) >= 1
    ok(f"four pillars intact: level={final['level']} "
       f"scene={final['sceneMetrics']['source']}")

    print(f"\nALL {len(CHECKS)} ACCEPTANCE CHECKS PASSED")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as e:
        print(f"\nFAIL: {e}")
        sys.exit(1)
