"""Telemetry aggregation for the QPlay hub.

Self-reported duty cycle is the primary signal: each workload measures its own
busy time and reports busy_pct per unit. `metric` is opaque — stored and
forwarded without interpretation, so a new unit type never requires a server
change.

Units can have multiple sources (the laptop NPU is claimed by both the GenieX
desk and the FX depth session). Reports carry an optional "source" key; the
snapshot merges live sources per unit: busy = max, metrics merged, state lines
joined, freshest age wins. A unit is stale only when ALL its sources are stale.
"""

from __future__ import annotations

import os
import time

STALE_MS = 3000  # dashboard renders grey after this


class TelemetryStore:
    def __init__(self):
        # devices[key] = {"device": kind, "label": str,
        #                 "units": {unit: {source: {"report": dict, "ts": float}}}}
        self._devices: dict[str, dict] = {}
        self.msg_count = 0            # incremented by the hub per inbound message
        self._last_times = os.times()
        self._last_wall = time.monotonic()
        self._last_msgs = 0

    # -------------------------------------------------------------- ingest --
    def ingest(self, device_key: str, device_kind: str, label: str, msg: dict) -> None:
        unit = msg.get("unit")
        if not isinstance(unit, str):
            return
        source = msg.get("source") if isinstance(msg.get("source"), str) else "default"
        entry = self._devices.setdefault(
            device_key, {"device": device_kind, "label": label, "units": {}})
        entry["device"] = device_kind
        entry["label"] = label
        entry["units"].setdefault(unit, {})[source] = {
            "report": {
                "busy_pct": msg.get("busy_pct", 0),
                "metric": msg.get("metric") if isinstance(msg.get("metric"), dict) else {},
                "temp_c": msg.get("temp_c"),
                "state": msg.get("state", ""),
            },
            "ts": time.monotonic(),
        }

    def drop(self, device_key: str) -> None:
        self._devices.pop(device_key, None)

    # -------------------------------------------------- server self-report --
    def self_report(self, extra_metric: dict | None = None) -> None:
        """Real numbers for laptop.cpu (process CPU via os.times)."""
        now_t, now_w = os.times(), time.monotonic()
        cpu_s = (now_t.user + now_t.system) - (self._last_times.user + self._last_times.system)
        wall = max(1e-6, now_w - self._last_wall)
        busy = max(0.0, min(100.0, 100.0 * cpu_s / wall))
        msgs = self.msg_count - self._last_msgs
        self._last_times, self._last_wall, self._last_msgs = now_t, now_w, self.msg_count

        metric = {"msg_rate": round(msgs / wall, 1)}
        if extra_metric:
            metric.update(extra_metric)
        self.ingest("laptop", "laptop", "laptop", {
            "unit": "cpu", "source": "server", "busy_pct": round(busy, 1),
            "metric": metric, "temp_c": None, "state": "asyncio server"})

    # ------------------------------------------------------------ snapshot --
    def snapshot(self) -> dict:
        now = time.monotonic()
        devices = {}
        for key, entry in self._devices.items():
            units = {}
            for unit, sources in entry["units"].items():
                busy = 0.0
                metric: dict = {}
                temp = None
                states: list[str] = []
                best_age = None
                for rec in sources.values():
                    rep = rec["report"]
                    age = int((now - rec["ts"]) * 1000)
                    if age > STALE_MS:
                        continue
                    try:
                        busy = max(busy, float(rep.get("busy_pct") or 0))
                    except (TypeError, ValueError):
                        pass
                    metric.update(rep.get("metric") or {})
                    if temp is None:
                        temp = rep.get("temp_c")
                    if rep.get("state"):
                        states.append(str(rep["state"]))
                    best_age = age if best_age is None else min(best_age, age)
                if best_age is None:
                    freshest = min(sources.values(), key=lambda r: now - r["ts"])
                    rep = freshest["report"]
                    units[unit] = dict(rep, age_ms=int((now - freshest["ts"]) * 1000))
                    continue
                units[unit] = {"busy_pct": round(busy, 1), "metric": metric,
                               "temp_c": temp, "state": " · ".join(states),
                               "age_ms": best_age}
            devices[key] = {"device": entry["device"], "label": entry["label"],
                            "units": units}
        return {"type": "telem_state", "stale_ms": STALE_MS, "devices": devices}
