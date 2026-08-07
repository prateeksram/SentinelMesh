"""Shared async GenieX client (Desk + SceneEngine).

Sends OpenAI-style /v1/chat/completions bodies; auto-detects OpenAI or
Anthropic response shapes so either GenieX wire format works.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import ClientSession, ClientTimeout

GENIEX_URL = os.environ.get("GF_GENIEX_URL", "http://127.0.0.1:18181/v1")
GENIEX_MODEL = os.environ.get(
    "GF_GENIEX_MODEL", "qualcomm/Qwen3-4B-Instruct-2507:W4A16"
)
# Auto-start knobs used by ensure_serve() from server.py.
_GENIEX_ENABLED = os.environ.get("GF_GENIEX", "1").lower() not in ("0", "false", "no")
_AUTOSTART = os.environ.get("GF_GENIEX_AUTOSTART", "1").lower() not in (
    "0",
    "false",
    "no",
)
_COMPUTE = os.environ.get("GF_GENIEX_COMPUTE", "npu")
_READY_TIMEOUT_S = float(os.environ.get("GF_GENIEX_READY_TIMEOUT_S", "120"))
_LOG_PATH = Path(__file__).parent / "logs" / "geniex_serve.log"
_spawned_proc: subprocess.Popen | None = None


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


async def ping(timeout=15.0, url: str | None = None) -> bool:
    return (
        await chat("ok", "ping", max_tokens=1, timeout=timeout, url=url)
    ) is not None


def _models_url(url: str | None = None) -> str:
    base = (url or GENIEX_URL).rstrip("/")
    if base.endswith("/v1"):
        return base + "/models"
    return base + "/v1/models"


async def models_ready(timeout=3.0, url: str | None = None) -> bool:
    """True when the OpenAI-compatible /v1/models endpoint answers."""
    try:
        async with ClientSession(timeout=ClientTimeout(total=timeout)) as s:
            async with s.get(_models_url(url)) as r:
                return r.status == 200
    except Exception:
        return False


def _find_geniex() -> str | None:
    found = shutil.which("geniex")
    if found:
        return found
    local = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "GenieX CLI"
        / ("geniex.exe" if sys.platform == "win32" else "geniex")
    )
    if local.is_file():
        return str(local)
    return None


def _serve_host_flag(url: str | None = None) -> str:
    """Map GF_GENIEX_URL to geniex serve --host value (default 127.0.0.1:18181)."""
    parsed = urlparse((url or GENIEX_URL).rstrip("/"))
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 18181
    return f"{host}:{port}"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _spawn_serve() -> subprocess.Popen | None:
    """Launch `geniex serve` detached; returns the Popen or None on failure."""
    global _spawned_proc
    exe = _find_geniex()
    if not exe:
        _log("[geniex] CLI not found - install GenieX or put geniex on PATH")
        return None
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    args = [
        exe,
        "serve",
        "--skip-update",
        "--host",
        _serve_host_flag(),
        "-c",
        _COMPUTE,
    ]
    try:
        log_f = _LOG_PATH.open("a", encoding="utf-8")
        kwargs: dict = {
            "stdout": log_f,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            # Detach so Ctrl+C on server.py does not kill GenieX mid-match.
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
            kwargs["close_fds"] = True
        else:
            kwargs["start_new_session"] = True
        _log(f"[geniex] starting: {' '.join(args)}")
        _log(f"[geniex] log -> {_LOG_PATH}")
        _spawned_proc = subprocess.Popen(args, **kwargs)
        return _spawned_proc
    except OSError as exc:
        _log(f"[geniex] failed to spawn serve ({exc})")
        return None


async def _warm_npu(url: str | None = None) -> None:
    """Best-effort tiny chat so the first desk/scene call is less cold."""
    try:
        await ping(timeout=45.0, url=url)
    except Exception:
        pass


async def ensure_serve(
    *,
    timeout: float | None = None,
    url: str | None = None,
) -> bool:
    """Make sure GenieX is up. Starts `geniex serve` when needed.

    Ready = OpenAI `/v1/models` answers. A background chat warms the NPU.
    Honors GF_GENIEX=0 / GF_GENIEX_AUTOSTART=0. Never kills an existing serve.
    """
    if not _GENIEX_ENABLED:
        _log("[geniex] GF_GENIEX=0 - skipping")
        return False

    wait_s = _READY_TIMEOUT_S if timeout is None else float(timeout)

    async def _mark_ready(already: bool) -> bool:
        _log("[geniex] already up" if already else "[geniex] ready")
        asyncio.create_task(_warm_npu(url))
        return True

    if await models_ready(timeout=2.0, url=url):
        return await _mark_ready(already=True)

    if not _AUTOSTART:
        _log("[geniex] down and GF_GENIEX_AUTOSTART=0 - not starting")
        return False

    if _spawn_serve() is None:
        return False

    deadline = asyncio.get_running_loop().time() + wait_s
    while asyncio.get_running_loop().time() < deadline:
        if await models_ready(timeout=2.0, url=url):
            return await _mark_ready(already=False)
        proc = _spawned_proc
        if proc is not None and proc.poll() is not None:
            _log(f"[geniex] serve exited early (code {proc.returncode}); see {_LOG_PATH}")
            return False
        await asyncio.sleep(1.5)

    _log(f"[geniex] not ready after {wait_s:.0f}s - falling back to templates")
    return False
