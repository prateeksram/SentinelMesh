#!/usr/bin/env python3
"""
GESTURE FOOTBALL — solo edition (laptop host)
=============================================
One human striker versus THE WALL, an AI goalkeeper.

The phone tracks the player's full body with its camera:
  * hand position  -> aim (L / C / R), streamed live as {"type":"aim"}
  * leg swing      -> the kick, sent once as {"type":"kick","zone","power"}

This file has four jobs:
  * WebSocket hub   — /ws, broadcasts full match state to phone + TV
  * Game engine     — kick-by-kick state machine, referee, scoring
  * AI keeper       — reads your aim (with human-like reaction lag),
                      studies your shot history, and dives
  * AI Desk         — optional LLM commentary (Ollama local or Claude
                      cloud); template lines are the always-on fallback

Run:    python server.py
Phone:  https://<laptop-ip>:8443/phone.html   (camera needs HTTPS off-localhost)
TV:     http://localhost:8080/tv.html
HTTPS on :8443 appears automatically when cert.pem/key.pem sit next to this file.
"""

import asyncio
import json
import os
import random
import ssl
import sys
import time
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

import geniex_client
import neural_fx
import scene_engine

ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"
REPO_ROOT = ROOT.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai100 import report_engine  # noqa: E402
from ai100.web import ReportWeb  # noqa: E402


# ------------------------------------------------- knobs (env-overridable) --
def _f(name, default):
    return float(os.environ.get(name, default))

KICKS         = int(os.environ.get("GF_KICKS", 5))      # kicks per match
SHOOT_WINDOW  = _f("GF_SHOOT_WINDOW", 0)     # seconds to swing; <= 0 waits forever for the kick
KEEPER_REACT  = _f("GF_KEEPER_REACTION", 0.45)  # keeper reads your aim this many s BEFORE the kick — feint inside this window to beat it
KEEPER_IQ     = _f("GF_KEEPER_IQ", 0.75)     # 0 = guesses randomly, 1 = near-psychic
ANNOUNCE_S    = _f("GF_ANNOUNCE_S", 3.5)     # long enough for the spoken keeper line on the TV
COUNTDOWN_S   = _f("GF_COUNTDOWN_S", 3.0)
RESOLVE_S     = _f("GF_RESOLVE_S", 3.8)
POWER_BEAT    = 0.82      # power above this can beat a keeper in the same corner

ZONES = ("L", "C", "R")


# ============================================================== AI DESK =====
class Desk:
    """LLM commentary desk. GenieX (default) > local Ollama > Anthropic cloud
    > templates. Every call is fire-and-forget; the game never waits on it."""

    def __init__(self):
        self.local_url = os.environ.get("GF_LLM_URL")           # e.g. http://localhost:11434/v1/messages
        self.api_key   = os.environ.get("ANTHROPIC_API_KEY")
        # GenieX is default; set GF_GENIEX=0 to fall through to local/cloud.
        self.geniex = os.environ.get("GF_GENIEX", "1").lower() not in ("0", "false", "no")
        if self.geniex:
            self.mode  = "geniex"
            self.model = geniex_client.GENIEX_MODEL
        elif self.local_url:
            self.mode  = "local"
            self.model = os.environ.get("GF_MODEL", "llama3.2:3b")
        elif self.api_key:
            self.mode  = "cloud"
            self.model = os.environ.get("GF_MODEL", "claude-haiku-4-5-20251001")
        else:
            self.mode  = None
        self._warned = False
        self.recent  = []                                       # anti-repetition

    SYSTEM = ("You are the live commentator of a one-person gesture-controlled "
              "penalty shootout: a human striker (their phone camera tracks their "
              "body — hand aims, leg kicks the air) versus THE WALL, an AI "
              "goalkeeper that studies their patterns. Kick force is measured in "
              "real Newtons by on-device vision (ForcePose); big numbers deserve "
              "respect. Reply with the commentary line only: at most 2 short "
              "punchy sentences, grounded ONLY in the JSON match data supplied. "
              "Never repeat the recent lines given. No emoji, no quotes, no preamble.")

    async def line(self, kind: str, ctx: dict) -> str | None:
        if not self.mode:
            return None
        prompts = {
            "read": "Before the kick: one line on what THE WALL expects from the striker. Data:\n",
            "kick": "A kick was just taken. Commentate.\n",
            "end":  "The shootout is over. Deliver the verdict on the human.\n",
        }
        ctx = dict(ctx, recent_lines=self.recent[-3:])
        if self.mode == "geniex":
            try:
                text = await geniex_client.chat(
                    self.SYSTEM, prompts[kind] + json.dumps(ctx), max_tokens=110, timeout=20.0
                )
                if text:
                    self.recent = (self.recent + [text])[-6:]
                    return text
            except Exception as e:
                if not self._warned:
                    print(f"[desk] geniex unreachable ({e}) — template commentary continues")
                    self._warned = True
            return None
        body = {
            "model": self.model,
            "max_tokens": 110,
            "system": self.SYSTEM,
            "messages": [{"role": "user", "content": prompts[kind] + json.dumps(ctx)}],
        }
        url = self.local_url if self.mode == "local" else "https://api.anthropic.com/v1/messages"
        headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
        if self.mode == "cloud":
            headers["x-api-key"] = self.api_key
        try:
            async with ClientSession(timeout=ClientTimeout(total=8)) as s:
                async with s.post(url, json=body, headers=headers) as r:
                    if r.status != 200:
                        raise RuntimeError(f"HTTP {r.status}")
                    data = await r.json()
            text = " ".join(b.get("text", "") for b in data.get("content", [])
                            if b.get("type") == "text").strip()
            if text:
                self.recent.append(text)
                self.recent = self.recent[-6:]
                return text
        except Exception as e:
            if not self._warned:
                print(f"[desk] {self.mode} desk unreachable ({e}) — template commentary continues")
                self._warned = True
        return None


