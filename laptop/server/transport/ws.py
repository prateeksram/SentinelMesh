"""WebSocket adapter — the only module in the server allowed to import aiohttp.

Wire behavior preserved from the legacy server.py ws_handler:
- TEXT frames only; binary silently dropped
- parse failures swallowed (plus TypeError, which the old code let escape)
- heartbeat=20 aiohttp ping keepalive
- on close: hub.on_disconnect fires so peers see the LED drop
"""

from __future__ import annotations

import json

from aiohttp import WSMsgType, web

from .base import Connection, Hub


class WsConnection(Connection):
    def __init__(self, ws: web.WebSocketResponse, remote: str):
        self._ws = ws
        self._remote = remote

    async def send_json(self, obj: dict) -> None:
        try:
            await self._ws.send_str(json.dumps(obj))
        except Exception:
            pass  # dead socket — lifecycle reaper cleans up

    async def close(self) -> None:
        try:
            await self._ws.close()
        except Exception:
            pass

    @property
    def remote(self) -> str:
        return self._remote


def make_ws_handler(hub: Hub):
    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        conn = WsConnection(ws, request.remote or "?")
        await hub.on_connect(conn)
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                    if isinstance(data, dict):
                        await hub.on_message(conn, data)
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    pass
        finally:
            await hub.on_disconnect(conn)
        return ws

    return ws_handler
