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

    async def prog(p):
        print("  ", p, "%")

    s = await scene_engine.generate(ctx, lvl, prog)
    print(
        f"score {score}/5 -> L{lvl} {s['timeOfDay']} "
        f"iq={s['difficulty']['keeperIq']} src={s['metrics']['source']} "
        f"{s['metrics'].get('total_ms', '?')}ms"
    )


async def main():
    for sc in (1, 3):
        await fake(sc)


if __name__ == "__main__":
    asyncio.run(main())
