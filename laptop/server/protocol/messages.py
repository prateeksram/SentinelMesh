"""Message and channel constants shared by server, simulator, and (later) firmware.

The WebSocket path speaks JSON objects with a "type" key; the datagram path
(discovery today, data plane later) uses the numeric msg_type in the 20-byte
header. Both enumerations live here so they can never drift apart.

NOTE — dev vs target: this code is written and tested on an x86-64 dev machine
but deploys to the Snapdragon X Elite laptop (ARM64). Everything in server/ and
engine/ is deliberately pure Python (stdlib + aiohttp) so it runs unchanged on
both; nothing here may depend on x64-only wheels.
"""

from __future__ import annotations


class Msg:
    HELLO = 1
    WELCOME = 2
    DISCOVER = 3
    ANNOUNCE = 4
    EVENT = 5
    ACK = 6
    TELEM = 7
    STATE = 8
    HEARTBEAT = 9


class Channel:
    CONTROL = 0
    STREAM = 1      # continuous, latest-wins
    EVENT = 2       # discrete, ACK + dedup
    BULK = 3        # skel-class payloads (HTTP today)


# JSON "type" values accepted over WebSocket, and how each is classified.
# Continuous streams are fire-and-forget latest-wins; discrete events must
# reach the engine exactly once (dedup by event_id when one is present).
WS_STREAMS = {"aim"}
WS_EVENTS = {"kick", "skel", "start", "again", "abort", "event"}
WS_CONTROL = {"hello", "hb", "telem"}

# Legacy roles ("client" field) accepted without a capability descriptor.
LEGACY_ROLES = {"phone", "tv"}

# Roles that receive game-state broadcasts. Dashboards get telemetry only.
GAME_ROLES = {"phone", "tv", "display", "keeper_input"}
