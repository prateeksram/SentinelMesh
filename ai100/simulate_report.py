"""Generate the deterministic demo scouting report, optionally with real AI100 art."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--player", default="DEMO STRIKER")
    parser.add_argument("--env", type=Path, help="Optional .env containing AI100 settings")
    args = parser.parse_args()
    if args.env:
        os.environ["AI100_ENV_FILE"] = str(args.env.resolve())

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ai100 import report_engine

    store = report_engine.ReportStore()
    card = await store.create(report_engine.sample_shotmap(), 3, args.player)
    safe = {
        "status": card["status"],
        "token": card["token"],
        "png": str((report_engine.DATA / f"{card['token']}.png").resolve()),
        "pdf": str((report_engine.DATA / f"{card['token']}.pdf").resolve()),
        "preview": card["preview"],
        "ai": card["ai"],
    }
    print(json.dumps(safe, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
