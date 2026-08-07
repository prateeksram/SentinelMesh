"""Post-install verification for the QPlay (SentinelMesh) host.

Run from the repository root after `pip install -r requirements.txt`:

    python setup_check/verify_setup.py

Checks, in order:
  1. Python version (3.11+; 3.13/3.14 verified)
  2. Required packages import (aiohttp, PIL, qrcode)
  3. The game server module imports and its referee geometry behaves
  4. The aiohttp app boots on an ephemeral port and answers its health routes

Read-only: nothing in the repository is modified, port 8080 is not touched.
Exit code 0 = setup is good; 1 = a check failed (details printed).
"""

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "ok  " if ok else "FAIL"
    line = f"[{mark}] {label}"
    if detail:
        line += f" - {detail}"
    print(line)
    if not ok:
        FAILURES.append(label)


def check_python() -> None:
    v = sys.version_info
    check(
        "Python version",
        v >= (3, 11),
        f"{v.major}.{v.minor}.{v.micro} (need 3.11+, 3.13/3.14 verified)",
    )


def check_packages() -> None:
    for mod, hint in (
        ("aiohttp", "python -m pip install -r requirements.txt"),
        ("PIL", "python -m pip install -r requirements.txt  (pillow)"),
        ("qrcode", "python -m pip install -r requirements.txt"),
    ):
        try:
            __import__(mod)
            check(f"import {mod}", True)
        except ImportError:
            check(f"import {mod}", False, hint)


def check_server_logic():
    try:
        import server  # noqa: F401
    except Exception as exc:  # pragma: no cover - reported to the user
        check("import server", False, repr(exc))
        return None

    check("import server", True)

    # Referee geometry: regulation goal is 7.32 x 2.44 m.
    check("goal geometry", server.GOAL_HALF_W_M == 3.66 and server.GOAL_H_M == 2.44)
    check(
        "zone mapping",
        server.zone_of_x(-2.4) == "L"
        and server.zone_of_x(0.0) == "C"
        and server.zone_of_x(2.4) == "R",
    )

    game = server.Game(server.Desk())
    # Darts rings: 0.05 m from the bull = 100 pts; 2 m out = miss.
    game.sport = "darts"
    r_bull = game.referee_target(0.05, 1.73)
    r_miss = game.referee_target(2.0, 1.73)
    check(
        "darts ring scoring",
        r_bull[1] == 100 and r_miss[1] == 0,
        f"bull={r_bull[1]} far={r_miss[1]}",
    )
    # Football hybrid referee: 5 m wide of a 3.66 m half-width goal is WIDE.
    game.sport = "football"
    wide = game.referee_metric(5.0, 1.0, 0.8, "C")
    check("football wide gate", wide[0] == "wide", f"got {wide[0]!r}")
    return server


async def check_http(server_mod) -> None:
    import aiohttp
    from aiohttp import web

    app = server_mod.make_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)  # ephemeral port; 8080 untouched
    await site.start()
    port = runner.addresses[0][1]
    try:
        async with aiohttp.ClientSession() as http:
            for route, key in (
                ("/edge/status", "server"),
                ("/fx/status", "backend"),
                ("/hw/status", "desk"),
            ):
                async with http.get(f"http://127.0.0.1:{port}{route}") as resp:
                    body = await resp.json()
                    check(
                        f"GET {route}",
                        resp.status == 200 and key in body,
                        f"status={resp.status}",
                    )
            async with http.get(f"http://127.0.0.1:{port}/tv.html") as resp:
                text = await resp.text()
                check(
                    "GET /tv.html",
                    resp.status == 200 and "<html" in text.lower(),
                    f"status={resp.status}, {len(text)} bytes",
                )
    finally:
        await runner.cleanup()


def main() -> int:
    print(f"QPlay setup check - repo: {REPO_ROOT}\n")
    check_python()
    check_packages()
    if FAILURES:
        # No point booting the server without its dependencies.
        print(f"\n{len(FAILURES)} check(s) failed.")
        return 1
    server_mod = check_server_logic()
    if server_mod is not None:
        asyncio.run(check_http(server_mod))
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("All checks passed - the host is ready. Next: python server.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