# ============================================================ TEMPLATES =====
ZW = {"L": "the left corner", "C": "straight down the middle", "R": "the right corner"}

T = {
    "read": [
        "THE WALL runs the tape. It's leaning {z}.",
        "The pattern says {z} — but you know it knows.",
        "THE WALL crouches. Its money is on {z}.",
        "Machine read: {z} is live. Prove it wrong.",
    ],
    "goal": [
        "GOAL! Buried in {z} — THE WALL never moved.",
        "You picked {z} and the net ripples. Emphatic.",
        "THE WALL guessed wrong. Goal in {z}.",
    ],
    "goal_beaten": [
        "THE WALL was THERE — but you hit it too hard. Goal in {z}!",
        "Right corner, wrong outcome for the machine. Pure power!",
    ],
    "save": [
        "SAVED! THE WALL read you like a book.",
        "Huge stop! You went {z} and found a glove.",
        "Denied! The machine saw that one coming.",
    ],
    "post": [
        "OFF THE POST! Millimetres from glory.",
        "The woodwork rescues THE WALL — unbelievable.",
    ],
    "over": [
        "Skied it! No swing in time — the window slammed shut.",
        "Frozen at the spot. THE WALL didn't even need to dive.",
    ],
    "end": {
        5: ["PERFECT. Five from five — THE WALL is in pieces."],
        4: ["Four out of five. The machine got one look, that's all."],
        3: ["Three of five. Honours roughly even — rematch?"],
        2: ["Two goals. THE WALL is learning your game."],
        1: ["One solitary goal. The machine owned the night."],
        0: ["Shut out. THE WALL saw everything coming."],
    },
}


def tline(key, **kw):
    return random.choice(T[key]).format(**kw)


