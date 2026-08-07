import asyncio, json, random, sys
# dev harness: run server.py first (small GF_*_S values help), then: python test_match.py [seed]
import aiohttp

URL = "http://127.0.0.1:8080/ws"

async def phone():
    """Simulated striker: streams aim updates, swings on most kicks,
    deliberately freezes on the final kick to exercise the timeout path."""
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(URL) as ws:
            await ws.send_json({"type": "hello", "client": "phone"})
            kicked = None
            async for msg in ws:
                st = json.loads(msg.data)
                if st["phase"] in ("announce", "countdown", "shoot"):
                    await ws.send_json({"type": "aim", "zone": random.choice("LCR")})
                if st["phase"] == "shoot" and kicked != st["kick"]:
                    kicked = st["kick"]
                    if st["kick"] != st["kicksTotal"]:
                        await asyncio.sleep(random.uniform(0.1, 0.6))
                        await ws.send_json({"type": "kick", "zone": random.choice("LCR"),
                                            "power": random.random(),
                                            "force": random.randint(80, 400),
                                            "dirDeg": random.randint(-30, 45)})
                        await ws.send_json({"type": "skel", "kick": st["kick"],
                                            "frames": [{"t": i * 40, "p": [[0, 0, 0]] * 33}
                                                       for i in range(12)]})
                if st["phase"] == "end":
                    return st

async def tv():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(URL) as ws:
            await ws.send_json({"type": "hello", "client": "tv"})
            started = False
            async for msg in ws:
                st = json.loads(msg.data)
                if st["phase"] == "lobby" and st["connected"]["phone"] and not started:
                    started = True
                    await ws.send_json({"type": "start"})
                if st["phase"] == "end":
                    return st

async def main():
    random.seed(int(sys.argv[1]) if len(sys.argv) > 1 else None)
    st, _ = await asyncio.gather(tv(), phone())
    print("FINAL:", st["score"], "/", st["kicksTotal"], "| saves:", st["saves"], "| line:", st["line"])
    print("shotmap:")
    for s in st["shotmap"]:
        print("  ", s)
    # sanity checks
    assert st["phase"] == "end"
    assert len(st["shotmap"]) == st["kicksTotal"]
    assert st["score"] == sum(1 for s in st["shotmap"] if s["result"] == "goal")
    assert st["saves"] == sum(1 for s in st["shotmap"] if s["result"] != "goal")
    assert st["shotmap"][-1]["result"] == "over", "frozen final kick should time out"
    assert st["shotmap"][-1]["timedOut"] is True, "frozen kick must be marked as timeout"
    assert all(s["keeperZone"] in "LCR" for s in st["shotmap"])
    assert all(s["force"] > 0 for s in st["shotmap"] if s["result"] != "over"), "ForcePose Newtons missing"
    assert st["replay"] is None, "a timed-out attempt must not retain a phantom replay"
    print("OK — match completed, scores consistent, timeout handled, ForcePose stored")

asyncio.run(main())
