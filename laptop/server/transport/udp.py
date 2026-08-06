"""UDP data-plane transport — interface only (handoff §2: no UDP yet).

The slot exists so the later UDP phase plugs in beside the WebSocket adapter
without touching the server core. The discovery beacon is separate and live —
see server/discovery.py; this stub is the *data plane* (aim/kick/telem over
datagrams with the 20-byte header), which arrives in a later phase.
"""

from __future__ import annotations

from .base import Hub, Transport


class UdpTransport(Transport):
    def __init__(self, port: int = 47811):
        self.port = port

    async def start(self, hub: Hub) -> None:
        raise NotImplementedError(
            "UDP data plane is a later phase — use the WebSocket adapter (transport/ws.py)"
        )

    async def stop(self) -> None:
        raise NotImplementedError
