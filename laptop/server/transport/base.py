"""Transport abstraction (handoff §4).

The server core must not import aiohttp types outside the WS adapter — if it
does, the UDP work later becomes a rewrite. The core sees only these two
interfaces; ws.py is the sole module allowed to touch aiohttp.
"""

from __future__ import annotations

import abc
from typing import Any, Awaitable, Callable


class Connection(abc.ABC):
    """One connected device, whatever the wire underneath."""

    @abc.abstractmethod
    async def send_json(self, obj: dict) -> None: ...

    @abc.abstractmethod
    async def close(self) -> None: ...

    @property
    @abc.abstractmethod
    def remote(self) -> str:
        """Peer address, for logs only."""


class Hub(abc.ABC):
    """What a transport calls into. Implemented by the server core."""

    @abc.abstractmethod
    async def on_connect(self, conn: Connection) -> None: ...

    @abc.abstractmethod
    async def on_message(self, conn: Connection, msg: dict) -> None: ...

    @abc.abstractmethod
    async def on_disconnect(self, conn: Connection) -> None: ...


class Transport(abc.ABC):
    @abc.abstractmethod
    async def start(self, hub: Hub) -> None: ...

    @abc.abstractmethod
    async def stop(self) -> None: ...


HandlerFactory = Callable[[Hub], Callable[..., Awaitable[Any]]]
