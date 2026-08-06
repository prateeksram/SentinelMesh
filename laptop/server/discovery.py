"""Discovery beacon (A§6.4): UDP :8079, DISCOVER in, unicast ANNOUNCE out.

A device broadcasts a header-only DISCOVER datagram; the server replies
unicast with a 20-byte header + JSON body naming the WebSocket port. The
device takes the server's address from the ANNOUNCE packet's *source IP* —
no IP is ever carried in the body, so nothing can be hardcoded stale.

Fixed-layout header + tiny body deliberately: either side of the UNO Q
(Linux or MCU) can speak this in ~20 lines without a CBOR decoder or an
mDNS responder.
"""

from __future__ import annotations

import asyncio
import json

from .protocol import header
from .protocol.messages import Msg

DISCOVERY_PORT = 8079


class DiscoveryResponder(asyncio.DatagramProtocol):
    def __init__(self, ws_port: int = 8080, name: str = "gesture-football"):
        self.ws_port = ws_port
        self.name = name
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        if len(data) > header.MAX_DATAGRAM:
            return                                  # reject oversized before parsing
        h = header.unpack(data)
        if h is None or h.msg_type != Msg.DISCOVER:
            return                                  # drop silently (A§8.4)
        body = json.dumps({
            "name": self.name,
            "proto": header.VERSION,
            "ws_port": self.ws_port,
            "ws_path": "/ws",
        }).encode()
        self.transport.sendto(header.pack(Msg.ANNOUNCE, ts_us=h.ts_us) + body, addr)


async def start_discovery(ws_port: int = 8080,
                          port: int = DISCOVERY_PORT) -> asyncio.DatagramTransport | None:
    loop = asyncio.get_running_loop()
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: DiscoveryResponder(ws_port=ws_port),
            local_addr=("0.0.0.0", port), allow_broadcast=True)
        print(f"Disco :  udp://0.0.0.0:{port}  (DISCOVER -> ANNOUNCE)")
        return transport
    except OSError as e:
        print(f"Disco :  off ({e}) — devices must use a manual host address")
        return None
