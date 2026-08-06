"""Discrete-event reliability: server-side dedup (A§5).

Senders retransmit an event until it is ACKed; the server dedups by event_id.
At-least-once delivery plus dedup is effectively-once — the engine must see
each event exactly once. Legacy messages (kick/start/... without an event_id)
pass through untouched; they have no retransmit, so no duplicate risk.
"""

from __future__ import annotations

from collections import OrderedDict

SEEN_CAP = 256  # per session — bounded memory per device (A§15)


class EventGate:
    """Per-session dedup window. Bounded; oldest ids age out first."""

    def __init__(self, cap: int = SEEN_CAP):
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._cap = cap

    def fresh(self, event_id: str) -> bool:
        """True exactly once per event_id. Duplicates return False
        (still ACK them — the sender's retransmit means the first ACK was lost)."""
        if event_id in self._seen:
            self._seen.move_to_end(event_id)
            return False
        self._seen[event_id] = None
        while len(self._seen) > self._cap:
            self._seen.popitem(last=False)
        return True

    def clear(self) -> None:
        self._seen.clear()


def ack(event_id: str) -> dict:
    return {"type": "ack", "event_id": event_id}
