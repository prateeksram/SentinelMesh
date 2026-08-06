"""End-to-end simulation: phone+TV harness against a live laptop server.

Requires:
  - geniex serve on :18181 (optional — templates still OK)
  - python server.py on :8080 (this branch)

Usage:
  set GF_ANNOUNCE_S=0.4 GF_COUNTDOWN_S=0.4 GF_RESOLVE_S=0.5 GF_SHOOT_WINDOW=1.2
  python e2e_sim.py [seed]
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time

import aiohttp

WS = "http://127.0.0.1:8080/ws"
HTTP = "http://127.0.0.1:8080"


async def check_http(session: aiohttp.ClientSession) -> dict:
    out = {}
    for path in ("/hw/status", "/fx/status", "/scene/status"):
        async with session.get(HTTP + path) as r:
            out[path] = {"status": r.status, "body": await r.json()}
    # Hero plate (skeleton silhouette → depth or procedural)
    frames = [
        {"t": i * 40, "p": [[(j % 10) / 10 - 0.5, (j // 10) / 10 - 0.2, 0] for j in range(33)]}
        for i in range(8)
    ]
    async with session.post(
        HTTP + "/fx/hero",
        json={"frames": frames, "force": 260, "result": "goal", "strike": "drive"},
    ) as r:
        hero = await r.json()
        out["/fx/hero"] = {
            "status": r.status,
            "ok": hero.get("ok"),
            "backend": hero.get("backend"),
            "ms": hero.get("ms"),
            "plate": bool(hero.get("plate")),
        }
    return out


async def phone():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS) as ws:
            await ws.send_json({"type": "hello", "client": "phone"})
            kicked = None
            saw_generating = False
            last = None
            async for msg in ws:
                st = json.loads(msg.data)
                last = st
                if st["phase"] == "generating":
                    saw_generating = True
                if st["phase"] in ("announce", "countdown", "shoot"):
                    await ws.send_json({"type": "aim", "zone": random.choice("LCR")})
                if st["phase"] == "shoot" and kicked != st["kick"]:
                    kicked = st["kick"]
                    if st["kick"] != 3:
                        await asyncio.sleep(random.uniform(0.05, 0.25))
                        await ws.send_json(
                            {
                                "type": "kick",
                                "zone": random.choice("LCR"),
                                "power": random.random(),
                                "force": random.randint(80, 400),
                                "dirDeg": random.randint(-30, 45),
                                "height": random.choice(["H", "L"]),
                                "spin": round(random.uniform(-1, 1), 2),
                                "strike": random.choice(["chip", "drive"]),
                                "foot": random.choice(["L", "R"]),
                            }
                        )
                        await ws.send_json(
                            {
                                "type": "skel",
                                "kick": st["kick"],
                                "frames": [
                                    {"t": i * 40, "p": [[0, 0, 0]] * 33} for i in range(12)
                                ],
                            }
                        )
                if st["phase"] == "end":
                    st["_saw_generating"] = saw_generating
                    return st
            return last


async def tv():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS) as ws:
            await ws.send_json({"type": "hello", "client": "tv"})
            started = False
            phases = []
            async for msg in ws:
                st = json.loads(msg.data)
                if not phases or phases[-1] != st["phase"]:
                    phases.append(st["phase"])
                    print(
                        f"  phase={st['phase']}"
                        + (
                            f" gen={st.get('genProgress')}%"
                            if st["phase"] == "generating"
                            else ""
                        )
                        + (f" level={st.get('level')}" if st.get("level") else "")
                    )
                if st["phase"] == "lobby" and st["connected"]["phone"] and not started:
                    started = True
                    await ws.send_json({"type": "start"})
                if st["phase"] == "end":
                    st["_phases"] = phases
                    return st


async def main():
    random.seed(int(sys.argv[1]) if len(sys.argv) > 1 else 42)
    t0 = time.perf_counter()
    async with aiohttp.ClientSession() as s:
        http = await check_http(s)
    print("HTTP checks:")
    for k, v in http.items():
        print(f"  {k}: {v}")

    assert http["/hw/status"]["status"] == 200
    assert http["/hw/status"]["body"].get("desk") == "geniex"
    assert http["/fx/status"]["status"] == 200
    assert http["/fx/hero"]["ok"] and http["/fx/hero"]["plate"]
    assert http["/fx/hero"]["backend"] in ("cpu", "qnn", "procedural")

    print("\nMatch simulation:")
    st, tv_st = await asyncio.gather(phone(), tv())
    elapsed = time.perf_counter() - t0

    print(
        f"\nFINAL: {st['score']}/{st['kicksTotal']} saves={st['saves']} "
        f"level={st.get('level')} src={((st.get('sceneMetrics') or {}).get('source'))}"
    )
    print(f"line: {st.get('line')}")
    print(f"report: {st.get('report')}")
    print("shotmap:")
    for s in st["shotmap"]:
        print(" ", s)
    print("phases:", tv_st.get("_phases"))

    # Core match invariants
    assert st["phase"] == "end"
    assert len(st["shotmap"]) == st["kicksTotal"]
    assert st["score"] == sum(1 for s in st["shotmap"] if s["result"] == "goal")
    assert st["saves"] == sum(1 for s in st["shotmap"] if s["result"] != "goal")
    assert st["shotmap"][2]["result"] == "over", "frozen kick 3 should be skied"
    assert all(s["keeperZone"] in "LCR" for s in st["shotmap"])
    assert all(s["force"] > 0 for s in st["shotmap"] if s["result"] != "over")
    assert st["replay"] and st["replay"]["kick"] == st["kicksTotal"]

    # Four-pillar extras
    assert st.get("_saw_generating") or "generating" in (tv_st.get("_phases") or [])
    assert st.get("level") >= 1
    assert st.get("scene"), "scene missing on end snapshot"
    assert st["scene"].get("atmosphere") and st["scene"].get("difficulty")
    assert st.get("sceneMetrics", {}).get("source") in ("geniex", "template")
    assert st.get("report") is not None
    d = st["scene"]["difficulty"]
    for k in ("keeperIq", "keeperReaction", "shootWindow", "powerBeat"):
        assert k in d

    print(
        f"\nOK — E2E sim passed in {elapsed:.1f}s "
        f"(desk={http['/hw/status']['body']['desk']}, "
        f"fx={http['/fx/hero']['backend']}/{http['/fx/hero']['ms']}ms, "
        f"scene={st['sceneMetrics']['source']})"
    )


if __name__ == "__main__":
    asyncio.run(main())
