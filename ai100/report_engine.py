"""AI100-assisted, deterministic post-match scouting reports.

AI100 creates only the decorative stadium artwork. The laptop computes and
typesets every number so an image model can never hallucinate match telemetry.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
import os
import re
import secrets
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp
import qrcode
from PIL import Image, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
DATA = ROOT / "data" / "reports"
CACHE = ROOT / "cache"
REPORT_SIZE = (1600, 2200)
REPORT_TTL_SECONDS = 30 * 60


PRO_BENCHMARKS = (
    {
        "name": "Cristiano Ronaldo",
        "short": "RONALDO",
        "scored": 183,
        "missed": 36,
        "rate": round(183 / (183 + 36) * 100, 1),
        "source": "Transfermarkt",
        "sourceUrl": "https://www.transfermarkt.com/cristiano-ronaldo/elfmetertore/spieler/8198",
    },
    {
        "name": "Lionel Messi",
        "short": "MESSI",
        "scored": 116,
        "missed": 32,
        "rate": round(116 / (116 + 32) * 100, 1),
        "source": "Transfermarkt",
        "sourceUrl": "https://www.transfermarkt.com/lionel-messi/elfmetertore/spieler/28003",
    },
)
PRO_SNAPSHOT_DATE = "2026-08-06"


def load_repo_env() -> None:
    """Load a local .env without replacing variables set by the launcher."""
    configured_path = os.environ.get("AI100_ENV_FILE", "").strip()
    candidates = [Path(configured_path)] if configured_path else [ROOT / ".env", REPO / ".env"]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) and not os.environ.get(key):
            os.environ[key] = value


def generation_endpoint(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/images/generations"):
        return base
    if re.search(r"/(?:apis/)?v\d+$", base, flags=re.IGNORECASE):
        return f"{base}/images/generations"
    return f"{base}/v1/images/generations"


@dataclass(frozen=True)
class AI100Settings:
    endpoint: str
    api_key: str
    model: str
    size: str
    timeout: float

    @classmethod
    def from_env(cls) -> "AI100Settings":
        load_repo_env()
        endpoint = os.environ.get("AI100_IMAGE_ENDPOINT", "").strip()
        if not endpoint:
            endpoint = generation_endpoint(os.environ.get("AI100_BASE_URL", ""))
        model = os.environ.get("AI100_MODEL", "stabilityai/sdxl-turbo").strip()
        if "aisuite.cirrascale.com" in endpoint and model in {"sdxl-turbo", "stable-diffusion-xl"}:
            model = "stabilityai/sdxl-turbo"
        return cls(
            endpoint=endpoint,
            api_key=os.environ.get("AI100_API_KEY", "").strip(),
            model=model,
            size=os.environ.get("AI100_IMAGE_SIZE", "512x512").strip(),
            timeout=max(30.0, float(os.environ.get("AI100_TIMEOUT_SECONDS", "240"))),
        )

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.api_key and "YOUR_" not in self.api_key)


class AI100ArtworkClient:
    """Generate a text-free report illustration using Qualcomm Cloud AI100."""

    def __init__(self, settings: AI100Settings | None = None) -> None:
        self.settings = settings or AI100Settings.from_env()
        CACHE.mkdir(parents=True, exist_ok=True)

    async def generate(self, prompt: str) -> tuple[bytes | None, dict[str, Any]]:
        settings = self.settings
        cache_key = hashlib.sha256(
            f"{settings.model}|{settings.size}|{prompt}".encode("utf-8")
        ).hexdigest()[:24]
        cache_path = CACHE / f"{cache_key}.img"
        if cache_path.is_file():
            return cache_path.read_bytes(), {
                "source": "ai100-cache",
                "model": settings.model,
                "elapsedMs": 0,
            }
        if not settings.configured:
            return None, {"source": "procedural", "error": "AI100 is not configured"}

        payload = {
            "model": settings.model,
            "prompt": " ".join(prompt.split())[:1800],
            "n": 1,
            "stream": "aisuite.cirrascale.com" in settings.endpoint,
            "size": settings.size,
            "response_format": (
                "url" if "aisuite.cirrascale.com" in settings.endpoint else "b64_json"
            ),
        }
        headers = {
            "Authorization": f"Bearer {settings.api_key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(
            total=settings.timeout,
            connect=min(20.0, settings.timeout),
            sock_read=settings.timeout,
        )
        started = time.perf_counter()
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                record = await self._request(session, headers, payload)
                image = await self._extract(session, record, headers)
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_bytes(image)
            temporary.replace(cache_path)
            return image, {
                "source": "ai100",
                "model": settings.model,
                "elapsedMs": int((time.perf_counter() - started) * 1000),
            }
        except Exception as exc:  # The report must still work offline.
            return None, {
                "source": "procedural",
                "model": settings.model,
                "elapsedMs": int((time.perf_counter() - started) * 1000),
                "error": str(exc)[:180],
            }

    async def _request(self, session, headers, payload) -> dict[str, Any]:
        async with session.post(self.settings.endpoint, headers=headers, json=payload) as response:
            if response.status >= 400:
                text = (await response.text())[:300]
                raise RuntimeError(f"AI100 HTTP {response.status}: {text}")
            content_type = response.headers.get("Content-Type", "")
            if content_type.startswith("text/event-stream"):
                last: dict[str, Any] = {}
                async for raw in response.content:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        candidate = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, dict):
                        last = candidate
                        record = _first_image_record(candidate)
                        if record.get("url") or record.get("b64_json") or record.get("base64"):
                            return record
                if last:
                    return _first_image_record(last)
                raise RuntimeError("AI100 stream ended without an image")
            body = await response.json(content_type=None)
            return _first_image_record(body)

    async def _extract(self, session, record, headers) -> bytes:
        encoded = record.get("b64_json") or record.get("base64")
        if isinstance(encoded, str) and encoded:
            if encoded.startswith("data:") and "," in encoded:
                encoded = encoded.split(",", 1)[1]
            return base64.b64decode(encoded)
        url = record.get("url")
        if not isinstance(url, str) or not url:
            raise RuntimeError("AI100 returned no image")
        image_url = urljoin(self.settings.endpoint, url)
        endpoint_host = urlparse(self.settings.endpoint).netloc
        download_host = urlparse(image_url).netloc
        download_headers = headers if endpoint_host == download_host else None
        async with session.get(image_url, headers=download_headers) as response:
            if response.status >= 400:
                raise RuntimeError(f"AI100 asset HTTP {response.status}")
            image = await response.read()
        if not image or len(image) > 20 * 1024 * 1024:
            raise RuntimeError("AI100 returned an invalid image")
        return image


def _first_image_record(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key in ("data", "images", "output"):
        value = payload.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
    return payload


def analyze_match(
    shotmap: list[dict[str, Any]],
    kicks_total: int,
    player_name: str = "THE STRIKER",
) -> dict[str, Any]:
    """Convert raw phone telemetry into display-safe deterministic metrics."""
    shots = [dict(shot) for shot in shotmap if isinstance(shot, dict)]
    taken = max(len(shots), int(kicks_total or 0), 1)
    goals = sum(1 for shot in shots if shot.get("result") == "goal")
    rate = round(goals / taken * 100, 1)
    forces = [float(shot["force"]) for shot in shots if float(shot.get("force") or 0) > 0]
    average_force = round(statistics.mean(forces)) if forces else 0
    max_force = round(max(forces)) if forces else 0
    powers = [max(0.0, min(1.0, float(shot.get("power") or 0))) for shot in shots]
    average_power = round(statistics.mean(powers) * 100) if powers else 0
    consistency = 0
    if len(forces) == 1:
        consistency = 100
    elif len(forces) > 1 and average_force:
        consistency = round(max(0, min(100, 100 - statistics.pstdev(forces) / average_force * 100)))

    zones = {zone: sum(1 for shot in shots if shot.get("zone") == zone) for zone in "LCR"}
    nonzero = [count / max(1, len(shots)) for count in zones.values() if count]
    entropy = -sum(part * math.log(part, 2) for part in nonzero)
    unpredictability = round(entropy / math.log(3, 2) * 100) if len(shots) > 1 else 0
    favorite_code = max(zones, key=zones.get) if shots else "C"
    favorite_zone = {"L": "LEFT CORNER", "C": "CENTER", "R": "RIGHT CORNER"}[favorite_code]

    fooled = sum(
        1
        for shot in shots
        if shot.get("zone") in "LCR"
        and shot.get("keeperZone") in "LCR"
        and shot.get("zone") != shot.get("keeperZone")
    )
    spins = [abs(float(shot["spin"])) for shot in shots if isinstance(shot.get("spin"), (int, float))]
    curve = round(statistics.mean(spins) * 100) if spins else 0
    angles = [float(shot["dirDeg"]) for shot in shots if isinstance(shot.get("dirDeg"), (int, float))]
    launch = round(statistics.mean(angles)) if angles else 0
    feet = {foot: sum(1 for shot in shots if shot.get("foot") == foot) for foot in "LR"}
    dominant_foot = "LEFT" if feet["L"] > feet["R"] else "RIGHT" if feet["R"] else "UNKNOWN"
    drives = sum(1 for shot in shots if shot.get("strike") == "drive")
    chips = sum(1 for shot in shots if shot.get("strike") == "chip")
    high = sum(1 for shot in shots if shot.get("height") == "H")
    low = sum(1 for shot in shots if shot.get("height") == "L")

    if rate >= 80 and average_force >= 280:
        style, nickname = "POWER FINISHER", "THE THUNDERBOOT"
    elif rate >= 80 and unpredictability >= 75:
        style, nickname = "CHAOS CREATOR", "THE WALL BREAKER"
    elif average_force >= 300:
        style, nickname = "POWER SPECIALIST", "THE CANNON"
    elif unpredictability >= 80:
        style, nickname = "PLACEMENT ARTIST", "THE PUZZLE"
    elif curve >= 55 or chips > drives:
        style, nickname = "TRICK-SHOT MAKER", "THE CURVE ARCHITECT"
    else:
        style, nickname = "COOL FINISHER", "THE CONTENDER"

    conversion_competitors = [
        {"name": "YOU", "rate": rate, "detail": f"{goals}/{taken} GAME SAMPLE", "kind": "player"}
    ] + [
        {
            "name": pro["short"],
            "rate": pro["rate"],
            "detail": f"{pro['scored']}/{pro['scored'] + pro['missed']} CAREER PENS",
            "kind": "pro",
        }
        for pro in PRO_BENCHMARKS
    ]
    conversion_competitors.sort(key=lambda row: (-row["rate"], row["name"] != "YOU"))
    rank = next(i + 1 for i, row in enumerate(conversion_competitors) if row["name"] == "YOU")

    ronaldo = PRO_BENCHMARKS[0]["rate"]
    messi = PRO_BENCHMARKS[1]["rate"]
    if rate > ronaldo:
        comparison = f"This game sample finished {rate - ronaldo:.1f} points above Ronaldo's career penalty benchmark."
    elif rate > messi:
        comparison = f"This game sample split the legends: {rate - messi:.1f} above Messi, {ronaldo - rate:.1f} below Ronaldo."
    else:
        comparison = f"Next target: close the {messi - rate:.1f}-point gap to Messi's career penalty benchmark."

    performance_score = round(
        min(100, rate * 0.60 + min(100, average_force / 3.8) * 0.20 + unpredictability * 0.20)
    )
    grade = "S" if performance_score >= 88 else "A" if performance_score >= 76 else "B" if performance_score >= 62 else "C"

    return {
        "playerName": player_name.strip().upper()[:28] or "THE STRIKER",
        "nickname": nickname,
        "style": style,
        "grade": grade,
        "performanceScore": performance_score,
        "goals": goals,
        "taken": taken,
        "conversionRate": rate,
        "averageForce": average_force,
        "maxForce": max_force,
        "averagePower": average_power,
        "forceConsistency": consistency,
        "favoriteZone": favorite_zone,
        "favoriteZoneCode": favorite_code,
        "zones": zones,
        "unpredictability": unpredictability,
        "keeperFooled": fooled,
        "curveIndex": curve,
        "averageLaunchAngle": launch,
        "dominantFoot": dominant_foot,
        "drives": drives,
        "chips": chips,
        "highShots": high,
        "lowShots": low,
        "conversionRank": rank,
        "conversionField": len(conversion_competitors),
        "conversionComparison": comparison,
        "conversionTable": conversion_competitors,
        "proBenchmarks": [dict(pro) for pro in PRO_BENCHMARKS],
        "proSnapshotDate": PRO_SNAPSHOT_DATE,
        "shots": shots,
    }


def artwork_prompt(analytics: dict[str, Any]) -> str:
    goal_mood = "victorious goal explosion" if analytics["conversionRate"] >= 60 else "dramatic comeback energy"
    style = analytics["style"].lower()
    zone = analytics["favoriteZone"].lower()
    return (
        "Premium cinematic abstract football-stadium background for a vertical data visualization. "
        f"Visual story: {goal_mood}, {style}, favorite target is the {zone}. "
        "A futuristic metallic goalkeeper wall cracks apart under a blazing football, packed fans "
        "celebrate under gold and electric-cyan floodlights, dynamic energy trails and confetti. "
        "Architecture, the football, light and particles are the only foreground subjects; spectators "
        "stay tiny and indistinct in distant stands. "
        "Leave clean dark negative space through the center and lower half for a statistics overlay. "
        "Polished sports-broadcast photography and graphic-design lighting. Absolutely no words, "
        "letters, numbers, logos, watermarks, UI, scoreboards or readable signs."
    )


def render_report(
    analytics: dict[str, Any],
    artwork: bytes | None,
    ai_meta: dict[str, Any],
) -> bytes:
    width, height = REPORT_SIZE
    image = Image.new("RGB", REPORT_SIZE, "#07131C")
    draw = ImageDraw.Draw(image)
    for y in range(height):
        q = y / height
        draw.line((0, y, width, y), fill=(5 + int(5 * q), 14 + int(10 * q), 23 + int(15 * q)))

    hero = _decode_artwork(artwork) if artwork else _procedural_artwork((width, 760))
    hero = ImageOps.fit(hero, (width, 760), method=Image.Resampling.LANCZOS)
    hero = ImageEnhance.Color(hero).enhance(1.16)
    image.paste(hero, (0, 0))
    veil = Image.new("RGBA", (width, 760), (0, 0, 0, 0))
    veil_draw = ImageDraw.Draw(veil)
    for y in range(760):
        alpha = int(42 + 205 * (y / 760) ** 1.55)
        veil_draw.line((0, y, width, y), fill=(3, 10, 18, alpha))
    image.paste(veil.convert("RGB"), (0, 0), veil)
    draw = ImageDraw.Draw(image)

    amber = "#FFC400"
    cyan = "#3EC7F4"
    chalk = "#F4F7F1"
    muted = "#9DB4C6"
    panel = "#102536"
    line = "#29475D"

    draw.rounded_rectangle((56, 48, 354, 106), 16, fill=amber)
    draw.text((82, 63), "AI100 MATCH LAB", font=_font(29, bold=True), fill="#07131C")
    raw_source = str(ai_meta.get("source", "procedural")).lower()
    source = "AI100" if raw_source in {"ai100", "ai100-cache"} else "PROCEDURAL"
    draw.text((width - 56, 70), f"ARTWORK · {source}", font=_font(23, bold=True), fill=cyan, anchor="ra")
    draw.text((56, 146), "POST-MATCH", font=_font(82, black=True), fill=chalk)
    draw.text((56, 232), "SCOUTING REPORT", font=_font(82, black=True), fill=chalk)
    draw.text((58, 332), analytics["playerName"], font=_font(35, bold=True), fill=muted)
    draw.text((56, 385), analytics["nickname"], font=_font(66, black=True), fill=amber)
    draw.text((60, 466), analytics["style"], font=_font(28, bold=True), fill=cyan)

    grade_box = (1290, 160, 1518, 422)
    draw.rounded_rectangle(grade_box, 28, fill=(7, 19, 28, 220), outline=amber, width=5)
    draw.text((1404, 203), "MATCH GRADE", font=_font(22, bold=True), fill=muted, anchor="ma")
    draw.text((1404, 314), analytics["grade"], font=_font(126, black=True), fill=amber, anchor="mm")
    draw.text((1404, 392), f"{analytics['performanceScore']} / 100", font=_font(25, bold=True), fill=chalk, anchor="ma")

    y = 650
    metric_width = 464
    gap = 28
    _metric_card(draw, (56, y, 56 + metric_width, y + 224), f"{analytics['goals']} / {analytics['taken']}", "GOALS", amber, panel, line)
    _metric_card(draw, (56 + metric_width + gap, y, 56 + metric_width * 2 + gap, y + 224), f"{analytics['conversionRate']:.0f}%", "CONVERSION", cyan, panel, line)
    _metric_card(draw, (56 + (metric_width + gap) * 2, y, 56 + metric_width * 3 + gap * 2, y + 224), f"{analytics['maxForce']} N", "THUNDERFOOT", "#FF6B45", panel, line)

    race_y = 914
    draw.rounded_rectangle((56, race_y, 1544, race_y + 430), 26, fill=panel, outline=line, width=2)
    draw.text((94, race_y + 36), "THE PENALTY BENCHMARK RACE", font=_font(34, black=True), fill=chalk)
    draw.text((1506, race_y + 42), f"GAME RANK  #{analytics['conversionRank']} / {analytics['conversionField']}", font=_font(25, bold=True), fill=amber, anchor="ra")
    for index, row in enumerate(analytics["conversionTable"]):
        bar_y = race_y + 112 + index * 88
        color = amber if row["name"] == "YOU" else cyan if row["name"] == "RONALDO" else "#9B7CFF"
        draw.text((96, bar_y + 12), row["name"], font=_font(26, bold=True), fill=chalk)
        draw.rounded_rectangle((350, bar_y, 1290, bar_y + 40), 20, fill="#071722")
        draw.rounded_rectangle((350, bar_y, 350 + int(940 * row["rate"] / 100), bar_y + 40), 20, fill=color)
        draw.text((1320, bar_y + 20), f"{row['rate']:.1f}%", font=_font(27, black=True), fill=color, anchor="lm")
        draw.text((350, bar_y + 52), row["detail"], font=_font(18, bold=True), fill=muted)
    _wrapped_text(draw, analytics["conversionComparison"], (94, race_y + 388), 1320, _font(20, bold=True), muted, 27)

    insight_y = 1380
    cards = [
        (f"{analytics['averageForce']} N", "AVERAGE FORCE", f"{analytics['forceConsistency']}% consistent · {analytics['averagePower']}% power"),
        (analytics["favoriteZone"], "FAVORITE TARGET", f"{analytics['unpredictability']}% unpredictability"),
        (f"{analytics['keeperFooled']} / {analytics['taken']}", "KEEPER WRONG-FOOTED", "shot zone differed from the dive"),
        (f"{analytics['curveIndex']}%", "CURVE INDEX", f"avg launch angle {analytics['averageLaunchAngle']:+d}°"),
    ]
    card_w, card_h = 718, 190
    for index, (value, label, detail) in enumerate(cards):
        col, row = index % 2, index // 2
        x0 = 56 + col * (card_w + 52)
        y0 = insight_y + row * (card_h + 28)
        draw.rounded_rectangle((x0, y0, x0 + card_w, y0 + card_h), 22, fill="#0D2130", outline=line, width=2)
        draw.text((x0 + 30, y0 + 28), label, font=_font(22, bold=True), fill=cyan)
        draw.text((x0 + 30, y0 + 70), str(value)[:24], font=_font(43, black=True), fill=chalk)
        draw.text((x0 + 30, y0 + 135), detail, font=_font(20, bold=True), fill=muted)

    shots_y = 1834
    draw.text((56, shots_y), "YOUR FIVE-KICK DNA", font=_font(30, black=True), fill=chalk)
    draw.text((1544, shots_y + 5), f"{analytics['dominantFoot']} FOOT · {analytics['drives']} DRIVES · {analytics['chips']} CHIPS · {analytics['highShots']} HIGH / {analytics['lowShots']} LOW", font=_font(19, bold=True), fill=muted, anchor="ra")
    for index in range(analytics["taken"]):
        shot = analytics["shots"][index] if index < len(analytics["shots"]) else {}
        x0 = 56 + index * 296
        color = amber if shot.get("result") == "goal" else cyan if shot.get("result") == "save" else "#FF6B45"
        draw.rounded_rectangle((x0, shots_y + 62, x0 + 268, shots_y + 170), 18, fill="#0B1D2A", outline=color, width=3)
        draw.text((x0 + 20, shots_y + 83), f"KICK {index + 1}", font=_font(18, bold=True), fill=muted)
        result = str(shot.get("result") or "MISS").upper()
        draw.text((x0 + 20, shots_y + 112), result, font=_font(28, black=True), fill=color)
        detail = f"{shot.get('zone') or '-'} · {int(shot.get('force') or 0)} N"
        draw.text((x0 + 244, shots_y + 126), detail, font=_font(19, bold=True), fill=chalk, anchor="ra")

    footer_y = 2072
    draw.line((56, footer_y, 1544, footer_y), fill=line, width=2)
    draw.text((56, footer_y + 25), "FOR FUN · NOT A PROFESSIONAL SCOUTING ASSESSMENT", font=_font(20, black=True), fill=amber)
    draw.text((56, footer_y + 61), "Your result is a short game sample. Pro rates are career penalty records and are not equivalent measurements.", font=_font(18, bold=True), fill=muted)
    draw.text((56, footer_y + 94), f"Pro snapshot: {PRO_SNAPSHOT_DATE} · Transfermarkt · AI100 supplies artwork; laptop code supplies every statistic.", font=_font(18, bold=True), fill=muted)
    draw.text((1544, footer_y + 92), "SNAPDRAGON MULTIVERSE", font=_font(21, black=True), fill=cyan, anchor="ra")

    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()


def png_to_pdf(png: bytes) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    output = io.BytesIO()
    image.save(output, "PDF", resolution=150.0, quality=92)
    return output.getvalue()


class ReportStore:
    def __init__(self) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        self.artwork = AI100ArtworkClient()

    async def create(
        self,
        shotmap: list[dict[str, Any]],
        kicks_total: int,
        player_name: str = "THE STRIKER",
    ) -> dict[str, Any]:
        self.cleanup()
        analytics = analyze_match(shotmap, kicks_total, player_name)
        artwork, ai_meta = await self.artwork.generate(artwork_prompt(analytics))
        png = await asyncio.to_thread(render_report, analytics, artwork, ai_meta)
        pdf = await asyncio.to_thread(png_to_pdf, png)
        token = secrets.token_urlsafe(18)
        created = int(time.time())
        (DATA / f"{token}.png").write_bytes(png)
        (DATA / f"{token}.pdf").write_bytes(pdf)
        metadata = {
            "token": token,
            "createdAt": created,
            "expiresAt": created + REPORT_TTL_SECONDS,
            "analytics": analytics,
            "ai": ai_meta,
        }
        (DATA / f"{token}.json").write_text(json.dumps(metadata), encoding="utf-8")
        return {
            "status": "ready",
            "token": token,
            "createdAt": created,
            "expiresAt": created + REPORT_TTL_SECONDS,
            "landingUrl": f"/report/{token}",
            "pngUrl": f"/report/{token}.png",
            "pdfUrl": f"/report/{token}.pdf",
            "qrUrl": f"/report/{token}/qr.png",
            "preview": {
                "nickname": analytics["nickname"],
                "grade": analytics["grade"],
                "conversionRate": analytics["conversionRate"],
                "maxForce": analytics["maxForce"],
                "conversionRank": analytics["conversionRank"],
            },
            "ai": ai_meta,
        }

    def metadata(self, token: str) -> dict[str, Any] | None:
        if not _valid_token(token):
            return None
        path = DATA / f"{token}.json"
        if not path.is_file():
            return None
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if int(meta.get("expiresAt", 0)) <= int(time.time()):
            return None
        return meta

    def asset(self, token: str, extension: str) -> Path | None:
        if extension not in {"png", "pdf"} or self.metadata(token) is None:
            return None
        path = DATA / f"{token}.{extension}"
        return path if path.is_file() else None

    def cleanup(self) -> None:
        now = int(time.time())
        for meta_path in DATA.glob("*.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
            if int(meta.get("expiresAt", 0)) > now:
                continue
            token = meta_path.stem
            for suffix in ("json", "png", "pdf"):
                (DATA / f"{token}.{suffix}").unlink(missing_ok=True)


def qr_png(url: str) -> bytes:
    qr = qrcode.QRCode(version=None, box_size=9, border=3, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#07131C", back_color="#F4F7F1").convert("RGB")
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()


def sample_shotmap() -> list[dict[str, Any]]:
    """A deterministic five-kick fixture that exercises every report metric."""
    return [
        {"kick": 1, "zone": "L", "keeperZone": "C", "power": 0.91, "force": 346, "dirDeg": 18, "height": "H", "spin": -0.62, "strike": "drive", "foot": "R", "result": "goal"},
        {"kick": 2, "zone": "R", "keeperZone": "L", "power": 0.78, "force": 298, "dirDeg": 9, "height": "L", "spin": 0.48, "strike": "drive", "foot": "R", "result": "goal"},
        {"kick": 3, "zone": "C", "keeperZone": "C", "power": 0.68, "force": 258, "dirDeg": 25, "height": "H", "spin": 0.12, "strike": "chip", "foot": "L", "result": "save"},
        {"kick": 4, "zone": "L", "keeperZone": "R", "power": 0.96, "force": 372, "dirDeg": 14, "height": "L", "spin": -0.71, "strike": "drive", "foot": "R", "result": "goal"},
        {"kick": 5, "zone": "R", "keeperZone": "R", "power": 0.87, "force": 331, "dirDeg": 21, "height": "H", "spin": 0.55, "strike": "drive", "foot": "R", "result": "goal"},
    ]


def _valid_token(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{20,40}", token or ""))


def _decode_artwork(raw: bytes) -> Image.Image:
    try:
        return ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    except Exception:
        return _procedural_artwork((1600, 760))


def _procedural_artwork(size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, "#0B1D2A")
    draw = ImageDraw.Draw(image)
    for y in range(height):
        q = y / height
        draw.line((0, y, width, y), fill=(6 + int(18 * q), 20 + int(24 * q), 33 + int(18 * q)))
    for tier in range(5):
        y0 = 160 + tier * 82
        draw.rectangle((0, y0, width, y0 + 58), fill=(9 + tier * 4, 29 + tier * 5, 43 + tier * 5))
        for x in range(18, width, 34):
            color = "#FFC400" if (x // 34 + tier) % 5 == 0 else "#3EC7F4" if (x // 34) % 7 == 0 else "#8399A9"
            draw.ellipse((x, y0 + 16, x + 9, y0 + 25), fill=color)
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((width * 0.22, -height * 0.5, width * 0.78, height * 0.7), fill=(255, 196, 0, 96))
    glow = glow.filter(ImageFilter.GaussianBlur(70))
    image.paste(glow.convert("RGB"), (0, 0), glow)
    return image


def _metric_card(draw, box, value, label, color, panel, line) -> None:
    draw.rounded_rectangle(box, 24, fill=panel, outline=line, width=2)
    x0, y0, x1, _ = box
    draw.text(((x0 + x1) / 2, y0 + 84), value, font=_font(58, black=True), fill=color, anchor="mm")
    draw.text(((x0 + x1) / 2, y0 + 170), label, font=_font(23, bold=True), fill="#9DB4C6", anchor="mm")


def _wrapped_text(draw, text, xy, max_width, font, fill, line_height) -> None:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    x, y = xy
    for line_text in lines[:2]:
        draw.text((x, y), line_text, font=font, fill=fill)
        y += line_height


def _font(size: int, *, bold: bool = False, black: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if black:
        candidates += [Path("C:/Windows/Fonts/ariblk.ttf")]
    if bold or black:
        candidates += [Path("C:/Windows/Fonts/arialbd.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]
    candidates += [Path("C:/Windows/Fonts/arial.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    for candidate in candidates:
        try:
            return ImageFont.truetype(str(candidate), size)
        except OSError:
            continue
    return ImageFont.load_default()
