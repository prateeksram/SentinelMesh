"""Per-device snapshot filtering (handoff §4; handoff-2 P1).

The snapshot grew five campaign keys and the phone was still receiving all of
it over LAN. Verified consumer reads:

  tv.html          — everything (scene, report, genProgress, sceneMetrics, replay…)
  phone.html       — phase, kick, kicksTotal, score, timerMs, last, line, genProgress*
  Android app      — phase, kick, kicksTotal, score, line, timerMs, last.*

P1 acceptance: the phone's outbound frames carry no `replay`, no `report`,
no `genProgress` (and `shotmap` stays excluded from the original fix).
*phone.html's generating screen therefore renders its static variant — the
progress % is a TV affordance.

A device can override its default with `"wants": [...]` in its hello.
"""

from __future__ import annotations

from .registry import Session

# Keys excluded from the state snapshot, by role. Everything not listed
# receives the snapshot in full.
DEFAULT_EXCLUDE: dict[str, frozenset[str]] = {
    "phone": frozenset({"replay", "shotmap", "report", "genProgress"}),
    "keeper_input": frozenset({"replay", "shotmap", "report", "genProgress", "scene"}),
}


def filter_state(payload: dict, session: Session) -> dict:
    exclude: set[str] = set()
    for role in session.roles:
        exclude |= DEFAULT_EXCLUDE.get(role, frozenset())
    if not exclude:
        return payload
    wants = session.caps.raw.get("wants")
    if isinstance(wants, list):
        exclude -= {w for w in wants if isinstance(w, str)}
    if not exclude:
        return payload
    return {k: v for k, v in payload.items() if k not in exclude}
