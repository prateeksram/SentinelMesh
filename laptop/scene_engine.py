"""Pillar 3 — SceneEngine: agentic GenieX TV generation + golden verify + learning."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import geniex_client
import scene_contract

ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"
GOLDEN = PUBLIC / "tv.html"
SCENES = PUBLIC / "scenes"
CANDIDATES = SCENES / "candidates"
LIVE = SCENES / "live"
LOGS = ROOT / "logs"
MEMORY_PATH = LOGS / "scene_memory.jsonl"
FEWSHOT_PATH = LOGS / "scene_fewshot.json"
MAX_LEVEL = int(os.environ.get("GF_SCENE_MAX_LEVEL", 5))
TIMEOUT = float(os.environ.get("GF_SCENE_TIMEOUT_S", 90))
MAX_ATTEMPTS = int(os.environ.get("GF_SCENE_MAX_ATTEMPTS", 3))
MEMORY_CAP = int(os.environ.get("GF_SCENE_MEMORY_CAP", 24))

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

DIFF = {
    1: {
        "keeperIq": 0.65,
        "keeperReaction": 0.50,
        "shootWindow": 0,
        "powerBeat": 0.85,
    },
    2: {
        "keeperIq": 0.72,
        "keeperReaction": 0.46,
        "shootWindow": 0,
        "powerBeat": 0.84,
    },
    3: {
        "keeperIq": 0.78,
        "keeperReaction": 0.42,
        "shootWindow": 3.0,
        "powerBeat": 0.83,
    },
    4: {
        "keeperIq": 0.85,
        "keeperReaction": 0.38,
        "shootWindow": 2.6,
        "powerBeat": 0.82,
    },
    5: {
        "keeperIq": 0.92,
        "keeperReaction": 0.34,
        "shootWindow": 2.2,
        "powerBeat": 0.80,
    },
}

# Last agentic status (for /scene/status + brief)
LAST_STATUS: dict = {
    "genStep": "",
    "attempts": [],
    "lessonsApplied": 0,
    "contract": None,
    "promoted": False,
    "tvUrl": None,
}


def pick_next_level(score, current):
    want = {0: 1, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5}.get(
        score, min(5, max(1, score))
    )
    return max(1, min(MAX_LEVEL, max(current, want)))


def build_context(score, saves, kicks_total, shotmap):
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
    return out


def _safe_css(css: str) -> str:
    css = css or ""
    css = re.sub(r"(?i)</?script[^>]*>", "", css)
    css = re.sub(r"(?i)expression\s*\(", "/*blocked*/(", css)
    css = re.sub(r"(?i)@import\b", "/*blocked-import*/", css)
    return css[:12_000]


def _safe_overlay(html: str) -> str:
    html = html or ""
    html = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", html)
    html = re.sub(r"(?i)\son\w+\s*=", " data-blocked=", html)
    html = re.sub(r"(?i)javascript:", "", html)
    return html[:8_000]


def template_visual(level: int) -> tuple[str, str]:
    a = DEFAULT_ATMOS[level]
    tod = TIME_OF_DAY[level]
    floods = (
        "box-shadow:inset 0 0 120px rgba(255,220,120,.18);"
        if a["floodlights"]
        else ""
    )
    css = f"""
