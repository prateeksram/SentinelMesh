"""The shootout engine — four-pillar edition behind the process boundary.

Ported from the mainline monolith `Game` with the transport removed, plus
the parent branch's device-keeper support. The engine sees devices only
through the link: joined/left roster items, continuous samples (aim), and
discrete events (kick, skel, start, again, abort, dive). It emits full state
snapshots and targeted commands via link.broadcast(); the server filters and
fans out. It never sees a socket, a session id, or a packet (A§9).

Four-pillar behavior preserved:
- campaign: `generating` phase after full time; SceneEngine (GenieX Qwen3-4B)
  designs the next venue + keeper difficulty; level never regresses; rematch
  keeps campaign state, abort wipes it.
- per-scene keeper knobs k_iq / k_react / k_window / k_beat.
- verdict owns the end screen; the scene's lobby line applies on `again` (P0).

Device keeper: a `dive` event from a keeper_input device during
countdown/shoot overrides THE WALL's zone for that kick; the engine sends a
haptic command back to the device on resolve.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import time
from pathlib import Path

import scene_engine
from server.link import DeviceInfo, DeviceState, Event

from .commentary import Desk, T, ZW, tline
from .link import EngineSideLink

# ai100 post-match reports (a71a0af) live at the repo root, one level above
# laptop/. Import is deferred and optional: the game must run without
# pillow/qrcode installed — the report card then reports its own error.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_REPORT_STORE = None
_REPORT_ERR: str | None = None


def _report_store():
    global _REPORT_STORE, _REPORT_ERR
    if _REPORT_STORE is None and _REPORT_ERR is None:
        try:
            from ai100 import report_engine
            _REPORT_STORE = report_engine.ReportStore()
        except Exception as e:  # missing deps → feature off, game unaffected
            _REPORT_ERR = f"{type(e).__name__}: {e}"
    return _REPORT_STORE


# ------------------------------------------------- knobs (env-overridable) --
def _f(name, default):
    return float(os.environ.get(name, default))

KICKS         = int(os.environ.get("GF_KICKS", 5))   # kicks per match
SHOOT_WINDOW  = _f("GF_SHOOT_WINDOW", 0)     # seconds to swing; <= 0 waits forever
KEEPER_REACT  = _f("GF_KEEPER_REACTION", 0.45)  # keeper reads aim this many s BEFORE the kick
KEEPER_IQ     = _f("GF_KEEPER_IQ", 0.75)     # 0 = guesses randomly, 1 = near-psychic
ANNOUNCE_S    = _f("GF_ANNOUNCE_S", 3.5)
COUNTDOWN_S   = _f("GF_COUNTDOWN_S", 3.0)
RESOLVE_S     = _f("GF_RESOLVE_S", 3.8)
POWER_BEAT    = 0.82      # power above this can beat a keeper in the same corner

ZONES = ("L", "C", "R")


class Engine:
    def __init__(self, link: EngineSideLink, desk: Desk | None = None):
        self.link = link
        self.desk = desk or Desk()
        self.roster: dict[str, DeviceInfo] = {}
        self.task: asyncio.Task | None = None
        self.report_task: asyncio.Task | None = None
        self.match_gen = 0            # bumped on abort so a dying match can't clobber lobby
        # Campaign state survives rematch (cleared only on full _reset / abort).
        self.campaign_level = 1
        self.scene = None
        self.report = ""
        self.gen_progress = 0
        self.scene_metrics = None
        self.pending_lobby_line = None
        self._reset()

    # ------------------------------------------------------------ state ----
    def _reset_match(self):
        """Per-match fields only — keeps campaign_level / scene / k_* knobs."""
        self.phase = "lobby"
        self.kick = 0
        self.score = 0
        self.saves = 0
        self.shotmap = []
        self.aim = "C"
        self.aim_trail = []           # (monotonic t, zone) during a kick
        self.replay = None
        self.last = None
        self.line = "Waiting for the striker…"
        self.timer_end = 0.0
        self.kick_msg = None
        self.kick_evt = asyncio.Event()
        self.key = 0                  # guards stale desk lines
        self.dive_zone = None         # device keeper's dive for the current kick
        self.dive_device = None
        self.report_card = None       # ai100 post-match scouting card

    def _reset(self):
        """Full reset — lobby from cold / abort. Wipes campaign."""
        self._reset_match()
        self.campaign_level = 1
        self.scene = None
        self.report = ""
        self.gen_progress = 0
        self.scene_metrics = None
        self.pending_lobby_line = None
        self.k_iq = KEEPER_IQ
        self.k_react = KEEPER_REACT
        self.k_window = SHOOT_WINDOW
        self.k_beat = POWER_BEAT

    def _alive(self, gen: int) -> bool:
        return gen == self.match_gen

    @property
    def phone_present(self) -> bool:
        return any("phone" in d.roles for d in self.roster.values())

    def snapshot(self):
        return {
            "type": "state",
            "phase": self.phase,
            "kick": self.kick,
            "kicksTotal": KICKS,
            "score": self.score,
            "saves": self.saves,
            "shotmap": self.shotmap,
            "aimLive": self.aim,
            "timerMs": max(0, int((self.timer_end - time.monotonic()) * 1000)),
            "last": self.last,
            "replay": self.replay,
            "line": self.line,
            "llm": self.desk.mode,
            "connected": {"phone": self.phone_present},
            "level": self.campaign_level,
            "scene": self.scene,
            "report": self.report,
            "genProgress": self.gen_progress,
            "sceneMetrics": self.scene_metrics,
            "postGameReport": self.report_card,
        }

    async def broadcast(self):
        await self.link.broadcast(self.snapshot(), "all")

    # ------------------------------------------------------- desk upgrade --
    def ask_desk(self, kind, ctx, phases):
        """Fire-and-forget LLM call; applies only if the match hasn't moved on."""
        key = self.key

        async def run():
            text = await self.desk.line(kind, ctx)
            if text and self.key == key and self.phase in phases:
                self.line = text
                if self.last and kind == "kick":
                    self.last["ai"] = True
                await self.broadcast()

        asyncio.create_task(run())

    # --------------------------------------------------------- AI keeper ---
    def predict(self):
        if not self.shotmap:
            return random.choice(ZONES)
        freq = {z: 0.0 for z in ZONES}
        for i, s in enumerate(self.shotmap):
            if s["zone"]:
                freq[s["zone"]] += 1.0 + i * 0.25
        return max(freq, key=freq.get)

    def keeper_iq(self):
        iq = self.k_iq
        taken = len(self.shotmap)
        if taken >= 3:
            rate = self.score / taken
            if rate <= 0.34:
                iq -= 0.20
            elif rate >= 0.80:
                iq += 0.15
        return min(1.0, max(0.0, iq))

    def keeper_pick(self, kick_t):
        """AI dive decision — reads the aim as it was k_react s before the
        kick, so a last-moment feint sends it the wrong way."""
        seen = None
        cutoff = kick_t - self.k_react
        for t, z in reversed(self.aim_trail):
            if t <= cutoff:
                seen = z
                break
        if seen is None and self.aim_trail:
            seen = self.aim_trail[0][1]
        iq = self.keeper_iq()
        r = random.random()
        if seen and r < 0.55 * iq:
            return seen
        if r < 0.85 * iq:
            return self.predict()
        return random.choice(ZONES)

    def ctx(self):
        return {
            "score": self.score, "saves": self.saves,
            "kick": self.kick, "kicks_total": KICKS,
            "striker_history": [{"zone": h["zone"], "result": h["result"],
                                 "force_newtons": h.get("force", 0)}
                                for h in self.shotmap][-5:],
            "keeper_prediction": self.predict(),
            "last_kick": self.last,
        }

    # ------------------------------------------------------------- referee --
    def referee(self, shot_zone, power, keeper_zone):
        if shot_zone == keeper_zone:
            p_goal = 0.10 + max(0.0, power - self.k_beat) * 2.5
        else:
            p_goal = 0.90
        p_goal = min(0.98, max(0.02, p_goal))
        if random.random() < p_goal:
            return "goal"
        return "post" if random.random() < (0.5 if shot_zone != keeper_zone else 0.18) else "save"

    # --------------------------------------------------------- match logic --
    async def timer(self, seconds, phase_broadcast_every=None):
        self.timer_end = time.monotonic() + seconds
        await self.broadcast()
        if phase_broadcast_every:
            end = self.timer_end
            while time.monotonic() < end:
                await asyncio.sleep(min(phase_broadcast_every, max(0.01, end - time.monotonic())))
                if time.monotonic() < end:
                    await self.broadcast()
        else:
            await asyncio.sleep(seconds)

    async def run_match(self):
        gen = self.match_gen
        try:
            for _ in range(KICKS):
                if not self._alive(gen):
                    return
                # ---------------- announce ----------------
                self.key += 1
                self.kick += 1
                self.kick_msg = None
                self.kick_evt = asyncio.Event()
                self.aim_trail = []
                self.replay = None
                self.dive_zone = None
                self.dive_device = None
                if not self._alive(gen):
                    return
                self.phase = "announce"
                pred = self.predict()
                self.line = tline("read", z=ZW[pred].replace("the ", ""))
                await self.broadcast()
                if not self._alive(gen):
                    return
                self.ask_desk("read", self.ctx(), {"announce", "countdown"})
                await asyncio.sleep(ANNOUNCE_S)
                if not self._alive(gen):
                    return

                # ---------------- countdown ---------------
                self.phase = "countdown"
                await self.timer(COUNTDOWN_S, phase_broadcast_every=1.0)
                if not self._alive(gen):
                    return

                # ---------------- shoot -------------------
                self.phase = "shoot"
                if self.k_window > 0:
                    self.timer_end = time.monotonic() + self.k_window
                    await self.broadcast()
                    if not self._alive(gen):
                        return
                    try:
                        await asyncio.wait_for(self.kick_evt.wait(), self.k_window)
                    except asyncio.TimeoutError:
                        pass
                else:
                    # Wait as long as it takes — the kick decides the tempo.
                    self.timer_end = 0.0
                    await self.broadcast()
                    if not self._alive(gen):
                        return
                    await self.kick_evt.wait()
                if not self._alive(gen):
                    return

                # ---------------- resolve -----------------
                if self.kick_msg:
                    sz, power, kick_t = (self.kick_msg["zone"],
                                         self.kick_msg["power"],
                                         self.kick_msg["t"])
                    force, dir_deg = self.kick_msg["force"], self.kick_msg["dirDeg"]
                    height = self.kick_msg.get("height")
                    spin = self.kick_msg.get("spin")
                    strike = self.kick_msg.get("strike")
                    foot = self.kick_msg.get("foot")
                    if self.dive_zone:               # device keeper overrides THE WALL
                        kz, keeper_src = self.dive_zone, "device"
                    else:
                        kz, keeper_src = self.keeper_pick(kick_t), "ai"
                    result = self.referee(sz, power, kz)
                else:
                    sz, power, result = None, 0.0, "over"
                    if self.dive_zone:
                        kz, keeper_src = self.dive_zone, "device"
                    else:
                        kz, keeper_src = random.choice(ZONES), "ai"
                    force, dir_deg = 0, 0
                    height = spin = strike = foot = None

                if not self._alive(gen):
                    return
                if result == "goal":
                    self.score += 1
                else:
                    self.saves += 1
                entry = {"kick": self.kick, "zone": sz,
                         "keeperZone": kz, "power": round(power, 2),
                         "force": force, "dirDeg": dir_deg,
                         "result": result}
                if height in ("H", "L"):
                    entry["height"] = height
                if isinstance(spin, (int, float)):
                    entry["spin"] = round(float(spin), 3)
                if strike in ("chip", "drive"):
                    entry["strike"] = strike
                if foot in ("L", "R"):
                    entry["foot"] = foot
                if keeper_src == "device":
                    entry["keeperSrc"] = "device"
                self.shotmap.append(entry)
                self.last = self.shotmap[-1]

                tkey = result
                if result == "goal" and sz == kz:
                    tkey = "goal_beaten"
                self.line = tline(tkey, z=ZW.get(sz, "the middle"))
                self.phase = "resolve"
                await self.broadcast()
                # Tell the keeper device what happened — haptics + LEDs.
                await self.link.broadcast(
                    {"type": "haptic", "pattern": result, "kick": self.kick,
                     "zone": kz}, "role:keeper_input")
                if not self._alive(gen):
                    return
                self.ask_desk("kick", self.ctx(), {"resolve", "end"})
                await asyncio.sleep(RESOLVE_S)

            if self._alive(gen):
                await self.full_time()
        except asyncio.CancelledError:
            return

    async def full_time(self):
        self.key += 1
        # 1) verdict line — it owns the end screen (P0 fix)
        self.line = random.choice(T["end"].get(self.score, T["end"][3]))
        # 2) generating phase — SceneEngine owns the NPU; desk verdict waits
        self.phase = "generating"
        self.gen_progress = 0
        await self.broadcast()
        gen = self.match_gen
        # 3) pick + generate
        level = scene_engine.pick_next_level(self.score, self.campaign_level)
        ctx = scene_engine.build_context(self.score, self.saves, KICKS, self.shotmap)
        last_bcast = 0.0

        async def progress(p):
            nonlocal last_bcast
            self.gen_progress = p
            now = time.monotonic()
            if p in (5, 100) or now - last_bcast > 0.25:
                last_bcast = now
                if self._alive(gen):
                    await self.broadcast()

        try:
            scene = await scene_engine.generate(ctx, level, progress)
        except asyncio.CancelledError:
            return
        if not self._alive(gen):
            return
        # 4) apply difficulty + scene
        d = scene["difficulty"]
        self.k_iq, self.k_react = d["keeperIq"], d["keeperReaction"]
        self.k_window, self.k_beat = d["shootWindow"], d["powerBeat"]
        self.scene = scene
        self.report = scene.get("report", "")
        self.scene_metrics = scene.get("metrics")
        self.campaign_level = level
        # Scene lobby line applies on the NEXT lobby (`again`) — never over
        # the verdict (P0 fix).
        self.pending_lobby_line = scene["copy"].get("lobbyLine") or None
        # 5) end — desk verdict can upgrade the line now that scene gen is done
        self.queue_report(self.shotmap, KICKS, generation=gen)
        self.phase = "end"
        await self.broadcast()
        self.ask_desk("end", self.ctx(), {"end"})

    def queue_report(
        self,
        shots,
        kicks_total,
        *,
        generation=None,
        player_name="THE STRIKER",
        require_end=True,
    ):
        """Start ai100 report creation without holding up the full-time screen
        (ported from a71a0af's monolith hook)."""
        if self.report_task and not self.report_task.done():
            self.report_task.cancel()
        generation = self.match_gen if generation is None else generation
        store = _report_store()
        if store is None:
            self.report_card = {"status": "error",
                                "message": f"AI100 unavailable: {_REPORT_ERR}"}
            return
        self.report_card = {
            "status": "generating",
            "startedAt": int(time.time()),
            "message": "AI100 is designing your scouting report",
        }

        async def run():
            try:
                card = await store.create(list(shots), int(kicks_total), player_name)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                card = {
                    "status": "error",
                    "message": f"Report generation failed: {str(exc)[:140]}",
                }
            if not self._alive(generation):
                return
            if require_end and self.phase != "end":
                return
            self.report_card = card
            await self.broadcast()

        self.report_task = asyncio.create_task(run())

    # ------------------------------------------------------------ inbound --
    async def handle(self, item) -> None:
        if isinstance(item, DeviceInfo):
            self.roster[item.device_id] = item
            await self.broadcast()                    # LED update, as before
        elif isinstance(item, tuple) and item and item[0] == "left":
            self.roster.pop(item[1], None)
            await self.broadcast()
        elif isinstance(item, DeviceState):
            await self._on_sample(item)
        elif isinstance(item, Event):
            await self._on_event(item)

    async def _on_sample(self, ds: DeviceState) -> None:
        if ds.stream == "aim" and ds.role == "phone":
            z = ds.data.get("zone")
            if z in ZONES:
                self.aim = z
                if self.phase in ("announce", "countdown", "shoot"):
                    self.aim_trail.append((time.monotonic(), z))
                    await self.broadcast()

    async def _on_event(self, ev: Event) -> None:
        import json as _json
        t, msg = ev.kind, ev.data
        if t == "kick":
            if (ev.role == "phone" and self.phase == "shoot"
                    and not self.kick_msg and msg.get("zone") in ZONES):
                height = msg.get("height")
                if height not in ("H", "L"):
                    height = None
                try:
                    spin = float(msg["spin"]) if msg.get("spin") is not None else None
                    if spin is not None:
                        spin = max(-1.0, min(1.0, spin))
                except (TypeError, ValueError):
                    spin = None
                strike = msg.get("strike")
                if strike not in ("chip", "drive"):
                    strike = None
                foot = msg.get("foot")
                if foot not in ("L", "R"):
                    foot = None
                try:
                    self.kick_msg = {"zone": msg["zone"],
                                     "power": min(1.0, max(0.0, float(msg.get("power", 0.5)))),
                                     "force": max(0, int(msg.get("force") or 0)),
                                     "dirDeg": int(msg.get("dirDeg") or 0),
                                     "height": height,
                                     "spin": spin,
                                     "strike": strike,
                                     "foot": foot,
                                     "t": time.monotonic()}
                except (TypeError, ValueError, KeyError):
                    return
                self.kick_evt.set()
        elif t == "skel":
            frames = msg.get("frames")
            if (ev.role == "phone" and isinstance(frames, list)
                    and msg.get("kick") == self.kick
                    and len(_json.dumps(frames)) < 200_000):
                self.replay = {"kick": self.kick, "frames": frames[:40]}
                await self.broadcast()
        elif t == "dive":
            z = msg.get("zone")
            if (ev.role == "keeper_input" and z in ZONES
                    and self.phase in ("countdown", "shoot")):
                self.dive_zone = z
                self.dive_device = ev.device_id
        elif t == "start":
            if (self.phase == "lobby" and self.phone_present
                    and not (self.task and not self.task.done())):
                self.line = "Here we go!"
                self.task = asyncio.create_task(self.run_match())
        elif t == "report_sim":
            # ai100 simulate endpoint, forwarded by the server as an event —
            # the server never builds a report itself.
            shots = msg.get("shots")
            if not isinstance(shots, list) or not shots:
                try:
                    from ai100 import report_engine
                    shots = report_engine.sample_shotmap()
                except Exception:
                    shots = []
            self.queue_report(
                shots, int(msg.get("kicks_total") or len(shots) or KICKS),
                player_name=str(msg.get("player_name") or "DEMO STRIKER")[:28],
                require_end=False)
            await self.broadcast()
        elif t == "again":
            if self.phase == "end":
                if self.task:
                    self.task.cancel()
                if self.report_task and not self.report_task.done():
                    self.report_task.cancel()
                self.desk.recent.clear()
                self._reset_match()          # keep campaign_level, scene, k_* knobs
                if self.pending_lobby_line:  # next venue's flavor line — the
                    self.line = self.pending_lobby_line  # verdict had its screen
                await self.broadcast()
        elif t == "abort":
            # Do NOT await the match task here — it may be mid-broadcast.
            if self.phase != "lobby":
                self.match_gen += 1
                if self.task and not self.task.done():
                    self.task.cancel()
                if self.report_task and not self.report_task.done():
                    self.report_task.cancel()
                self.kick_evt.set()   # unblock shoot-phase wait so the task exits
                self.desk.recent.clear()
                self._reset()
                self.line = "Match aborted — waiting for the striker…"
                await self.broadcast()

    # -------------------------------------------------------- telemetry ----
    async def _telem_loop(self) -> None:
        """1 Hz engine-side telemetry: GenieX desk + SceneEngine numbers.
        Placement caveat (handoff-2 P2): a URL cannot prove Hexagon — the LLM
        cell reports placement "unverified" until a [device] check confirms
        it. Do not fake a green NPU cell."""
        import geniex_client
        while True:
            await asyncio.sleep(1.0)
            if self.desk.mode != "geniex":
                continue
            metric = {"placement": "unverified",
                      "breaker": geniex_client.breaker_state()["state"]}
            dm = self.desk.last_meta or {}
            if dm.get("tok_per_s"):
                metric["tok_per_s"] = dm["tok_per_s"]
            if dm.get("total_ms"):
                metric["last_ms"] = dm["total_ms"]
            sm = self.scene_metrics or {}
            if sm.get("tok_per_s"):
                metric["scene_tok_per_s"] = sm["tok_per_s"]
            try:
                await self.link.broadcast({
                    "type": "telem", "unit": "npu", "source": "llm",
                    "busy_pct": 0, "metric": metric,
                    "state": "geniex qwen3-4b (placement unverified)"}, "telem")
            except Exception:
                pass

    # ---------------------------------------------------------------- run --
    async def run(self) -> None:
        """Consume link items forever. One initial broadcast so a freshly
        (re)started engine pushes lobby state to whoever is connected."""
        asyncio.create_task(self._telem_loop())
        await self.broadcast()
        while True:
            item = await self.link.recv()
            try:
                await self.handle(item)
            except Exception as e:                    # engine must not die on bad input
                print(f"[engine] handle error: {e!r}")
