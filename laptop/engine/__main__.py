"""Standalone engine process for the TCP link mode.

    python -m engine            # connects to the server on 127.0.0.1:8899

Kill and restart this freely while devices stay connected — the server holds
their sessions, and this process re-receives the roster on reconnect. That is
the test that proves the boundary is real (handoff §5).
"""

import asyncio
import os

from .link import TcpEngineLink
from .match import Engine


async def main() -> None:
    port = int(os.environ.get("GF_ENGINE_PORT", 8899))
    engine = Engine(TcpEngineLink(port=port))
    await engine.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
