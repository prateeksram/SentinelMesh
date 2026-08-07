"""OpenAI cloud chat helper (scene-gen fallback when GenieX fails)."""

from __future__ import annotations

import json
import os
from aiohttp import ClientSession, ClientTimeout

def _base_url() -> str:
    return os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def scene_model() -> str:
    return (
        os.environ.get("OPENAI_SCENE_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or "gpt-4o-mini"
    )


# Back-compat alias (lazy via property-like function calls in callers).
OPENAI_MODEL = "gpt-4o-mini"  # overwritten by refresh(); prefer scene_model()


def image_model() -> str:
    return os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")


def _api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip()


def configured() -> bool:
    key = _api_key()
    return bool(key) and not key.startswith("YOUR_") and key.startswith("sk-")


def refresh() -> None:
    """Re-read env after load_repo_env()."""
    global OPENAI_MODEL
    OPENAI_MODEL = scene_model()

def _extract(data: dict) -> str:
    ch = data.get("choices")
    if isinstance(ch, list) and ch:
        msg = ch[0].get("message") or {}
        if msg.get("content"):
            return str(msg["content"]).strip()
        if ch[0].get("text"):
            return str(ch[0]["text"]).strip()
    return ""


async def chat(
    system: str,
    user: str,
    *,
    max_tokens: int = 400,
    temperature: float = 0.4,
    timeout: float = 45.0,
    model: str | None = None,
) -> str | None:
    """OpenAI /v1/chat/completions. Returns text or None."""
    key = _api_key()
    if not configured():
        return None
    body = {
        "model": model or scene_model(),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    ep = _base_url() + "/chat/completions"
    try:
        async with ClientSession(timeout=ClientTimeout(total=timeout)) as s:
            async with s.post(
                ep,
                json=body,
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {key}",
                },
            ) as r:
                if r.status != 200:
                    err = (await r.text())[:240]
                    print(f"[openai] HTTP {r.status}: {err}", flush=True)
                    return None
                return _extract(await r.json()) or None
    except Exception as exc:
        print(f"[openai] chat failed ({type(exc).__name__}: {exc})", flush=True)
        return None


async def chat_json(
    system: str,
    user: str,
    *,
    max_tokens: int = 400,
    timeout: float = 45.0,
    model: str | None = None,
) -> dict | None:
    """Expects strict JSON back. Strips fences, parses, returns dict or None."""
    txt = await chat(
        system,
        user,
        max_tokens=max_tokens,
        temperature=0.3,
        timeout=timeout,
        model=model,
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
