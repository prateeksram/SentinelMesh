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
import math
import os
import random
import ssl
import time
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"


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
EDGE_POSE_PORT = int(os.environ.get("GF_EDGE_POSE_PORT", 9999))
EDGE_FRAME_STALE_S = _f("GF_EDGE_FRAME_STALE_S", 2.0)

ZONES = ("L", "C", "R")

# Latest-only edge-camera state. The UNO Q posts JPEGs independently of pose
# inference, so a slow landmark model never creates an unbounded frame queue.
edge_frame: bytes | None = None
edge_frame_seq = 0
edge_frame_at = 0.0
edge_source_frame: bytes | None = None
edge_source_frame_seq = 0
edge_source_frame_at = 0.0
edge_pose_at = 0.0
edge_pose_seq = -1
edge_pose_capture_ns = -1


# ============================================================== AI DESK =====
class Desk:
    """LLM commentary desk. Local (Ollama, Messages-format) beats cloud
    (Anthropic API) beats nothing. Every call is fire-and-forget; the game
    never waits on it. Falls back silently to templates."""

    def __init__(self):
        self.local_url = os.environ.get("GF_LLM_URL")           # e.g. http://localhost:11434/v1/messages
        self.api_key   = os.environ.get("ANTHROPIC_API_KEY")
        if self.local_url:
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


def _finite(value, low, high, default=None):
    """Return a bounded JSON number or default; bool is never a number here."""
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return min(high, max(low, number))


def normalize_kick_state(raw):
    """Validate the source-neutral state without caring which backend made it."""
    if not isinstance(raw, dict) or raw.get("schema") != "sentinel.kick.state.v1":
        return None
    peak = _finite(raw.get("peakFootSpeedMps"), 0.0, 15.0)
    confidence = _finite(raw.get("confidence"), 0.0, 1.0)
    if peak is None or confidence is None:
        return None
    return {
        "schema": "sentinel.kick.state.v1",
        "source": str(raw.get("source", "UNKNOWN"))[:24],
        "peakFootSpeedMps": peak,
        "lateralVelocityMps": _finite(raw.get("lateralVelocityMps"), -15.0, 15.0, 0.0),
        "upwardVelocityMps": _finite(raw.get("upwardVelocityMps"), -15.0, 15.0, 0.0),
        "pathDisplacementM": _finite(raw.get("pathDisplacementM"), 0.0, 3.0, 0.0),
        "liftM": _finite(raw.get("liftM"), 0.0, 2.0, 0.0),
        "swingDurationMs": int(_finite(raw.get("swingDurationMs"), 0.0, 2500.0, 0.0)),
        "confidence": confidence,
    }


def normalize_trajectory(raw):
    """Bound a compact x/y/z sampled flight path before rebroadcasting it."""
    if not isinstance(raw, dict) or raw.get("schema") != "sentinel.trajectory.v1":
        return None
    confidence = _finite(raw.get("confidence"), 0.0, 1.0)
    flight_time = _finite(raw.get("flightTimeS"), 0.1, 3.0)
    launch_speed = _finite(raw.get("launchSpeedMps"), 1.0, 45.0)
    goal_x = _finite(raw.get("goalX"), -12.0, 12.0)
    goal_z = _finite(raw.get("goalZ"), 0.0, 10.0)
    apex = _finite(raw.get("apexM"), 0.0, 12.0)
    velocity = raw.get("launchVelocity")
    if (
        confidence is None or flight_time is None or launch_speed is None
        or goal_x is None or goal_z is None or apex is None
        or not isinstance(velocity, list) or len(velocity) != 3
    ):
        return None
    launch_velocity = [
        _finite(velocity[0], -45.0, 45.0),
        _finite(velocity[1], 0.0, 45.0),
        _finite(velocity[2], -45.0, 45.0),
    ]
    if any(value is None for value in launch_velocity):
        return None

    raw_points = raw.get("points")
    if not isinstance(raw_points, list):
        return None
    points = []
    last_t = -1.0
    last_y = -1.0
    for raw_point in raw_points[:48]:
        if not isinstance(raw_point, list) or len(raw_point) != 4:
            continue
        point = [
            _finite(raw_point[0], 0.0, 3.0),
            _finite(raw_point[1], -15.0, 15.0),
            _finite(raw_point[2], 0.0, 20.0),
            _finite(raw_point[3], 0.0, 12.0),
        ]
        if any(value is None for value in point):
            continue
        if point[0] <= last_t or point[2] + 0.05 < last_y:
            continue
        points.append(point)
        last_t, last_y = point[0], point[2]
    if len(points) < 2:
        return None
    return {
        "schema": "sentinel.trajectory.v1",
        "model": str(raw.get("model", "unknown"))[:48],
        "confidence": confidence,
        "launchVelocity": launch_velocity,
        "launchSpeedMps": launch_speed,
        "flightTimeS": flight_time,
        "goalX": goal_x,
        "goalZ": goal_z,
        "apexM": apex,
        "points": points,
    }


