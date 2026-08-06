"""Quick check for /scene upload + brief helpers."""

import asyncio

import scene_engine


async def main():
    r = await scene_engine.upload_and_promote(
        level=2,
        css="body{outline:1px solid red}",
        overlay_html='<div id="venueTitleCard">Up</div>',
        ctx={"score": 2, "saves": 3, "kicks_total": 5},
    )
    assert r.get("ok"), r
    assert r.get("tvUrl")
    b = scene_engine.build_brief(2)
    assert "contract_summary" in b and "instructions" in b
    print("upload+brief OK", r["tvUrl"], r["verify"]["fingerprint"])


if __name__ == "__main__":
    asyncio.run(main())
