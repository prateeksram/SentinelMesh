"""Pillar 3 — SceneEngine: GenieX designs the next venue + difficulty."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import geniex_client
import openai_client

ROOT = Path(__file__).parent
SCENES = ROOT / "public" / "scenes"
LOGS = ROOT / "logs"
MAX_LEVEL = int(os.environ.get("GF_SCENE_MAX_LEVEL", 5))
TIMEOUT = float(os.environ.get("GF_SCENE_TIMEOUT_S", 90))
# Primary scene call budget (was hardcoded 900 via chat_json). Compact retry
# runs only if the first attempt returns nothing / times out.
MAX_TOKENS = int(os.environ.get("GF_SCENE_MAX_TOKENS", 320))
MAX_TOKENS_RETRY = int(os.environ.get("GF_SCENE_MAX_TOKENS_RETRY", 180))
OPENAI_MAX_TOKENS = int(os.environ.get("GF_SCENE_OPENAI_MAX_TOKENS", 400))
OPENAI_TIMEOUT = float(os.environ.get("GF_SCENE_OPENAI_TIMEOUT_S", 45))
# Keep the TV "NEXT VENUE" bar visible even when GenieX fails fast → template.
MIN_DISPLAY_S = float(os.environ.get("GF_SCENE_MIN_DISPLAY_S", "2.5"))

TIME_OF_DAY = {
    1: "day",
    2: "late morning",
    3: "afternoon",
    4: "golden hour",
    5: "night",
}

DEFAULT_ATMOS = {
    1: {
        "sky": "#8Fc3ff",
        "grade": "day",
        "floodlights": False,
        "tint": "#eaf4ff",
        "crowd": 0.4,
    },
    2: {
        "sky": "#bcd0e0",
        "grade": "morning",
        "floodlights": False,
        "tint": "#f2f6f0",
        "crowd": 0.5,
    },
    3: {
        "sky": "#f2b36b",
        "grade": "afternoon",
        "floodlights": False,
        "tint": "#ffe9c8",
        "crowd": 0.65,
    },
    4: {
        "sky": "#e6743a",
        "grade": "golden",
        "floodlights": True,
        "tint": "#ffd9a8",
        "crowd": 0.8,
    },
    5: {
        "sky": "#0b1d2a",
        "grade": "night",
        "floodlights": True,
        "tint": "#0f2436",
        "crowd": 1.0,
    },
}

# Darts / basketball difficulty: scoring rings shrink as the campaign climbs.
RING_SCALE = {1: 1.00, 2: 0.90, 3: 0.80, 4: 0.70, 5: 0.60}

DIFF = {
    1: {
        "keeperIq": 0.65,
        "keeperReaction": 0.50,
        "shootWindow": 0,
        "powerBeat": 0.85,
        "ringScale": RING_SCALE[1],
    },
    2: {
        "keeperIq": 0.72,
        "keeperReaction": 0.46,
        "shootWindow": 0,
        "powerBeat": 0.84,
        "ringScale": RING_SCALE[2],
    },
    3: {
        "keeperIq": 0.78,
        "keeperReaction": 0.42,
        "shootWindow": 3.0,
        "powerBeat": 0.83,
        "ringScale": RING_SCALE[3],
    },
    4: {
        "keeperIq": 0.85,
        "keeperReaction": 0.38,
        "shootWindow": 2.6,
        "powerBeat": 0.82,
        "ringScale": RING_SCALE[4],
    },
    5: {
        "keeperIq": 0.92,
        "keeperReaction": 0.34,
        "shootWindow": 2.2,
        "powerBeat": 0.80,
        "ringScale": RING_SCALE[5],
    },
}


def pick_next_level(score, current):
    want = {0: 1, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5}.get(
        score, min(5, max(1, score))
    )
    return max(1, min(MAX_LEVEL, max(current, want)))  # never regress


def build_context(score, saves, kicks_total, shotmap, sport="football"):
    zones = {"L": 0, "C": 0, "R": 0}
    forces, feints, chips, drives = [], 0, 0, 0
    for s in shotmap:
        if s.get("zone") in zones:
            zones[s["zone"]] += 1
        if s.get("force"):
            forces.append(s["force"])
        if s.get("strike") == "chip":
            chips += 1
        elif s.get("strike") == "drive":
            drives += 1
        if (
            s.get("zone")
            and s.get("keeperZone")
            and s["zone"] != s["keeperZone"]
        ):
            feints += 1
    return {
        "sport": sport,
        "score": score,
        "saves": saves,
        "kicks_total": kicks_total,
        "zone_histogram": zones,
        "avg_force": round(sum(forces) / len(forces)) if forces else 0,
        "peak_force": max(forces) if forces else 0,
        "feint_count": feints,
        "chip_vs_drive": {"chip": chips, "drive": drives},
        "shotmap": shotmap,
    }


def template_scene(level, ctx):
    a = DEFAULT_ATMOS[level]
    sport = ctx.get("sport", "football")
    if sport == "football":
        report = f"Scored {ctx['score']}/{ctx['kicks_total']}. The Wall adapts."
        lobby = (f"{TIME_OF_DAY[level].title()} at the ground. "
                 "THE WALL is sharper.")
    else:
        report = (f"{ctx['score']}/{ctx['kicks_total']} on target. "
                  "The rings tighten.")
        lobby = (f"{TIME_OF_DAY[level].title()} session. "
                 "Smaller rings, same points — earn them.")
    return {
        "level": level,
        "timeOfDay": TIME_OF_DAY[level],
        "title": f"Level {level} — {TIME_OF_DAY[level].title()}",
        "report": report,
        "atmosphere": a,
        "difficulty": DIFF[level],
        "copy": {
            "lobbyLine": lobby,
            "ticker": "Next venue ready.",
        },
        "metrics": {
            "model": geniex_client.GENIEX_MODEL,
            "source": "template",
            "ttft_ms": 0,
            "total_ms": 0,
            "tokens": 0,
            "tok_per_s": 0,
        },
    }


SYSTEM = (
    "You are the match director for a gesture-controlled arena game "
    "(sport given in the stats: football penalty shootout vs an AI keeper, "
    "or darts / basketball ring-target rounds). Given match stats, design "
    "the NEXT venue and difficulty. Reply with ONE JSON object only, no "
    "prose, no code fences. Schema: {level:int, timeOfDay:str, "
    "title:str, report:str(<=2 sentences), atmosphere:{sky:hex, grade:str, "
    "floodlights:bool, tint:hex, crowd:0-1}, difficulty:{keeperIq:0-1, "
    "keeperReaction:0.3-0.6, shootWindow:0-4, powerBeat:0.78-0.9, "
    "ringScale:0.5-1.0}, copy:{lobbyLine:str, ticker:str}}. Harder level = "
    "higher keeperIq, lower keeperReaction, shorter shootWindow, smaller "
    "ringScale (tighter scoring rings for darts/basketball)."
)


def _clamp_diff(d, level):
    base = DIFF[level]
    out = {}

    def cl(v, lo, hi, dflt):
        try:
            return max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            return dflt

    out["keeperIq"] = cl(d.get("keeperIq"), 0.0, 1.0, base["keeperIq"])
    out["keeperReaction"] = cl(
        d.get("keeperReaction"), 0.30, 0.60, base["keeperReaction"]
    )
    out["shootWindow"] = cl(d.get("shootWindow"), 0.0, 4.0, base["shootWindow"])
    out["powerBeat"] = cl(d.get("powerBeat"), 0.78, 0.90, base["powerBeat"])
    out["ringScale"] = cl(d.get("ringScale"), 0.50, 1.00, base["ringScale"])
    return out


async def _emit(progress_cb, p, step=None):
    if not progress_cb:
        return
    try:
        await progress_cb(p, step)
    except TypeError:
        # Older callers only accept progress percent.
        await progress_cb(p)


async def _chat_json_with_progress(
    system,
    user,
    *,
    max_tokens,
    timeout,
    progress_cb,
    lo,
    hi,
    provider="geniex",
    step="",
):
    """Run chat_json while easing genProgress from lo→hi so the TV bar moves."""
    if provider == "openai":
        coro = openai_client.chat_json(
            system, user, max_tokens=max_tokens, timeout=timeout
        )
    else:
        coro = geniex_client.chat_json(
            system, user, max_tokens=max_tokens, timeout=timeout
        )
    task = asyncio.create_task(coro)
    t0 = time.monotonic()
    try:
        if step:
            await _emit(progress_cb, lo, step)
        while not task.done():
            frac = min(0.95, (time.monotonic() - t0) / max(timeout, 1.0))
            await _emit(progress_cb, int(lo + (hi - lo) * frac), step or None)
            done, _ = await asyncio.wait({task}, timeout=0.35)
            if done:
                break
        return task.result()
    except asyncio.CancelledError:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        raise
    except Exception:
        return None


def _scene_from_llm(data, level, *, source, model, elapsed_ms, used_tokens):
    data = dict(data)
    data["level"] = level
    data.setdefault("timeOfDay", TIME_OF_DAY[level])
    atmos = {**DEFAULT_ATMOS[level], **(data.get("atmosphere") or {})}
    return {
        "level": level,
        "timeOfDay": data["timeOfDay"],
        "title": data.get("title", f"Level {level}"),
        "report": data.get("report", ""),
        "atmosphere": atmos,
        "difficulty": _clamp_diff(data.get("difficulty") or {}, level),
        "copy": {
            "lobbyLine": (data.get("copy") or {}).get("lobbyLine", ""),
            "ticker": (data.get("copy") or {}).get(
                "ticker", "Next venue ready."
            ),
        },
        "metrics": {
            "model": model,
            "source": source,
            "total_ms": elapsed_ms,
            "tokens_budget": used_tokens,
        },
    }


async def generate(ctx, level, progress_cb=None):
    SCENES.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    await _emit(
        progress_cb, 5,
        f"Level {level} — packing match stats for the director",
    )
    user = json.dumps(ctx)

    # GenieX (on-device) → compact GenieX retry → OpenAI cloud → template.
    primary_timeout = max(20.0, TIMEOUT * 0.55)
    retry_timeout = max(12.0, TIMEOUT * 0.25)

    data = await _chat_json_with_progress(
        SYSTEM,
        user,
        max_tokens=MAX_TOKENS,
        timeout=primary_timeout,
        progress_cb=progress_cb,
        lo=8,
        hi=45,
        provider="geniex",
        step=f"On-device GenieX · designing Level {level} venue JSON",
    )
    used_tokens = MAX_TOKENS
    source = "geniex"
    model = geniex_client.GENIEX_MODEL
    geniex_fail = "" if data else (geniex_client.last_fail() or "no reply")

    if not data and MAX_TOKENS_RETRY > 0 and MAX_TOKENS_RETRY < MAX_TOKENS:
        await _emit(
            progress_cb, 48,
            f"GenieX fail · {geniex_fail} — compact retry",
        )
        data = await _chat_json_with_progress(
            SYSTEM,
            user,
            max_tokens=MAX_TOKENS_RETRY,
            timeout=retry_timeout,
            progress_cb=progress_cb,
            lo=48,
            hi=62,
            provider="geniex",
            step=f"On-device GenieX · retry after {geniex_fail}",
        )
        used_tokens = MAX_TOKENS_RETRY
        if not data:
            geniex_fail = geniex_client.last_fail() or geniex_fail

    if not data and openai_client.configured():
        cloud_model = openai_client.scene_model()
        await _emit(
            progress_cb, 65,
            f"GenieX · {geniex_fail} → OpenAI {cloud_model}",
        )
        print(f"[scene] GenieX fail ({geniex_fail}) — trying OpenAI cloud", flush=True)
        data = await _chat_json_with_progress(
            SYSTEM,
            user,
            max_tokens=OPENAI_MAX_TOKENS,
            timeout=OPENAI_TIMEOUT,
            progress_cb=progress_cb,
            lo=65,
            hi=88,
            provider="openai",
            step=f"Cloud OpenAI · {cloud_model} designing venue JSON",
        )
        if data:
            used_tokens = OPENAI_MAX_TOKENS
            source = "openai"
            model = cloud_model

    if not data:
        await _emit(
            progress_cb, 90,
            f"Cloud missed — template (GenieX: {geniex_fail or 'n/a'})",
        )
    else:
        why = f" · GenieX was {geniex_fail}" if geniex_fail and source != "geniex" else ""
        await _emit(
            progress_cb, 90,
            f"Got venue from {source}{why}",
        )

    # Hold the generating overlay long enough to be readable on fast fallbacks.
    held = time.perf_counter() - t0
    if held < MIN_DISPLAY_S:
        await asyncio.sleep(MIN_DISPLAY_S - held)

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if not data:
        scene = template_scene(level, ctx)
        scene["metrics"]["total_ms"] = elapsed_ms
        scene["metrics"]["tokens_budget"] = used_tokens
        if geniex_fail:
            scene["metrics"]["geniex_fail"] = geniex_fail
        await _emit(
            progress_cb, 96,
            f"Template locked · GenieX {geniex_fail or 'offline'}",
        )
    else:
        scene = _scene_from_llm(
            data,
            level,
            source=source,
            model=model,
            elapsed_ms=elapsed_ms,
            used_tokens=used_tokens,
        )
        if geniex_fail and source != "geniex":
            scene["metrics"]["geniex_fail"] = geniex_fail
        await _emit(
            progress_cb, 96,
            f"Writing Level {level} · source={source}",
        )
    (SCENES / f"level_{level}.json").write_text(json.dumps(scene))
    (SCENES / "latest.json").write_text(json.dumps(scene))
    with (LOGS / "scene_gen.jsonl").open("a") as f:
        f.write(
            json.dumps(
                {
                    "t": time.time(),
                    "level": level,
                    "source": scene["metrics"]["source"],
                    "total_ms": scene["metrics"].get("total_ms", 0),
                    "tokens_budget": scene["metrics"].get("tokens_budget"),
                    "model": scene["metrics"].get("model"),
                    "geniex_fail": scene["metrics"].get("geniex_fail"),
                }
            )
            + "\n"
        )
    src = scene["metrics"]["source"]
    fail_bit = (
        f" · GenieX {scene['metrics'].get('geniex_fail')}"
        if scene["metrics"].get("geniex_fail")
        else ""
    )
    await _emit(
        progress_cb, 100,
        f"Venue ready · {src} · {scene['metrics'].get('total_ms', 0)} ms{fail_bit}",
    )
    return scene
