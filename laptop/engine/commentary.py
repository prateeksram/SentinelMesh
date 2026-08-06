"""Commentary orchestration: GenieX-first Desk with template fallback.

Backend order: GenieX (default, on-device Qwen3-4B via geniex_client, which
carries the circuit breaker) > local Ollama > Anthropic cloud > templates.
Every call is fire-and-forget; the game never waits on it.

Boundary note: the engine never *listens* on a socket. GenieX/LLM calls are
outbound HTTP through the engine's own client — never through server state.
aiohttp is imported lazily so the engine core stays stdlib-importable.
"""

from __future__ import annotations

import json
import os
import random

import geniex_client


class Desk:
    """LLM commentary desk. Falls back silently to templates."""

    def __init__(self):
        self.local_url = os.environ.get("GF_LLM_URL")   # e.g. http://localhost:11434/v1/messages
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        # GenieX is default; set GF_GENIEX=0 to fall through to local/cloud.
        self.geniex = os.environ.get("GF_GENIEX", "1").lower() not in ("0", "false", "no")
        if self.geniex:
            self.mode = "geniex"
            self.model = geniex_client.GENIEX_MODEL
        elif self.local_url:
            self.mode = "local"
            self.model = os.environ.get("GF_MODEL", "llama3.2:3b")
        elif self.api_key:
            self.mode = "cloud"
            self.model = os.environ.get("GF_MODEL", "claude-haiku-4-5-20251001")
        else:
            self.mode = None
        self._warned = False
        self.recent = []                                # anti-repetition
        self.last_meta: dict = {}                       # tok/s etc. for telemetry

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
            text, meta = await geniex_client.chat_with_meta(
                self.SYSTEM, prompts[kind] + json.dumps(ctx),
                max_tokens=110, timeout=20.0)
            self.last_meta = meta
            if text:
                self.recent = (self.recent + [text])[-6:]
                return text
            if not self._warned and meta.get("breaker") != "open":
                print("[desk] geniex unreachable — template commentary continues")
                self._warned = True
            return None
        from aiohttp import ClientSession, ClientTimeout  # lazy — see module note
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