/* venue skin level {level} — template */
:root{{
  --night:{a["sky"]};
  --ink:{a["tint"]};
  --venue-tint:{a["tint"]};
}}
body{{
  background:radial-gradient(120% 80% at 50% 0%,{a["sky"]} 0%,#04070d 55%);
  {floods}
}}
.livebug{{border-left-color:{"#FFC400" if level >= 4 else "#FF4438"};}}
#venueSplash{{
  position:fixed;inset:0;z-index:3;pointer-events:none;
  background:linear-gradient(180deg,{a["tint"]}33,transparent 42%);
  opacity:{0.35 + 0.1 * level};
}}
#venueTitleCard{{
  position:fixed;top:72px;left:18px;z-index:7;max-width:280px;
  font-family:var(--fBlack);font-size:18px;letter-spacing:1px;
  color:#fff;text-shadow:0 4px 18px rgba(0,0,0,.7);
  padding:10px 14px;border-radius:10px;
  background:linear-gradient(135deg,rgba(8,18,28,.82),rgba(5,12,20,.7));
  border:1px solid rgba(158,190,215,.28);
}}
#venueTitleCard small{{
  display:block;font-family:var(--fCond);font-weight:700;
  letter-spacing:2px;font-size:11px;color:var(--mut);margin-top:4px;
}}
"""
    overlay = (
        f'<div id="venueSplash" aria-hidden="true"></div>'
        f'<div id="venueTitleCard">Level {level} — {tod.title()}'
        f"<small>NEXT VENUE · GRADE {a['grade'].upper()}</small></div>"
    )
    return css.strip(), overlay


def assemble_candidate(css: str, overlay_html: str, *, level: int) -> str:
    """Build a full tv.html from golden + visual injections (never drop hooks)."""
    golden = GOLDEN.read_text(encoding="utf-8")
    css = _safe_css(css)
    overlay_html = _safe_overlay(overlay_html)
    style_block = (
        f"\n<style id=\"venue-skin\" data-level=\"{level}\">\n{css}\n</style>\n"
    )
    if "</head>" in golden:
        html = golden.replace("</head>", style_block + "</head>", 1)
    else:
        html = style_block + golden
    marker = "<!-- venue-overlay -->"
    body_inject = f"\n{marker}\n{overlay_html}\n"
    if "<body>" in html:
        html = html.replace("<body>", "<body>" + body_inject, 1)
    elif "<body " in html:
        html = re.sub(
            r"(<body[^>]*>)", r"\1" + body_inject, html, count=1, flags=re.I
        )
    else:
        html = body_inject + html
    # Fingerprint differs even if GenieX returns empty — stamp meta
    stamp = (
        f'\n<meta name="gf-venue-level" content="{level}">'
        f'\n<meta name="gf-venue-fp" content="{hashlib.sha256((css+overlay_html).encode()).hexdigest()[:12]}">'
    )
    if "<head>" in html:
        html = html.replace("<head>", "<head>" + stamp, 1)
    return html


def _read_memory(n: int = 8) -> list[dict]:
    if not MEMORY_PATH.is_file():
        return []
    rows = []
    try:
        for line in MEMORY_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return rows[-n:]


def _append_memory(entry: dict) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    with MEMORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    # Trim file if huge
    try:
        lines = MEMORY_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > MEMORY_CAP * 3:
            MEMORY_PATH.write_text(
                "\n".join(lines[-(MEMORY_CAP * 2) :]) + "\n", encoding="utf-8"
            )
    except OSError:
        pass


def _update_fewshot(kind: str, note: str, level: int) -> None:
    data = {"wins": [], "fails": []}
    if FEWSHOT_PATH.is_file():
        try:
            data = json.loads(FEWSHOT_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    bucket = data.setdefault("wins" if kind == "win" else "fails", [])
    bucket.append({"t": time.time(), "level": level, "note": note[:240]})
    data["wins"] = data.get("wins", [])[-8:]
    data["fails"] = data.get("fails", [])[-12:]
    LOGS.mkdir(parents=True, exist_ok=True)
    FEWSHOT_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _lessons_prompt() -> tuple[str, int]:
    mem = _read_memory(10)
    few = {}
    if FEWSHOT_PATH.is_file():
        try:
            few = json.loads(FEWSHOT_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            few = {}
    bits = []
    for e in mem:
        if e.get("kind") == "reject":
            bits.append("DO NOT: " + (e.get("note") or "")[:120])
        elif e.get("kind") == "accept":
            bits.append("GOOD: " + (e.get("note") or "")[:100])
    for e in (few.get("fails") or [])[-4:]:
        bits.append("DO NOT: " + (e.get("note") or "")[:120])
    for e in (few.get("wins") or [])[-3:]:
        bits.append("GOOD: " + (e.get("note") or "")[:100])
    # unique preserve order
    seen, out = set(), []
    for b in bits:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return "\n".join(out[:12]), len(out)


DIRECTOR_SYSTEM = (
    "You are the match director for Gesture Football (TV broadcast). "
    "Design the NEXT venue look. Reply with ONE JSON object only — no prose, "
    "no code fences. Schema: {"
    "level:int, timeOfDay:str, title:str, report:str,"
    "atmosphere:{sky:hex, grade:str, floodlights:bool, tint:hex, crowd:0-1},"
    "difficulty:{keeperIq:0-1, keeperReaction:0.3-0.6, shootWindow:0-4, powerBeat:0.78-0.9},"
    "copy:{lobbyLine:str, ticker:str},"
    "css:str, overlayHtml:str"
    "}. "
    "css = extra CSS only (no <script>). overlayHtml = small decorative HTML "
    "using ids venueSplash and/or venueTitleCard only — never remove or rename "
    "existing game elements (pitch, startBtn, WebSocket, onState, scores). "
    "Harder level = higher keeperIq, lower keeperReaction, shorter shootWindow. "
    "Make each level visually distinct (day→night)."
)

CRITIC_SYSTEM = (
    "You fix a TV venue skin that failed validation. Reply with ONE JSON object "
    "only: {css:str, overlayHtml:str, title:str, report:str, "
    "atmosphere:{sky,grade,floodlights,tint,crowd}, "
    "copy:{lobbyLine,ticker}, difficulty:{keeperIq,keeperReaction,shootWindow,powerBeat}}. "
    "Keep all game hooks intact — only visuals. No <script>. Fix the listed errors."
)


def template_scene(level, ctx, *, source="template", tv_url=None, fp=None):
    a = DEFAULT_ATMOS[level]
    css, overlay = template_visual(level)
    return {
        "level": level,
        "timeOfDay": TIME_OF_DAY[level],
        "title": f"Level {level} — {TIME_OF_DAY[level].title()}",
        "report": f"Scored {ctx['score']}/{ctx['kicks_total']}. The Wall adapts.",
        "atmosphere": a,
        "difficulty": DIFF[level],
        "copy": {
            "lobbyLine": (
                f"{TIME_OF_DAY[level].title()} at the ground. "
                "THE WALL is sharper."
            ),
            "ticker": "Next venue ready.",
        },
        "css": css,
        "overlayHtml": overlay,
        "tvUrl": tv_url,
        "fingerprint": fp,
        "verified": bool(tv_url),
        "metrics": {
            "model": geniex_client.GENIEX_MODEL,
            "source": source,
            "ttft_ms": 0,
            "total_ms": 0,
            "tokens": 0,
            "tok_per_s": 0,
            "attempts": 0,
        },
    }


def _scene_from_data(data: dict, level: int, ctx: dict, t0: float, source: str):
    data = dict(data or {})
    data["level"] = level
    data.setdefault("timeOfDay", TIME_OF_DAY[level])
    atmos = {**DEFAULT_ATMOS[level], **(data.get("atmosphere") or {})}
    css = data.get("css") or template_visual(level)[0]
    overlay = data.get("overlayHtml") or template_visual(level)[1]
    return {
        "level": level,
        "timeOfDay": data["timeOfDay"],
        "title": data.get("title", f"Level {level}"),
        "report": data.get("report", ""),
        "atmosphere": atmos,
        "difficulty": _clamp_diff(data.get("difficulty") or {}, level),
        "copy": {
            "lobbyLine": (data.get("copy") or {}).get("lobbyLine", ""),
            "ticker": (data.get("copy") or {}).get("ticker", "Next venue ready."),
        },
        "css": css,
        "overlayHtml": overlay,
        "metrics": {
            "model": geniex_client.GENIEX_MODEL,
            "source": source,
            "total_ms": int((time.perf_counter() - t0) * 1000),
        },
    }


async def _progress(cb, pct, step=""):
    LAST_STATUS["genStep"] = step or LAST_STATUS.get("genStep") or ""
    if not cb:
        return
    try:
        await cb(pct, step)
    except TypeError:
        await cb(pct)


def promote_html(level: int, html: str) -> str:
    LIVE.mkdir(parents=True, exist_ok=True)
    CANDIDATES.mkdir(parents=True, exist_ok=True)
    cand = CANDIDATES / f"level_{level}.html"
    live = LIVE / f"level_{level}.html"
    cand.write_text(html, encoding="utf-8")
    live.write_text(html, encoding="utf-8")
    return f"/scenes/live/level_{level}.html"


def build_brief(level: int | None = None, ctx: dict | None = None) -> dict:
    contract = scene_contract.load_contract()
    lessons, n = _lessons_prompt()
    lvl = level or LAST_STATUS.get("level") or 1
    return {
        "level": lvl,
        "context": ctx,
        "contract_summary": {
            "required_ids": contract.get("required_ids"),
            "required_symbols": contract.get("required_symbols"),
            "golden_fingerprint": contract.get("golden_fingerprint"),
        },
        "lessons": lessons,
        "lessonCount": n,
        "lastStatus": {
            k: LAST_STATUS.get(k)
            for k in (
                "genStep",
                "attempts",
                "promoted",
                "tvUrl",
                "contract",
            )
        },
        "instructions": (
            "Return CSS + overlayHtml only (or full assembled HTML). "
            "Must keep every required_id and WebSocket/onState/applyScene hooks. "
            "Upload via POST /scene/upload with fields level, css, overlayHtml "
            "OR html (full file)."
        ),
    }


async def upload_and_promote(
    *,
    level: int,
    css: str | None = None,
    overlay_html: str | None = None,
    html: str | None = None,
    ctx: dict | None = None,
) -> dict:
    """Validate uploaded visuals/HTML against golden; promote if ok."""
    contract = scene_contract.load_contract()
    ctx = ctx or {"score": 0, "saves": 0, "kicks_total": 5}
    if html:
        candidate = html
        # ensure stamp
        if "gf-venue-level" not in candidate:
            candidate = assemble_candidate(
                css or "", overlay_html or "", level=level
            )
            # if they sent full html, use it but still verify
            candidate = html
    else:
        if not css and not overlay_html:
            css, overlay_html = template_visual(level)
        candidate = assemble_candidate(css or "", overlay_html or "", level=level)
    result = scene_contract.verify(candidate, contract)
    if not result["ok"]:
        _append_memory(
            {
                "t": time.time(),
                "kind": "reject",
                "level": level,
                "note": "upload fail: " + ",".join(result["errors"][:4]),
                "errors": result["errors"],
            }
        )
        _update_fewshot(
            "fail", "upload: " + ",".join(result["errors"][:3]), level
        )
        return {"ok": False, "verify": result}

    tv_url = promote_html(level, candidate)
    scene = template_scene(
        level, ctx, source="upload", tv_url=tv_url, fp=result["fingerprint"]
    )
    if css:
        scene["css"] = css
    if overlay_html:
        scene["overlayHtml"] = overlay_html
    scene["verified"] = True
    scene["fingerprint"] = result["fingerprint"]
    scene["tvUrl"] = tv_url
    (SCENES / f"level_{level}.json").write_text(json.dumps(scene), encoding="utf-8")
    (SCENES / "latest.json").write_text(json.dumps(scene), encoding="utf-8")
    LAST_STATUS.update(
        {
            "promoted": True,
            "tvUrl": tv_url,
            "contract": result,
            "level": level,
            "genStep": "Ready — verified (upload)",
        }
    )
    _append_memory(
        {
            "t": time.time(),
            "kind": "accept",
            "level": level,
            "note": f"upload ok fp={result['fingerprint']}",
            "fingerprint": result["fingerprint"],
        }
    )
    _update_fewshot("win", f"upload verified {result['fingerprint']}", level)
    return {"ok": True, "scene": scene, "verify": result, "tvUrl": tv_url}


async def generate(ctx, level, progress_cb=None):
    SCENES.mkdir(parents=True, exist_ok=True)
    CANDIDATES.mkdir(parents=True, exist_ok=True)
    LIVE.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    attempts_log: list[dict] = []
    LAST_STATUS.update(
        {
            "level": level,
            "attempts": attempts_log,
            "promoted": False,
            "tvUrl": None,
            "contract": None,
        }
    )

    await _progress(progress_cb, 5, "Loading golden TV contract…")
    contract = scene_contract.save_contract()  # refresh from golden

    await _progress(progress_cb, 10, "Loading learned lessons…")
    lessons, lesson_n = _lessons_prompt()
    LAST_STATUS["lessonsApplied"] = lesson_n

    await _progress(progress_cb, 12, "Checking on-device GenieX…")
    geniex_ok = await geniex_client.ping(timeout=3.0)

    user_base = {
        "match": ctx,
        "targetLevel": level,
        "timeOfDayHint": TIME_OF_DAY[level],
        "requiredIds": contract.get("required_ids"),
        "lessons": lessons,
    }

    data = None
    html = None
    verify_result = None
    source = "template"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        label = (
            f"Director drafting venue HTML (attempt {attempt}/{MAX_ATTEMPTS})…"
            if attempt == 1
            else f"Director reframing (attempt {attempt}/{MAX_ATTEMPTS})…"
        )
        await _progress(progress_cb, 15 + (attempt - 1) * 20, label)

        if not geniex_ok:
            data = None
        elif attempt == 1:
            data = await geniex_client.chat_json(
                DIRECTOR_SYSTEM,
                json.dumps(user_base),
                max_tokens=1400,
                timeout=TIMEOUT,
            )
        else:
            fail = {
                "previous": {
                    "css": (data or {}).get("css", "")[:1500],
                    "overlayHtml": (data or {}).get("overlayHtml", "")[:800],
                },
                "errors": (verify_result or {}).get("errors", []),
                "missing_ids": (verify_result or {}).get("missing_ids", []),
                "lessons": lessons,
                "targetLevel": level,
            }
            await _progress(
                progress_cb,
                55 + (attempt - 1) * 5,
                "Critic reviewing failures…",
            )
            fix = await geniex_client.chat_json(
                CRITIC_SYSTEM,
                json.dumps(fail),
                max_tokens=1200,
                timeout=TIMEOUT,
            )
            if fix and data:
                data = {**data, **fix}
            elif fix:
                data = fix
            else:
                data = None

        if not data:
            # GenieX down — use template visuals (one attempt)
            css, overlay = template_visual(level)
            data = {
                "title": f"Level {level} — {TIME_OF_DAY[level].title()}",
                "report": f"Scored {ctx['score']}/{ctx['kicks_total']}.",
                "atmosphere": DEFAULT_ATMOS[level],
                "difficulty": DIFF[level],
                "copy": {
                    "lobbyLine": template_scene(level, ctx)["copy"]["lobbyLine"],
                    "ticker": "Next venue ready.",
                },
                "css": css,
                "overlayHtml": overlay,
            }
            source = "template"
        else:
            source = "geniex"
            data.setdefault("css", template_visual(level)[0])
            data.setdefault("overlayHtml", template_visual(level)[1])

        await _progress(progress_cb, 40 + (attempt - 1) * 15, "Writing candidate file…")
        html = assemble_candidate(
            data.get("css", ""), data.get("overlayHtml", ""), level=level
        )
        cand_path = CANDIDATES / f"level_{level}_a{attempt}.html"
        cand_path.write_text(html, encoding="utf-8")

        await _progress(
            progress_cb,
            50 + (attempt - 1) * 10,
            "Validating candidate against golden contract…",
        )
        verify_result = scene_contract.verify(html, contract)
        attempts_log.append(
            {
                "attempt": attempt,
                "ok": verify_result["ok"],
                "errors": verify_result["errors"],
                "fingerprint": verify_result["fingerprint"],
                "source": source,
            }
        )
        LAST_STATUS["contract"] = verify_result

        if verify_result["ok"]:
            break

        if source == "template":
            # Template assembly should always pass; don't spin
            break

        miss = ",".join(verify_result.get("missing_ids") or [])[:80]
        err = ",".join(verify_result.get("errors") or [])[:100]
        note = f"missing={miss or '-'} err={err}"
        await _progress(
            progress_cb,
            70,
            f"Recording lesson — {err[:90] or 'contract fail'}…",
        )
        _append_memory(
            {
                "t": time.time(),
                "kind": "reject",
                "level": level,
                "attempt": attempt,
                "note": note,
                "errors": verify_result["errors"],
            }
        )
        _update_fewshot("fail", note, level)
        lessons, lesson_n = _lessons_prompt()
        LAST_STATUS["lessonsApplied"] = lesson_n

    scene = _scene_from_data(data or {}, level, ctx, t0, source)
    scene["metrics"]["attempts"] = len(attempts_log)

    if verify_result and verify_result["ok"] and html:
        await _progress(progress_cb, 85, "Promoting verified TV page…")
        tv_url = promote_html(level, html)
        scene["tvUrl"] = tv_url
        scene["fingerprint"] = verify_result["fingerprint"]
        scene["verified"] = True
        LAST_STATUS.update({"promoted": True, "tvUrl": tv_url})
        _append_memory(
            {
                "t": time.time(),
                "kind": "accept",
                "level": level,
                "note": f"verified fp={verify_result['fingerprint']} src={source}",
                "fingerprint": verify_result["fingerprint"],
            }
        )
        _update_fewshot(
            "win",
            f"level {level} {scene.get('title', '')} fp={verify_result['fingerprint']}",
            level,
        )
        await _progress(progress_cb, 95, "Applying next venue…")
        await _progress(
            progress_cb,
            100,
            f"Ready — verified · {verify_result['fingerprint']}",
        )
    else:
        # Fallback: still write template-assembled page for atmos, but mark unverified
        # Prefer golden path — do not promote broken HTML
        await _progress(progress_cb, 85, "Fallback to golden TV…")
        css, overlay = template_visual(level)
        safe_html = assemble_candidate(css, overlay, level=level)
        safe_v = scene_contract.verify(safe_html, contract)
        tv_url = None
        if safe_v["ok"]:
            tv_url = promote_html(level, safe_html)
            scene["css"] = css
            scene["overlayHtml"] = overlay
            scene["fingerprint"] = safe_v["fingerprint"]
            scene["verified"] = True
            scene["tvUrl"] = tv_url
            scene["metrics"]["source"] = "template"
            LAST_STATUS.update({"promoted": True, "tvUrl": tv_url})
            await _progress(
                progress_cb,
                100,
                f"Fallback to golden TV · template skin · {safe_v['fingerprint']}",
            )
        else:
            scene["verified"] = False
            scene["tvUrl"] = None
            scene["fingerprint"] = None
            scene["metrics"]["source"] = "template"
            LAST_STATUS["promoted"] = False
            await _progress(progress_cb, 100, "Fallback to golden TV")

    (SCENES / f"level_{level}.json").write_text(
        json.dumps(scene), encoding="utf-8"
    )
    (SCENES / "latest.json").write_text(json.dumps(scene), encoding="utf-8")
    with (LOGS / "scene_gen.jsonl").open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "t": time.time(),
                    "level": level,
                    "source": scene["metrics"]["source"],
                    "total_ms": scene["metrics"].get("total_ms", 0),
                    "fingerprint": scene.get("fingerprint"),
                    "verified": scene.get("verified"),
                    "tvUrl": scene.get("tvUrl"),
                    "attempts": attempts_log,
                    "genStep": LAST_STATUS.get("genStep"),
                }
            )
            + "\n"
        )
    return scene
