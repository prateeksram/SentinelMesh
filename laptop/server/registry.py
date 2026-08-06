"""Device registry and session lifecycle (A§6.3, handoff §4).

    REGISTERING -> ACTIVE -> DEGRADED -> LOST -> REAPED
                      ^          |
                      +----------+

- DEGRADED holds the session and player slot through a grace window: a brief
  network hiccup must never eject a player mid-match.
- Reconnecting with the same device_id resumes the same session from DEGRADED
  (or LOST, before it is reaped).
- HELLO is idempotent: a retry after a lost WELCOME returns the existing
  session_id, never a new one.
- Legacy sessions (no device_id) cannot be resumed — they die with the socket,
  exactly as the old server behaved.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Iterable

from .capabilities import Caps
from .protocol.events import EventGate
from .protocol.messages import GAME_ROLES
from .transport.base import Connection

# Lifecycle timing (seconds)
DEGRADED_AFTER = 6.0     # 3 missed 2 s heartbeats
LOST_AFTER = 15.0        # DEGRADED grace window before the slot is released
REAP_AFTER = 60.0        # LOST sessions are forgotten after this

REGISTERING, ACTIVE, DEGRADED, LOST, REAPED = (
    "REGISTERING", "ACTIVE", "DEGRADED", "LOST", "REAPED")


@dataclass
class Session:
    session_id: int
    caps: Caps
    conn: Connection | None
    state: str = REGISTERING
    last_seen: float = field(default_factory=time.monotonic)
    state_since: float = field(default_factory=time.monotonic)
    events: EventGate = field(default_factory=EventGate)
    last_state_sent: str | None = None   # per-session dup-payload suppression

    @property
    def device_id(self) -> str | None:
        return self.caps.device_id

    @property
    def roles(self) -> set[str]:
        return set(self.caps.roles)

    @property
    def is_game_client(self) -> bool:
        return bool(self.roles & GAME_ROLES)

    def touch(self) -> None:
        self.last_seen = time.monotonic()

    def _set_state(self, s: str) -> None:
        if self.state != s:
            self.state = s
            self.state_since = time.monotonic()


class Registry:
    """Sessions by id; resumable sessions also indexed by device_id.
    Bounded per-session memory; device count scales linearly (A§15)."""

    def __init__(self):
        self._by_id: dict[int, Session] = {}
        self._by_device: dict[str, Session] = {}
        self._by_conn: dict[Connection, Session] = {}

    # ------------------------------------------------------------ register --
    def register(self, conn: Connection, caps: Caps) -> tuple[Session, bool]:
        """Returns (session, resumed). Idempotent on device_id: a duplicate
        HELLO — same conn or a fresh one — returns the existing session."""
        if caps.device_id:
            existing = self._by_device.get(caps.device_id)
            if existing and existing.state != REAPED:
                resumed = existing.state in (DEGRADED, LOST)
                if existing.conn is not None and existing.conn is not conn:
                    self._by_conn.pop(existing.conn, None)
                existing.conn = conn
                existing.caps = caps          # device may re-declare capabilities
                existing.touch()
                existing._set_state(ACTIVE)
                self._by_conn[conn] = existing
                return existing, resumed

        session = Session(session_id=self._new_id(), caps=caps, conn=conn)
        session._set_state(ACTIVE)
        self._by_id[session.session_id] = session
        self._by_conn[conn] = session
        if caps.device_id:
            self._by_device[caps.device_id] = session
        return session, False

    def _new_id(self) -> int:
        # Random, never sequential — the session_id doubles as the bearer
        # token on the future datagram path (A§15).
        while True:
            sid = random.getrandbits(32) or 1
            if sid not in self._by_id:
                return sid

    # -------------------------------------------------------------- lookup --
    def by_conn(self, conn: Connection) -> Session | None:
        return self._by_conn.get(conn)

    def sessions(self) -> Iterable[Session]:
        return list(self._by_id.values())

    def active_game_sessions(self) -> list[Session]:
        return [s for s in self._by_id.values()
                if s.state == ACTIVE and s.conn is not None and s.is_game_client]

    def with_role(self, role: str) -> list[Session]:
        role_set = {"tv", "display"} if role in ("tv", "display") else {role}
        return [s for s in self._by_id.values()
                if s.state == ACTIVE and s.conn is not None and (s.roles & role_set)]

    # ---------------------------------------------------------- transitions --
    def disconnect(self, conn: Connection) -> tuple[Session | None, bool]:
        """Socket gone. Returns (session, released) where released means the
        engine should drop the device now (legacy sessions only — resumable
        ones enter the DEGRADED grace window instead)."""
        session = self._by_conn.pop(conn, None)
        if session is None:
            return None, False
        session.conn = None
        if session.caps.legacy:
            session._set_state(REAPED)
            self._by_id.pop(session.session_id, None)
            return session, True
        session._set_state(DEGRADED)
        return session, False

    def tick(self, now: float | None = None) -> list[Session]:
        """Advance lifecycle timers. Returns sessions that just went LOST —
        the caller releases their player slot with the engine."""
        now = now or time.monotonic()
        newly_lost: list[Session] = []
        for s in list(self._by_id.values()):
            # Missed-heartbeat rule applies only to sessions that declared one
            # (extended hello). Legacy clients send nothing after hello — the
            # aiohttp ping keepalive plus on_disconnect covers their liveness.
            if (s.state == ACTIVE and not s.caps.legacy
                    and now - s.last_seen > DEGRADED_AFTER):
                s._set_state(DEGRADED)
            if s.state == DEGRADED and now - s.state_since > LOST_AFTER:
                s._set_state(LOST)
                newly_lost.append(s)
            if s.state == LOST and now - s.state_since > REAP_AFTER:
                s._set_state(REAPED)
                self._by_id.pop(s.session_id, None)
                if s.device_id:
                    self._by_device.pop(s.device_id, None)
        return newly_lost
