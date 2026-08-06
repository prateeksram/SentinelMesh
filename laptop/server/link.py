"""The server/engine boundary (A§9, handoff §5).

Contract, both directions:

    server -> engine   joined / left / sample(DeviceState) / event(Event)
                       (InputFrame is the tick-aligned aggregate view;
                        the engine may also consume items as they arrive —
                        A§9.2's relaxed tick model)
    engine -> server   Broadcast(payload, target)

The engine never sees a socket, a session ID, or a packet. Two implementations
behind one interface (A§9.3):

- InProcLink  — in-memory queues, engine runs as a task in the server's loop.
  The default: one process to start on demo day.
- TcpLink     — NDJSON over 127.0.0.1. The engine is a separate process that
  can be killed and restarted while sessions survive — the test that proves
  the boundary is real.

This module is pure stdlib. It must never import aiohttp.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable

ENGINE_PORT_DEFAULT = 8899


def now_us() -> int:
    return time.monotonic_ns() // 1000


# ================================================================ contract ==
@dataclass(frozen=True)
class DeviceState:
    """Continuous channel sample — latest-wins, never retransmitted."""
    device_id: str
    role: str
    stream: str
    data: dict
    server_ts_us: int = field(default_factory=now_us)


@dataclass(frozen=True)
class Event:
    """Discrete channel — the engine sees each exactly once (dedup upstream)."""
    kind: str
    device_id: str
    role: str
    data: dict
    event_id: str | None = None
    server_ts_us: int = field(default_factory=now_us)


@dataclass(frozen=True)
class DeviceInfo:
    device_id: str
    roles: tuple[str, ...]
    device: str = "unknown"


@dataclass(frozen=True)
class InputFrame:
    """Tick-aligned aggregate (contract completeness; the shipped engine
    consumes items as they arrive per A§9.2)."""
    tick_id: int
    server_time_us: int
    devices: list[DeviceState]
    events: list[Event]
    joined: list[DeviceInfo]
    left: list[str]


@dataclass(frozen=True)
class Broadcast:
    payload: dict
    target: str = "all"        # "all" | "role:<r>" | "<device_id>"


def _to_wire(item) -> dict:
    d = asdict(item)
    d["_t"] = type(item).__name__
    return d


def _from_wire(d: dict):
    t = d.pop("_t", None)
    if t == "DeviceState":
        return DeviceState(**d)
    if t == "Event":
        return Event(**d)
    if t == "DeviceInfo":
        return DeviceInfo(**{**d, "roles": tuple(d.get("roles", ()))})
    if t == "Broadcast":
        return Broadcast(**d)
    if t == "Left":
        return ("left", d["device_id"])
    return None


# =============================================================== interface ==
BroadcastHandler = Callable[[Broadcast], Awaitable[None]]


class EngineLinkServer:
    """Server side of the boundary."""

    def __init__(self):
        self._on_broadcast: BroadcastHandler | None = None
        self._roster: dict[str, DeviceInfo] = {}   # replayed to a restarting engine

    def set_broadcast_handler(self, cb: BroadcastHandler) -> None:
        self._on_broadcast = cb

    async def start(self) -> None: ...
    async def device_joined(self, info: DeviceInfo) -> None:
        self._roster[info.device_id] = info
        await self._send(info)

    async def device_left(self, device_id: str) -> None:
        self._roster.pop(device_id, None)
        await self._send_left(device_id)

    async def sample(self, ds: DeviceState) -> None:
        await self._send(ds)

    async def event(self, ev: Event) -> None:
        await self._send(ev)

    async def _send(self, item) -> None: ...
    async def _send_left(self, device_id: str) -> None: ...


# ================================================================= in-proc ==
class InProcLink(EngineLinkServer):
    """Engine runs in the same process; items cross two asyncio queues."""

    def __init__(self):
        super().__init__()
        self.to_engine: asyncio.Queue = asyncio.Queue(maxsize=512)
        self.from_engine: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._pump: asyncio.Task | None = None

    async def start(self) -> None:
        self._pump = asyncio.create_task(self._pump_broadcasts())

    async def _pump_broadcasts(self) -> None:
        while True:
            b: Broadcast = await self.from_engine.get()
            if self._on_broadcast:
                await self._on_broadcast(b)

    async def _send(self, item) -> None:
        self._put(item)

    async def _send_left(self, device_id: str) -> None:
        self._put(("left", device_id))

    def _put(self, item) -> None:
        try:
            self.to_engine.put_nowait(item)
        except asyncio.QueueFull:
            # Latest-wins under pressure: drop the oldest continuous sample.
            try:
                self.to_engine.get_nowait()
                self.to_engine.put_nowait(item)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass


# ==================================================================== tcp ===
class TcpLink(EngineLinkServer):
    """NDJSON over loopback. The server listens; the engine process connects.
    On engine (re)connect the current roster is replayed so a restarted engine
    knows who is on the pitch — sessions survive the engine dying (criterion 10).
    While no engine is connected, items are dropped: the match cannot advance
    anyway, and continuous samples are stale by definition."""

    def __init__(self, port: int = ENGINE_PORT_DEFAULT):
        super().__init__()
        self.port = port
        self._writer: asyncio.StreamWriter | None = None
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._on_engine, "127.0.0.1", self.port)

    async def _on_engine(self, reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter) -> None:
        if self._writer is not None:
            writer.close()
            return
        self._writer = writer
        print(f"[link] engine connected from {writer.get_extra_info('peername')}")
        for info in self._roster.values():   # replay roster to the fresh engine
            await self._write(_to_wire(info))
        try:
            while line := await reader.readline():
                try:
                    item = _from_wire(json.loads(line))
                except (json.JSONDecodeError, TypeError, KeyError):
                    continue
                if isinstance(item, Broadcast) and self._on_broadcast:
                    await self._on_broadcast(item)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            print("[link] engine disconnected — sessions held, waiting for restart")
            self._writer = None
            writer.close()

    async def _send(self, item) -> None:
        await self._write(_to_wire(item))

    async def _send_left(self, device_id: str) -> None:
        await self._write({"_t": "Left", "device_id": device_id})

    async def _write(self, obj: dict) -> None:
        if self._writer is None:
            return
        try:
            self._writer.write(json.dumps(obj).encode() + b"\n")
            await self._writer.drain()
        except (ConnectionError, RuntimeError):
            self._writer = None