# ================================================================ GAME ======
class Game:
    def __init__(self, desk: Desk):
        self.desk = desk
        self.sockets: dict[web.WebSocketResponse, str] = {}   # ws -> client id
        self.task: asyncio.Task | None = None
        self.match_gen = 0                      # bumped on abort so a dying match can't clobber lobby
        # Campaign state survives rematch (cleared only on full _reset / abort).
        self.campaign_level = 1
        self.scene = None
        self.report = ""
        self.gen_progress = 0
        self.scene_metrics = None
        self.report_card = None
        self.report_task: asyncio.Task | None = None
        self._reset()

    # ------------------------------------------------------------ state ----
    def _reset_match(self):
        """Per-match fields only — keeps campaign_level / scene / k_* knobs."""
        self.phase = "lobby"
        self.kick = 0
        self.score = 0                          # striker goals
        self.saves = 0                          # keeper saves (post/over count too)
        self.shotmap = []                       # per-kick results for the TV dot map
        self.aim = "C"                          # live aim from the hand
        self.aim_trail = []                     # (monotonic t, zone) during a kick
        self.replay = None                      # bullet-time skeleton for the current kick
        self.report_card = None
        self.last = None
        self.line = "Waiting for the striker…"
        self.timer_end = 0.0
        self.kick_msg = None
        self.kick_evt = asyncio.Event()
        self.key = 0                            # kick key — guards stale desk lines

    def _reset(self):
        """Full reset — lobby from cold / abort. Wipes campaign."""
        self._reset_match()
        self.campaign_level = 1
        self.scene = None
        self.report = ""
        self.gen_progress = 0
        self.scene_metrics = None
        self.k_iq = KEEPER_IQ
        self.k_react = KEEPER_REACT
        self.k_window = SHOOT_WINDOW
        self.k_beat = POWER_BEAT

    def _alive(self, gen: int) -> bool:
        return gen == self.match_gen

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
            "connected": {"phone": "phone" in self.sockets.values()},
            "level": self.campaign_level,
            "scene": self.scene,
            "report": self.report,
            "genProgress": self.gen_progress,
            "sceneMetrics": self.scene_metrics,
            "postGameReport": self.report_card,
        }

    async def broadcast(self):
        msg = json.dumps(self.snapshot())
        for ws in list(self.sockets):
            try:
                await ws.send_str(msg)
            except Exception:
                pass

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

        asyncio.get_event_loop().create_task(run())

    # --------------------------------------------------------- AI keeper ---
    def predict(self):
        """Most frequent corner in the striker's history (recent kicks weigh more)."""
        if not self.shotmap:
            return random.choice(ZONES)
        freq = {z: 0.0 for z in ZONES}
        for i, s in enumerate(self.shotmap):
            if s["zone"]:
                freq[s["zone"]] += 1.0 + i * 0.25
        return max(freq, key=freq.get)

    def keeper_iq(self):
        """Rubber-band: ease off a struggling human, punish a perfect one."""
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
        """Dive decision. The keeper 'watched' your hand but reacts late:
        it sees the aim as it was k_react seconds before the kick,
        so a last-moment feint sends it the wrong way."""
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
            return seen                          # trusted the hand it saw
        if r < 0.85 * iq:
            return self.predict()                # trusted the pattern
        return random.choice(ZONES)              # guessed

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
            p_goal = 0.10 + max(0.0, power - self.k_beat) * 2.5   # power can beat the glove
        else:
            p_goal = 0.90
        p_goal = min(0.98, max(0.02, p_goal))
        if random.random() < p_goal:
            return "goal"
        # remainder splits between glove and woodwork
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
                self.kick_evt = asyncio.Event()  # fresh event (abort may have set the old one)
                self.aim_trail = []
                self.replay = None
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
                    kz = self.keeper_pick(kick_t)
                    result = self.referee(sz, power, kz)
                else:
                    sz, power, kz, result = None, 0.0, random.choice(ZONES), "over"
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
                self.shotmap.append(entry)
                self.last = self.shotmap[-1]

                tkey = result
                if result == "goal" and sz == kz:
                    tkey = "goal_beaten"
                self.line = tline(tkey, z=ZW.get(sz, "the middle"))
                self.phase = "resolve"
                await self.broadcast()
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
        # 1) end line as today
        self.line = random.choice(T["end"].get(self.score, T["end"][3]))
        # 2) generating phase — SceneEngine owns the NPU; desk verdict waits until after
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
        self.line = scene["copy"].get("lobbyLine") or self.line
        # 5) end — desk verdict can upgrade the line now that scene gen is done
        self.queue_report(self.shotmap, KICKS, generation=gen)
        self.phase = "end"
        await self.broadcast()
        self.ask_desk("end", self.ctx(), {"end"})

    # ------------------------------------------------------------ inbound --
    def queue_report(
        self,
        shots,
        kicks_total,
        *,
        generation=None,
        player_name="THE STRIKER",
        require_end=True,
    ):
        """Start report creation without holding up the full-time screen."""
        if self.report_task and not self.report_task.done():
            self.report_task.cancel()
        generation = self.match_gen if generation is None else generation
        self.report_card = {
            "status": "generating",
            "startedAt": int(time.time()),
            "message": "AI100 is designing your scouting report",
        }

        async def run():
            try:
                card = await report_store.create(list(shots), int(kicks_total), player_name)
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

        self.report_task = asyncio.get_event_loop().create_task(run())

    async def on_message(self, ws, msg):
        t = msg.get("type")
        if t == "hello":
            client = msg.get("client")
            if client in ("phone", "tv"):
                self.sockets[ws] = client
            await self.broadcast()
        elif t == "aim":
            z = msg.get("zone")
            if self.sockets.get(ws) == "phone" and z in ZONES:
                self.aim = z
                if self.phase in ("announce", "countdown", "shoot"):
                    self.aim_trail.append((time.monotonic(), z))
                    await self.broadcast()
        elif t == "kick":
            if (self.sockets.get(ws) == "phone" and self.phase == "shoot"
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
                self.kick_msg = {"zone": msg["zone"],
                                 "power": min(1.0, max(0.0, float(msg.get("power", 0.5)))),
                                 "force": max(0, int(msg.get("force") or 0)),   # Newtons, from ForcePose
                                 "dirDeg": int(msg.get("dirDeg") or 0),
                                 "height": height,
                                 "spin": spin,
                                 "strike": strike,
                                 "foot": foot,
                                 "t": time.monotonic()}
                self.kick_evt.set()
        elif t == "skel":
            # bullet-time skeleton frames arriving ~0.45 s after the kick
            frames = msg.get("frames")
            if (self.sockets.get(ws) == "phone" and isinstance(frames, list)
                    and msg.get("kick") == self.kick
                    and len(json.dumps(frames)) < 200_000):
                self.replay = {"kick": self.kick, "frames": frames[:40]}
                await self.broadcast()
        elif t == "start":
            if (self.phase == "lobby" and "phone" in self.sockets.values()
                    and not (self.task and not self.task.done())):
                self.line = "Here we go!"
                self.task = asyncio.get_event_loop().create_task(self.run_match())
        elif t == "again":
            if self.phase == "end":
                self.match_gen += 1
                if self.task:
                    self.task.cancel()
                if self.report_task and not self.report_task.done():
                    self.report_task.cancel()
                self.desk.recent.clear()
                self._reset_match()          # keep campaign_level, scene, k_* knobs
                await self.broadcast()
        elif t == "abort":
            # End / restart from lobby mid-match (or from full time).
            # Do NOT await the match task here — that deadlocks the WS handler
            # when the match is mid-broadcast to this same socket.
            if self.phase != "lobby":
                self.match_gen += 1
                if self.task and not self.task.done():
                    self.task.cancel()
                if self.report_task and not self.report_task.done():
                    self.report_task.cancel()
                # Unblock shoot-phase wait so the cancelled task can exit.
                self.kick_evt.set()
                self.desk.recent.clear()
                self._reset()
                self.line = "Match aborted — waiting for the striker…"
                await self.broadcast()

    def on_close(self, ws):
        self.sockets.pop(ws, None)


# ================================================================ HTTP ======
report_store = report_engine.ReportStore()
game = Game(Desk())
report_web = ReportWeb(game, report_store, KICKS)


async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    await game.on_message(ws, json.loads(msg.data))
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
    finally:
        game.on_close(ws)
        await game.broadcast()
    return ws


async def fx_status(_request):
    return web.json_response(neural_fx.status())


async def fx_hero(request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return web.json_response(neural_fx.hero(payload))


async def scene_status(_request):
    return web.json_response({
        "level": game.campaign_level,
        "progress": game.gen_progress,
        "metrics": game.scene_metrics,
        "phase": game.phase,
    })


async def hw_status(_request):
    return web.json_response({
        "desk": game.desk.mode,
        "fx": neural_fx.status(),
        "geniex_url": geniex_client.GENIEX_URL,
        "model": geniex_client.GENIEX_MODEL,
        "ai100_report": {
            "configured": report_store.artwork.settings.configured,
            "model": report_store.artwork.settings.model,
        },
    })


def make_app():
    app = web.Application(client_max_size=8 * 1024 * 1024)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/fx/status", fx_status)
    app.router.add_post("/fx/hero", fx_hero)
    app.router.add_get("/scene/status", scene_status)
    app.router.add_get("/hw/status", hw_status)
    report_web.register(app)
    app.router.add_get("/", lambda r: web.HTTPFound("/tv.html"))
    app.router.add_static("/", PUBLIC, show_index=True)
    return app


async def main():
    app = make_app()
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8080).start()
    print("HTTP  :  http://0.0.0.0:8080   (tv.html · phone.html)")

    cert, key = ROOT / "cert.pem", ROOT / "key.pem"
    if cert.exists() and key.exists():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        await web.TCPSite(runner, "0.0.0.0", 8443, ssl_context=ctx).start()
        print("HTTPS :  https://0.0.0.0:8443  (use this for the phone camera)")
    else:
        print("No cert.pem/key.pem — HTTPS off. Phone cameras off-localhost need it:")
        print('  openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 -subj "/CN=gesture-football"')

    desk = game.desk
    if desk.mode == "geniex":
        desk_label = f"GenieX · {desk.model}"
    elif desk.mode == "local":
        desk_label = f"LOCAL AI DESK · {desk.model}"
    elif desk.mode == "cloud":
        desk_label = f"CLAUDE DESK · {desk.model}"
    else:
        desk_label = "templates only"
    print(f"Desk  = {desk_label}")
    fx = neural_fx.status()
    fx_model = fx.get("model") or "procedural"
    print(f"FX    = {fx.get('backend', '?').upper()} · {fx_model}")
    scene_ok = await geniex_client.ping()
    print(f"Scene = {'ready' if scene_ok else 'template (GenieX down)'}")
    print("Profile = QUAD on bench")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
