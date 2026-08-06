#!/usr/bin/env python3
"""P2 telemetry checks (handoff-2). Against a live server on :8080.

Verifies, via a dashboard WS client:
  - laptop.cpu carries the server self-report
  - laptop.npu carries the engine's GenieX cell with placement "unverified"
    (a URL cannot prove Hexagon — the cell must not fake green)
  - a TV client's `telem` lands on laptop.gpu with fps/draw_ms
  - a phone client's `telem` puts the fallback rung on the phone NPU cell
  - a killed device's cells go stale (age_ms > stale_ms) — dead ≠ idle
"""

import asyncio
import json
import time

import aiohttp

URL = "ws://127.0.0.1:8080/ws"
CHECKS = []


def ok(name):
    CHECKS.append(name)
    print(f"  PASS  {name}")


async def main():
    async with aiohttp.ClientSession() as http:
        # --- producers -------------------------------------------------
        tv = await http.ws_connect(URL)
        await tv.send_json({"type": "hello", "client": "tv"})
        phone = await http.ws_connect(URL)
        await phone.send_json({"type": "hello", "client": "phone",
                               "device_id": "phone-telem-test", "device": "phone",
                               "roles": ["phone"], "proto": 1})
        unoq = await http.ws_connect(URL)
        await unoq.send_json({"type": "hello", "client": "unoq",
                              "device_id": "unoq-telem-test", "device": "unoq",
                              "roles": ["keeper_input"], "proto": 1})

        async def drain(ws):
            try:
                async for _ in ws:
                    pass
            except Exception:
                pass
        drains = [asyncio.create_task(drain(w)) for w in (tv, phone, unoq)]

        async def feed():
            for _ in range(12):
                await tv.send_json({"type": "telem", "unit": "gpu", "source": "tv",
                                    "busy_pct": 41.0,
                                    "metric": {"fps": 60, "draw_ms": 6.8},
                                    "state": "canvas2d (main-thread raster)"})
                await phone.send_json({"type": "telem", "unit": "npu",
                                       "busy_pct": 0,
                                       "metric": {"rung": "NPU", "pose_ms": 23},
                                       "state": "rung: NPU"})
                await unoq.send_json({"type": "telem", "unit": "mcu",
                                      "busy_pct": 22, "metric": {"imu_hz": 208},
                                      "temp_c": 41, "state": "dive watch"})
                await asyncio.sleep(1.0)
        feeder = asyncio.create_task(feed())

        # --- dashboard -------------------------------------------------
        dash = await http.ws_connect(URL)
        await dash.send_json({"type": "hello", "client": "dashboard",
                              "roles": ["dashboard"], "device": "laptop",
                              "device_id": "dash-telem-test"})
        got = {}
        deadline = time.monotonic() + 20
        async for m in dash:
            snap = json.loads(m.data)
            if snap.get("type") != "telem_state":
                continue
            d = snap["devices"]
            lap = d.get("laptop", {}).get("units", {})
            ph = d.get("phone", {}).get("units", {})
            uq = d.get("unoq", {}).get("units", {})
            if ("cpu" in lap and "npu" in lap and "gpu" in lap
                    and "npu" in ph and "mcu" in uq):
                got = {"lap": lap, "ph": ph, "uq": uq, "stale_ms": snap["stale_ms"]}
                break
            if time.monotonic() > deadline:
                raise AssertionError(f"cells never filled: laptop={list(lap)} "
                                     f"phone={list(ph)} unoq={list(uq)}")

        assert "asyncio" in got["lap"]["cpu"]["state"], got["lap"]["cpu"]
        assert "msg_rate" in got["lap"]["cpu"]["metric"]
        ok("laptop.cpu: server self-report (CPU% + msg_rate)")

        npu = got["lap"]["npu"]
        assert npu["metric"].get("placement") == "unverified", npu
        assert "breaker" in npu["metric"]
        ok('laptop.npu: GenieX cell present, placement "unverified" — not faked green')

        gpu = got["lap"]["gpu"]
        assert gpu["metric"].get("fps") == 60 and "draw_ms" in gpu["metric"]
        ok("laptop.gpu: TV render telemetry (fps + draw_ms)")

        ph_npu = got["ph"]["npu"]
        assert "rung" in ph_npu["state"].lower() or ph_npu["metric"].get("rung")
        ok(f"phone.npu: fallback rung visible ({ph_npu['state']!r})")

        assert got["uq"]["mcu"]["metric"].get("imu_hz") == 208
        ok("unoq.mcu: simulator telemetry aggregated")

        # --- staleness: kill the unoq producer, cells must go stale ----
        feeder.cancel()
        await unoq.close()
        # The dashboard socket keeps delivering snapshots queued during the
        # kill — loop until a FRESH one shows the cell aged past stale_ms.
        stale_deadline = time.monotonic() + 15
        async for m in dash:
            snap = json.loads(m.data)
            if snap.get("type") != "telem_state":
                continue
            uq = snap["devices"].get("unoq", {}).get("units", {}).get("mcu", {})
            if uq.get("age_ms", 0) > snap["stale_ms"]:
                ok(f"staleness: unoq.mcu age {uq['age_ms']}ms > {snap['stale_ms']}ms after kill")
                break
            assert time.monotonic() < stale_deadline, \
                f"cell never went stale: {uq}"

        for t in drains:
            t.cancel()
    print(f"\nALL {len(CHECKS)} TELEMETRY CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), 60))
