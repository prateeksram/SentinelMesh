"""Smoke-test SceneEngine for score 1/5 vs 3/5 (GenieX up or template fallback)."""

import asyncio

import scene_engine


async def fake(score):
    shot = [
        {
            "zone": "L",
            "keeperZone": "R",
            "force": 200 + score * 20,
            "strike": "drive",
        }
    ] * 5
    ctx = scene_engine.build_context(score, 5 - score, 5, shot)
    lvl = scene_engine.pick_next_level(score, 1)

    async def prog(p, step=""):
        print(f"  {p}% {step}")

    s = await scene_engine.generate(ctx, lvl, prog)
    print(
        f"score {score}/5 -> L{lvl} {s['timeOfDay']} "
        f"iq={s['difficulty']['keeperIq']} src={s['metrics']['source']} "
        f"fp={s.get('fingerprint')} verified={s.get('verified')} "
        f"{s['metrics'].get('total_ms', '?')}ms"
    )
    return s


async def main():
    a = await fake(1)
    b = await fake(3)
    assert a.get("fingerprint") != b.get("fingerprint"), "venues should differ"
    assert a.get("verified") and b.get("verified")


if __name__ == "__main__":
    asyncio.run(main())