# ================================================================ GAME ======
class Game:
    def __init__(self, desk: Desk):
        self.desk = desk
        self.sockets: dict[web.WebSocketResponse, str] = {}   # ws -> client id
        self.task: asyncio.Task | None = None
        self.match_gen = 0                      # bumped on abort so a dying match can't clobber lobby
        self._reset()

    # ------------------------------------------------------------ state ----
    def _reset(self):
        self.phase = "lobby"
        self.kick = 0
        self.score = 0                          # striker goals
        self.saves = 0                          # keeper saves (post/over count too)
        self.shotmap = []                       # per-kick results for the TV dot map
        self.aim = "C"                          # live aim from the hand
        self.aim_trail = []                     # (monotonic t, zone) during a kick
        self.replay = None                      # bullet-time skeleton for the current kick
        self.last = None
        self.line = "Waiting for the striker…"
        self.timer_end = 0.0
        self.kick_msg = None
        self.kick_evt = asyncio.Event()
        self.key = 0                            # kick key — guards stale desk lines

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
        }

    async def broadcast(self):
        msg = json.dumps(self.snapshot())
        for ws in list(self.sockets):
            try:
                await ws.send_str(msg)
            except Exception:
                pass

    async def broadcast_edge_pose(self, packet: dict):
        """Forward full MediaPipe landmarks only to the native phone client."""
        msg = json.dumps({"type": "edge_pose", **packet}, separators=(",", ":"))
        for ws, client in list(self.sockets.items()):
            if client != "phone":
                continue
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
        iq = KEEPER_IQ
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
        it sees the aim as it was KEEPER_REACT seconds before the kick,
        so a last-moment feint sends it the wrong way."""
        seen = None
        cutoff = kick_t - KEEPER_REACT
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
            p_goal = 0.10 + max(0.0, power - POWER_BEAT) * 2.5   # power can beat the glove
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
                if SHOOT_WINDOW > 0:
                    self.timer_end = time.monotonic() + SHOOT_WINDOW
                    await self.broadcast()
                    if not self._alive(gen):
                        return
                    try:
                        await asyncio.wait_for(self.kick_evt.wait(), SHOOT_WINDOW)
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
                    kz = self.keeper_pick(kick_t)
                    result = self.referee(sz, power, kz)
                else:
                    sz, power, kz, result = None, 0.0, random.choice(ZONES), "over"
                    force, dir_deg = 0, 0

                if not self._alive(gen):
                    return
                if result == "goal":
                    self.score += 1
                else:
                    self.saves += 1
                shot = {"kick": self.kick, "zone": sz,
                        "keeperZone": kz, "power": round(power, 2),
                        "force": force, "dirDeg": dir_deg,
                        "result": result}
                if self.kick_msg:
                    for key in ("height", "spin", "strike", "foot", "kickState", "trajectory"):
                        if key in self.kick_msg:
                            shot[key] = self.kick_msg[key]
                self.shotmap.append(shot)
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
        self.phase = "end"
        self.line = random.choice(T["end"].get(self.score, T["end"][3]))
        await self.broadcast()
        self.ask_desk("end", self.ctx(), {"end"})

    # ------------------------------------------------------------ inbound --
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
                self.kick_msg = {"zone": msg["zone"],
                                 "power": min(1.0, max(0.0, float(msg.get("power", 0.5)))),
                                 "force": max(0, int(msg.get("force") or 0)),   # Newtons, from ForcePose
                                 "dirDeg": int(msg.get("dirDeg") or 0),
                                 "height": "H" if msg.get("height") == "H" else "L",
                                 "spin": _finite(msg.get("spin"), -1.0, 1.0, 0.0),
                                 "strike": "chip" if msg.get("strike") == "chip" else "drive",
                                 "foot": "L" if msg.get("foot") == "L" else "R",
                                 "t": time.monotonic()}
                kick_state = normalize_kick_state(msg.get("kickState"))
                trajectory = normalize_trajectory(msg.get("trajectory"))
                if kick_state is not None:
                    self.kick_msg["kickState"] = kick_state
                if trajectory is not None:
                    self.kick_msg["trajectory"] = trajectory
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
                if self.task:
                    self.task.cancel()
                self.desk.recent.clear()
                self._reset()
                await self.broadcast()
        elif t == "abort":
            # End / restart from lobby mid-match (or from full time).
            # Do NOT await the match task here — that deadlocks the WS handler
            # when the match is mid-broadcast to this same socket.
            if self.phase != "lobby":
                self.match_gen += 1
                if self.task and not self.task.done():
                    self.task.cancel()
                # Unblock shoot-phase wait so the cancelled task can exit.
                self.kick_evt.set()
                self.desk.recent.clear()
                self._reset()
                self.line = "Match aborted — waiting for the striker…"
                await self.broadcast()

    def on_close(self, ws):
        self.sockets.pop(ws, None)


# ================================================================ HTTP ======
game = Game(Desk())


def normalize_edge_packet(packet: dict) -> dict | None:
    """Validate the UDP boundary before it reaches an Android client."""
    if packet.get("schema") != "sentinel.edge.pose.v1":
        return None
    raw_landmarks = packet.get("landmarks")
    if not isinstance(raw_landmarks, list) or len(raw_landmarks) not in (0, 33):
        return None
    landmarks = []
    for point in raw_landmarks:
        if not isinstance(point, list) or len(point) < 4:
            return None
        try:
            landmarks.append([
                float(point[0]), float(point[1]), float(point[2]),
                max(0.0, min(1.0, float(point[3]))),
            ])
        except (TypeError, ValueError):
            return None
    frame = packet.get("frame") if isinstance(packet.get("frame"), dict) else {}
    diagnostics = (
        packet.get("diagnostics") if isinstance(packet.get("diagnostics"), dict) else {}
    )
    raw_motion = packet.get("motion") if isinstance(packet.get("motion"), dict) else None

    def finite(value, default=0.0):
        try:
            number = float(value)
            return number if math.isfinite(number) else default
        except (TypeError, ValueError):
            return default

    def flow_foot(name):
        if raw_motion is None or not isinstance(raw_motion.get(name), dict):
            return None
        foot = raw_motion[name]
        return {
            "vx": finite(foot.get("vx")),
            "vy": finite(foot.get("vy")),
            "peak_vx": finite(foot.get("peak_vx")),
            "peak_vy": finite(foot.get("peak_vy")),
            "dx": finite(foot.get("dx")),
            "dy": finite(foot.get("dy")),
            "confidence": max(0.0, min(1.0, finite(foot.get("confidence")))),
            "samples": max(0, int(foot.get("samples", 0))),
        }

    motion = None
    if raw_motion is not None:
        motion = {
            "t_ns": max(0, int(raw_motion.get("t_ns", 0))),
            "fps": max(0.0, finite(raw_motion.get("fps"))),
            "left": flow_foot("left"),
            "right": flow_foot("right"),
        }
    normalized = {
        "schema": "sentinel.edge.pose.v1",
        "seq": max(0, int(packet.get("seq", 0))),
        "t_capture_ns": max(0, int(packet.get("t_capture_ns", 0))),
        "frame": {
            "width": max(1, int(frame.get("width", 1))),
            "height": max(1, int(frame.get("height", 1))),
            "rotation": int(frame.get("rotation", 0)) % 360,
            "mirrored": bool(frame.get("mirrored", True)),
        },
        "landmarks": landmarks,
        "diagnostics": {
            "fps": max(0.0, float(diagnostics.get("fps", 0.0))),
            "inference_ms": max(0.0, float(diagnostics.get("inference_ms", 0.0))),
            "backend": str(diagnostics.get("backend", "uno-q"))[:40],
        },
    }
    if motion is not None:
        normalized["motion"] = motion
    return normalized


class EdgePoseProtocol(asyncio.DatagramProtocol):
    def datagram_received(self, data, _addr):
        global edge_pose_at, edge_pose_seq, edge_pose_capture_ns
        try:
            packet = normalize_edge_packet(json.loads(data.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return
        if packet is None:
            return
        now = time.monotonic()
        capture_ns = packet["t_capture_ns"]
        stream_advanced = capture_ns > edge_pose_capture_ns
        restart_after_gap = edge_pose_at > 0.0 and now - edge_pose_at > 2.0
        if not stream_advanced and not restart_after_gap:
            return
        edge_pose_seq = packet["seq"]
        edge_pose_capture_ns = capture_ns
        edge_pose_at = now
        asyncio.get_running_loop().create_task(game.broadcast_edge_pose(packet))


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


async def edge_frame_handler(request):
    global edge_frame, edge_frame_seq, edge_frame_at
    body = await request.read()
    if not body or len(body) > 2_000_000:
        raise web.HTTPBadRequest(text="expected JPEG body up to 2 MB")
    edge_frame = body
    edge_frame_seq += 1
    edge_frame_at = time.monotonic()
    return web.json_response({"ok": True, "seq": edge_frame_seq})


async def edge_source_frame_handler(request):
    """Laptop USB-camera input consumed by the UNO Q over MJPEG."""
    global edge_source_frame, edge_source_frame_seq, edge_source_frame_at
    body = await request.read()
    if not body or len(body) > 2_000_000:
        raise web.HTTPBadRequest(text="expected JPEG body up to 2 MB")
    edge_source_frame = body
    edge_source_frame_seq += 1
    edge_source_frame_at = time.monotonic()
    return web.json_response({"ok": True, "seq": edge_source_frame_seq})


async def edge_frame_jpeg(request):
    if edge_frame is None or time.monotonic() - edge_frame_at > EDGE_FRAME_STALE_S:
        raise web.HTTPServiceUnavailable(text="UNO Q camera waiting")
    try:
        after = int(request.query.get("after", -1))
    except ValueError:
        after = -1
    if after == edge_frame_seq:
        return web.Response(status=204, headers={"Cache-Control": "no-store"})
    return web.Response(
        body=edge_frame,
        content_type="image/jpeg",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "X-Edge-Seq": str(edge_frame_seq),
        },
    )


async def edge_camera_mjpeg(request):
    response = web.StreamResponse(
        headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=frame",
            "Cache-Control": "no-store",
        }
    )
    await response.prepare(request)
    sent = -1
    try:
        while True:
            if (
                edge_frame is not None
                and edge_frame_seq != sent
                and time.monotonic() - edge_frame_at <= EDGE_FRAME_STALE_S
            ):
                frame = edge_frame
                sent = edge_frame_seq
                await response.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(frame)).encode()
                    + b"\r\n\r\n"
                    + frame
                    + b"\r\n"
                )
            await asyncio.sleep(0.03)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    return response


async def edge_source_camera_mjpeg(request):
    response = web.StreamResponse(
        headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=frame",
            "Cache-Control": "no-store",
        }
    )
    await response.prepare(request)
    sent = -1
    try:
        while True:
            if (
                edge_source_frame is not None
                and edge_source_frame_seq != sent
                and time.monotonic() - edge_source_frame_at <= EDGE_FRAME_STALE_S
            ):
                frame = edge_source_frame
                sent = edge_source_frame_seq
                await response.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(frame)).encode()
                    + b"\r\n\r\n"
                    + frame
                    + b"\r\n"
                )
            await asyncio.sleep(0.03)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    return response


async def edge_status(_request):
    now = time.monotonic()
    return web.json_response({
        "sourceCamera": "live" if edge_source_frame_at and now - edge_source_frame_at <= EDGE_FRAME_STALE_S else "waiting",
        "camera": "live" if edge_frame_at and now - edge_frame_at <= EDGE_FRAME_STALE_S else "waiting",
        "pose": "live" if edge_pose_at and now - edge_pose_at <= 2.0 else "waiting",
        "frameSeq": edge_frame_seq,
        "poseSeq": edge_pose_seq,
    })


def make_app():
    app = web.Application(client_max_size=2 * 1024 * 1024)
    app.router.add_get("/ws", ws_handler)
    app.router.add_post("/edge/frame", edge_frame_handler)
    app.router.add_post("/edge/source/frame", edge_source_frame_handler)
    app.router.add_get("/edge/source/camera.mjpg", edge_source_camera_mjpeg)
    app.router.add_get("/edge/frame.jpg", edge_frame_jpeg)
    app.router.add_get("/edge/camera.mjpg", edge_camera_mjpeg)
    app.router.add_get("/edge/status", edge_status)
    app.router.add_get("/", lambda r: web.HTTPFound("/tv.html"))
    app.router.add_static("/", PUBLIC, show_index=True)
    return app


async def main():
    app = make_app()
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8080).start()
    loop = asyncio.get_running_loop()
    edge_transport = None
    try:
        edge_transport, _ = await loop.create_datagram_endpoint(
            EdgePoseProtocol,
            local_addr=("0.0.0.0", EDGE_POSE_PORT),
        )
    except OSError as exc:
        print(f"UNO Q :  UDP :{EDGE_POSE_PORT} unavailable ({exc}); local phone pose still works")
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
    print(f"Desk  :  {'LOCAL AI DESK · ' + desk.model if desk.mode == 'local' else 'CLAUDE DESK · ' + desk.model if desk.mode == 'cloud' else 'templates only (set ANTHROPIC_API_KEY or GF_LLM_URL to upgrade)'}")
    if edge_transport is not None:
        print(f"UNO Q :  UDP pose :{EDGE_POSE_PORT} / POST /edge/frame")
    try:
        await asyncio.Event().wait()
    finally:
        if edge_transport is not None:
            edge_transport.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
