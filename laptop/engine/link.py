"""Engine side of the server/engine boundary.

Imports only the *contract* from server.link (pure stdlib, no transport).
The engine never sees a socket for game traffic; TcpEngineLink's loopback
stream client is the IPC pipe itself, not a device transport.
"""

from __future__ import annotations

import asyncio
import json

from server.link import (ENGINE_PORT_DEFAULT, Broadcast, DeviceInfo,  # noqa: F401
                         DeviceState, Event, _from_wire, _to_wire)


class EngineSideLink:
    async def recv(self):
        """Next inbound item: DeviceInfo | DeviceState | Event | ("left", id)."""
        raise NotImplementedError

    async def broadcast(self, payload: dict, target: str = "all") -> None:
        raise NotImplementedError


class InProcEngineLink(EngineSideLink):
    """Same-process pair of the server's InProcLink."""

    def __init__(self, to_engine: asyncio.Queue, from_engine: asyncio.Queue):
        self._in = to_engine
        self._out = from_engine

    async def recv(self):
        return await self._in.get()

    async def broadcast(self, payload: dict, target: str = "all") -> None:
        await self._out.put(Broadcast(payload=payload, target=target))


class TcpEngineLink(EngineSideLink):
    """Separate-process pair of the server's TcpLink. Reconnects forever with
    a short backoff — the server holds sessions while the engine is away."""

    def __init__(self, port: int = ENGINE_PORT_DEFAULT):
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def _ensure(self) -> None:
        while self._reader is None:
            try:
                self._reader, self._writer = await asyncio.open_connection(
                    "127.0.0.1", self._port)
                print(f"[engine] linked to server on 127.0.0.1:{self._port}")
            except OSError:
                await asyncio.sleep(0.5)

    async def recv(self):
        while True:
            await self._ensure()
            try:
                line = await self._reader.readline()
            except (ConnectionError, asyncio.IncompleteReadError):
                line = b""
            if not line:
                print("[engine] link lost — retrying")
                self._reader = self._writer = None
                continue
            try:
                item = _from_wire(json.loads(line))
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
            if item is not None:
                return item

    async def broadcast(self, payload: dict, target: str = "all") -> None:
        await self._ensure()
        try:
            self._writer.write(json.dumps(
                _to_wire(Broadcast(payload=payload, target=target))).encode() + b"\n")
            await self._writer.drain()
        except (ConnectionError, RuntimeError):
            self._reader = self._writer = None
