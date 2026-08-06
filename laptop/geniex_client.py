"""Shared async GenieX client (Desk + SceneEngine).

Sends OpenAI-style /v1/chat/completions bodies; auto-detects OpenAI or
Anthropic response shapes so either GenieX wire format works.
"""

from __future__ import annotations

import json
import os

from aiohttp import ClientSession, ClientTimeout

GENIEX_URL = os.environ.get("GF_GENIEX_URL", "http://127.0.0.1:18181/v1")
GENIEX_MODEL = os.environ.get(
    "GF_GENIEX_MODEL", "ai-hub-models/Qwen3-4B-Instruct-2507"
)


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


async def chat(
    system,
    user,
    *,
    max_tokens=110,
    temperature=0.7,
    timeout=8.0,
    model=None,
    url=None,
) -> str | None:
    """One-shot chat. OpenAI-style body; _extract handles either response shape."""
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
    try:
        async with ClientSession(timeout=ClientTimeout(total=timeout)) as s:
            async with s.post(
                ep, json=body, headers={"content-type": "application/json"}
            ) as r:
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}")
                return _extract(await r.json()) or None
    except Exception:
        return None


async def chat_json(
    system, user, *, max_tokens=900, timeout=90.0, model=None, url=None
) -> dict | None:
    """Expects strict JSON back. Strips fences, parses, returns dict or None."""
    txt = await chat(
        system,
        user,
        max_tokens=max_tokens,
        temperature=0.4,
        timeout=timeout,
        model=model,
        url=url,
    )
    if not txt:
        return None
    txt = (
        txt.strip()
        .removeprefix("```json")
        .removeprefix("```")
        .removesuffix("```")
        .strip()
    )
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        i, j = txt.find("{"), txt.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(txt[i : j + 1])
            except json.JSONDecodeError:
                return None
    return None


async def ping(timeout=2.0) -> bool:
    return (await chat("ok", "ping", max_tokens=1, timeout=timeout)) is not None
