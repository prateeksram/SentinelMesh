"""Capability descriptor parsing and negotiation (A§6, handoff §4).

The server replies with the **intersection** of what the device offers and
what the game currently needs — a device advertising 60 Hz can be told 30.
This is the cheapest load-shedding lever in the system.

Legacy clients (`{"type":"hello","client":"phone"|"tv"}`) get a synthesized
descriptor and, critically, **no WELCOME**: tv.html pipes every WS message into
onState(), so unsolicited message types would corrupt its state. The rule is
"WELCOME iff the hello carried a device_id".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .protocol.messages import LEGACY_ROLES

# What the game currently wants per stream, Hz. Devices offering more are
# downgraded to this; devices offering less keep their own rate.
WANTED_HZ = {
    "aim": 5,       # 200 ms throttle — matches the shipped phone client
    "pose": 30,
    "telem": 1,
}

HEARTBEAT_MS = 2000
PROTO_VERSION = 1


@dataclass
class Caps:
    device_id: str | None = None
    roles: list[str] = field(default_factory=list)
    device: str = "unknown"          # kind: laptop | phone | unoq | browser…
    streams: list[dict] = field(default_factory=list)
    compute: dict = field(default_factory=dict)
    net: dict = field(default_factory=dict)
    proto: int = PROTO_VERSION
    legacy: bool = True              # no device_id → legacy, no WELCOME
    raw: dict = field(default_factory=dict)


def parse(msg: dict) -> Caps | None:
    """Parse a hello into Caps. Returns None if the hello is unusable."""
    device_id = msg.get("device_id")
    roles = msg.get("roles")
    client = msg.get("client")

    if not isinstance(roles, list) or not roles:
        roles = [client] if isinstance(client, str) else []
    roles = [r for r in roles if isinstance(r, str)]

    if device_id is None and not (set(roles) & LEGACY_ROLES) and "dashboard" not in roles:
        return None  # neither a descriptor nor a known legacy role — refuse

    device = msg.get("device") if isinstance(msg.get("device"), str) else None
    if device is None:
        device = {"phone": "phone", "tv": "laptop"}.get(client or "", "unknown")

    return Caps(
        device_id=str(device_id) if device_id is not None else None,
        roles=roles,
        device=device,
        streams=[s for s in msg.get("streams", []) if isinstance(s, dict)],
        compute=msg.get("compute") if isinstance(msg.get("compute"), dict) else {},
        net=msg.get("net") if isinstance(msg.get("net"), dict) else {},
        proto=int(msg.get("proto") or PROTO_VERSION),
        legacy=device_id is None,
        raw=msg,
    )


def negotiate(caps: Caps) -> dict:
    """Intersection of offer and need: per-stream negotiated rates."""
    streams = {}
    for s in caps.streams:
        name = s.get("name")
        if not isinstance(name, str):
            continue
        offered = s.get("rate_hz")
        wanted = WANTED_HZ.get(name)
        if isinstance(offered, (int, float)) and wanted:
            hz = min(int(offered), wanted)
        else:
            hz = wanted or offered or 0
        streams[name] = {"rate_hz": hz}
    return streams


def welcome(session_id: int, caps: Caps, t_rx_us: int, t_tx_us: int) -> dict:
    """WELCOME body. t_rx/t_tx are the Cristian T2/T3 stamps (A§7) — free from
    the handshake; the device already holds T1 and stamps T4 on receipt."""
    return {
        "type": "welcome",
        "session_id": session_id,
        "proto": PROTO_VERSION,
        "heartbeat_ms": HEARTBEAT_MS,
        "negotiated": negotiate(caps),
        "t_rx_us": t_rx_us,
        "t_tx_us": t_tx_us,
    }
