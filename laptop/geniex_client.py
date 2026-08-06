"""Shared async GenieX client (Desk + SceneEngine).

Sends OpenAI-style /v1/chat/completions bodies; auto-detects OpenAI or
Anthropic response shapes so either GenieX wire format works.

Circuit breaker (system-plan §5.3): 3 consecutive failures open the breaker
for 60 s — calls return None immediately instead of paying the full timeout
while GenieX is down. After 60 s the next call is the half-open probe; one
success closes the breaker.
"""

from __future__ import annotations

import json
import os
import time

from aiohttp import ClientSession, ClientTimeout

GENIEX_URL = os.environ.get("GF_GENIEX_URL", "http://127.0.0.1:18181/v1")
GENIEX_MODEL = os.environ.get(
    "GF_GENIEX_MODEL", "qualcomm/Qwen3-4B-Instruct-2507:W4A16"
)

BREAKER_TRIP = 3        # consecutive failures that open the breaker
BREAKER_HOLD_S = 60.0   # open duration before the half-open probe

_fails = 0
_open_until = 0.0


def breaker_state() -> dict:
    now = time.monotonic()
    return {
        "state": "open" if _open_until > now else "closed",
        "fails": _fails,
        "retry_in_s": max(0.0, round(_open_until - now, 1)),
    }


def _breaker_allows() -> bool:
    return time.monotonic() >= _open_until


def _record(ok: bool) -> None:
    global _fails, _open_until
    if ok:
        _fails = 0
        _open_until = 0.0
    else:
        _fails += 1
        if _fails >= BREAKER_TRIP:
            _open_until = time.monotonic() + BREAKER_HOLD_S


def _extract(data: dict) -> str:
    # OpenAI: choices[].message.content
    ch = data.get("choices")
    if isinstance(ch, list) and ch:
        msg = ch[0].get("message") or {}
        if msg.get("content"):
            return msg["content"].strip()
        if ch[0].get("text"):
            return ch[0]["text"].strip()  # legacy completions
    # Anthropic: content[].text
    c = data.get("content")
    if isinstance(c, list):
        t = " ".join(
            b.get("text", "") for b in c if b.get("type") == "text"
        ).strip()
        if t:
            return t
    return ""


def _tokens(data: dict) -> int | None:
    """Completion-token count from either usage shape."""
    u = data.get("usage")
    if not isinstance(u, dict):
        return None
    for k in ("completion_tokens", "output_tokens"):
        if isinstance(u.get(k), int):
            return u[k]
    return None


async def chat_with_meta(
    system,
    user,
    *,
    max_tokens=110,
    temperature=0.7,
    timeout=8.0,
    model=None,
    url=None,
) -> tuple[str | None, dict]:
    """One-shot chat. Returns (text|None, meta) where meta carries
    total_ms / tokens / tok_per_s from the response's usage block.
    No streaming → no true TTFT; it is intentionally not fabricated."""
    meta = {"total_ms": 0, "tokens": None, "tok_per_s": None,
            "breaker": breaker_state()["state"]}
    if not _breaker_allows():
        return None, meta
    body = {
        "model": model or GENIEX_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    base = (url or GENIEX_URL).rstrip("/")
    ep = base + ("/chat/completions" if base.endswith("/v1") else "")
    t0 = time.perf_counter()
    try:
        async with ClientSession(timeout=ClientTimeout(total=timeout)) as s:
            async with s.post(
                ep, json=body, headers={"content-type": "application/json"}
            ) as r:
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}")
                data = await r.json()
        text = _extract(data) or None
        meta["total_ms"] = int((time.perf_counter() - t0) * 1000)
        meta["tokens"] = _tokens(data)
        if meta["tokens"] and meta["total_ms"]:
            meta["tok_per_s"] = round(meta["tokens"] / (meta["total_ms"] / 1000), 1)
        _record(text is not None)
        return text, meta
    except Exception:
        meta["total_ms"] = int((time.perf_counter() - t0) * 1000)
        _record(False)
        return None, meta


async def chat(system, user, **kw) -> str | None:
    text, _ = await chat_with_meta(system, user, **kw)
    return text


async def chat_json_meta(
    system, user, *, max_tokens=900, timeout=90.0, model=None, url=None
) -> tuple[dict | None, dict]:
    """Expects strict JSON back. Strips fences, parses. (dict|None, meta)."""
    txt, meta = await chat_with_meta(
        system,
        user,
        max_tokens=max_tokens,
        temperature=0.4,
        timeout=timeout,
        model=model,
        url=url,
    )
    if not txt:
        return None, meta
    txt = (
        txt.strip()
        .removeprefix("```json")
        .removeprefix("```")
        .removesuffix("```")
        .strip()
    )
    try:
        return json.loads(txt), meta
    except json.JSONDecodeError:
        i, j = txt.find("{"), txt.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(txt[i : j + 1]), meta
            except json.JSONDecodeError:
                return None, meta
    return None, meta


async def chat_json(system, user, **kw) -> dict | None:
    data, _ = await chat_json_meta(system, user, **kw)
    return data


async def ping(timeout=15.0) -> bool:
    """Startup probe. Bypasses the open-breaker gate (it IS the probe) but
    records its result so a success closes the breaker."""
    global _open_until
    _open_until = 0.0
    return (await chat("ok", "ping", max_tokens=1, timeout=timeout)) is not None
