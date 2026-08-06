"""Golden tv.html functional contract — extract + verify candidates."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
GOLDEN = ROOT / "public" / "tv.html"
CONTRACT_PATH = ROOT / "public" / "scenes" / "contract.json"

# Required for match loop / HUD / reports / generation UI.
REQUIRED_IDS = [
    "pitch",
    "sYou",
    "dYou",
    "midTop",
    "midBot",
    "sWall",
    "dWall",
    "big",
    "sub",
    "reportLine",
    "startBtn",
    "reportPanel",
    "reportTitle",
    "reportStatus",
    "reportQr",
    "reportStats",
    "reportConversion",
    "reportForce",
    "reportOpen",
    "genBarWrap",
    "genBar",
    "genMeta",
    "desk",
    "deskName",
    "line",
    "ledP",
    "deskBadge",
    "fxBadge",
    "sceneBadge",
    "levelBadge",
    "soundBtn",
    "abortBtn",
]

REQUIRED_SYMBOLS = [
    "WebSocket(",
    "/ws",
    'type:"hello"',
    'client:"tv"',
    "function onState",
    "function applyScene",
    "function connect",
    '"again"',
    '"start"',
    'type:"abort"',
]

BANNED_PATTERNS = [
    (r"(?i)<script[^>]+src=[\"']https?://", "external_script_src"),
    (r"(?i)javascript:", "javascript_url"),
    (r"(?i)\beval\s*\(", "eval_call"),
]

MAX_BYTES = 2_500_000
MIN_BYTES = 20_000

_ID_RE = re.compile(r"""\bid\s*=\s*["']([^"']+)["']""", re.I)


def extract_ids(html: str) -> set[str]:
    return set(_ID_RE.findall(html))


def fingerprint(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()[:16]


def build_contract(golden_path: Path | None = None) -> dict:
    path = golden_path or GOLDEN
    html = path.read_text(encoding="utf-8")
    ids = sorted(extract_ids(html))
    missing_req = [i for i in REQUIRED_IDS if i not in ids]
    contract = {
        "source": str(path.name),
        "required_ids": list(REQUIRED_IDS),
        "golden_ids": ids,
        "required_symbols": list(REQUIRED_SYMBOLS),
        "banned": [name for _, name in BANNED_PATTERNS],
        "max_bytes": MAX_BYTES,
        "min_bytes": MIN_BYTES,
        "golden_fingerprint": fingerprint(html),
        "golden_bytes": len(html.encode("utf-8")),
        "missing_required_in_golden": missing_req,
    }
    return contract


def save_contract(contract: dict | None = None) -> dict:
    c = contract or build_contract()
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_PATH.write_text(json.dumps(c, indent=2), encoding="utf-8")
    return c


def load_contract() -> dict:
    if CONTRACT_PATH.is_file():
        try:
            return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return save_contract()


def verify(html: str, contract: dict | None = None) -> dict:
    """Return {ok, errors[], missing_ids[], fingerprint, bytes}."""
    c = contract or load_contract()
    errors: list[str] = []
    raw = html.encode("utf-8", errors="replace")
    nbytes = len(raw)
    if nbytes < c.get("min_bytes", MIN_BYTES):
        errors.append(f"too_small:{nbytes}")
    if nbytes > c.get("max_bytes", MAX_BYTES):
        errors.append(f"too_large:{nbytes}")

    ids = extract_ids(html)
    req = c.get("required_ids") or REQUIRED_IDS
    missing = [i for i in req if i not in ids]
    if missing:
        errors.append("missing_ids:" + ",".join(missing[:12]))

    compact = re.sub(r"""["']""", "", html)
    for sym in c.get("required_symbols") or REQUIRED_SYMBOLS:
        alt = sym.replace('"', "'")
        core = re.sub(r"""["']""", "", sym)
        if sym not in html and alt not in html and core not in compact:
            errors.append(f"missing_symbol:{sym[:40]}")

    for pat, name in BANNED_PATTERNS:
        if re.search(pat, html):
            errors.append(f"banned:{name}")

    # Must keep canvas pitch + WS path
    if 'id="pitch"' not in html and "id='pitch'" not in html:
        if "pitch" not in missing:
            errors.append("missing_pitch")

    return {
        "ok": not errors,
        "errors": errors,
        "missing_ids": missing,
        "fingerprint": fingerprint(html),
        "bytes": nbytes,
    }


def verify_file(path: Path, contract: dict | None = None) -> dict:
    html = path.read_text(encoding="utf-8")
    result = verify(html, contract)
    result["path"] = str(path)
    return result
