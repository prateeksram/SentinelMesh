"""Server entrypoint and wiring — the hub between transports, registry,
telemetry, and the engine link. Four-pillar mainline edition.

The server owns devices, sessions, streams, and routing. It knows nothing
about players, scores, or campaigns (A§9.4): the pillar endpoints below
(`/scene/status`, `/hw/status`) serve the engine's *published* snapshot back
out — the server never interprets it.

FX (`neural_fx`) runs in a one-process worker pool: 518² splatting, NCHW
normalization, and ORT inference never touch the event loop. The ORT session
lives in the worker (sessions don't pickle) and is created lazily once.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from aiohttp import web

from . import capabilities
from .discovery import start_discovery
from .link import (Broadcast, DeviceInfo, DeviceState, Event, InProcLink,
                   TcpLink, now_us)
from .protocol.events import ack
from .protocol.messages import WS_STREAMS
from .registry import Registry, Session
from .snapshot import filter_state
from .telemetry import TelemetryStore
from .transport.base import Connection, Hub
from .transport.ws import make_ws_handler

LAPTOP = Path(__file__).resolve().parent.parent
PUBLIC = LAPTOP / "public"
sys.path.insert(0, str(LAPTOP))  # for neural_fx when run as `python -m server`

import neural_fx  # noqa: E402

FX_POOL: ProcessPoolExecutor | None = None


async def _fx_call(fn, *args):
    loop = asyncio.get_running_loop()
    if FX_POOL is None:                      # tests importing make_app directly
        return fn(*args)
    return await loop.run_in_executor(FX_POOL, fn, *args)


# ================================================================== hub =====
class ServerHub(Hub):
    def __init__(self, registry: Registry, telemetry: TelemetryStore, elink):
        self.registry = registry
        self.telemetry = telemetry
        self.elink = elink
        self.last_state: dict = {}           # engine's latest published snapshot
        self.fx_stats = {"last_ms": 0, "backend": None, "count": 0, "pending": 0}
        elink.set_broadcast_handler(self.on_engine_broadcast)

    # -------------------------------------------------- transport inbound --
    async def on_connect(self, conn: Connection) -> None:
        pass  # a socket is nobody until it says hello

    async def on_disconnect(self, conn: Connection) -> None:
        session, released = self.registry.disconnect(conn)
        if session and released:
            await self.elink.device_left(self._engine_id(session))
        # Resumable sessions enter DEGRADED instead: the slot is held through
        # the grace window and the engine is told nothing yet.

    async def on_message(self, conn: Connection, msg: dict) -> None:
        self.telemetry.msg_count += 1
        t = msg.get("type") or msg.get("t")

        if t == "hello":
            await self._on_hello(conn, msg)
            return

        session = self.registry.by_conn(conn)
        if session is None:
            return  # never said hello — receives and contributes nothing
        session.touch()

        if t == "hb":
            return
        if t == "telem":
            key, kind, label = self._telem_identity(session)
            self.telemetry.ingest(key, kind, label, msg)
            return
        if t in WS_STREAMS:  # continuous: fire-and-forget, latest-wins
            await self.elink.sample(DeviceState(
                device_id=self._engine_id(session),
                role=self._primary_role(session),
                stream=t, data=msg))
            return
        if t == "event":     # generic discrete event with reliability semantics
            event_id = msg.get("event_id")
            kind = msg.get("kind")
            if not isinstance(kind, str):
                return
            if isinstance(event_id, str):
                fresh = session.events.fresh(event_id)
                await conn.send_json(ack(event_id))   # ACK duplicates too —
                if not fresh:                          # their first ACK was lost
                    return
            data = msg.get("data") if isinstance(msg.get("data"), dict) else {
                k: v for k, v in msg.items()
                if k not in ("type", "t", "kind", "event_id")}
            await self.elink.event(Event(
                kind=kind, device_id=self._engine_id(session),
                role=self._primary_role(session), data=data,
                event_id=event_id))
            return
        if t in ("kick", "skel", "start", "again", "abort"):  # legacy events
            await self.elink.event(Event(
                kind=t, device_id=self._engine_id(session),
                role=self._primary_role(session), data=msg))
            return
        # unknown type: ignore, forward-compatible

    async def _on_hello(self, conn: Connection, msg: dict) -> None:
        t_rx = now_us()
        caps = capabilities.parse(msg)
        if caps is None:
            return
        session, _resumed = self.registry.register(conn, caps)
        if not caps.legacy:
            # WELCOME only for descriptor-bearing clients — legacy tv.html
            # pipes every message into onState and must never see one.
            await conn.send_json(capabilities.welcome(
                session.session_id, caps, t_rx, now_us()))
        if "dashboard" in session.roles:
            await conn.send_json(self.telemetry.snapshot())
            return
        if session.is_game_client:
            await self.elink.device_joined(DeviceInfo(
                device_id=self._engine_id(session),
                roles=tuple(session.caps.roles),
                device=session.caps.device))

    # ---------------------------------------------------- engine outbound --
    async def on_engine_broadcast(self, b: Broadcast) -> None:
        payload = b.payload
        if b.target == "telem":
            # Engine-side telemetry (desk/scene) — ingest, never fan out.
            self.telemetry.ingest("laptop", "laptop", "laptop", payload)
            return
        if payload.get("type") == "state" and b.target == "all":
            self.last_state = payload        # cache BEFORE fan-out: /scene, /hw
        if b.target == "all":
            targets = self.registry.active_game_sessions()
        elif b.target.startswith("role:"):
            targets = self.registry.with_role(b.target[5:])
        else:
            targets = [s for s in self.registry.active_game_sessions()
                       if self._engine_id(s) == b.target]
        is_state = payload.get("type") == "state"
        for s in targets:
            if s.conn is None:
                continue
            out = filter_state(payload, s) if is_state else payload
            if is_state:
                js = json.dumps(out)
                if js == s.last_state_sent:   # per-session identical-payload skip
                    continue
                s.last_state_sent = js
            await s.conn.send_json(out)

    # -------------------------------------------------------------- ident --
    @staticmethod
    def _engine_id(session: Session) -> str:
        return session.device_id or f"anon-{session.session_id:08x}"

    @staticmethod
    def _primary_role(session: Session) -> str:
        for r in ("phone", "keeper_input", "tv", "display"):
            if r in session.roles:
                return r
        return next(iter(session.roles), "unknown")

    def _telem_identity(self, session: Session) -> tuple[str, str, str]:
        kind = session.caps.device
        if kind in ("laptop", "phone", "unoq"):
            key = kind
        else:
            key = self._engine_id(session)
        label = f"{kind} ({(session.device_id or 'legacy')[:8]})"
        return key, kind, label

    # -------------------------------------------------- background loops ---
    async def lifecycle_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            for session in self.registry.tick():
                await self.elink.device_left(self._engine_id(session))

    async def telem_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            self.telemetry.self_report()
            # FX worker stats land on the unit that actually did the work —
            # qnn → npu, cpu/procedural → cpu. A lit NPU cell backed by CPU is
            # the failure mode the honesty rule exists to prevent.
            if self.fx_stats["count"]:
                backend = self.fx_stats["backend"] or "procedural"
                unit = "npu" if backend == "qnn" else "cpu"
                self.telemetry.ingest("laptop", "laptop", "laptop", {
                    "unit": unit, "source": "fx",
                    "busy_pct": 0,
                    "metric": {"fx_plate_ms": self.fx_stats["last_ms"],
                               "fx_plates": self.fx_stats["count"],
                               "fx_queue": self.fx_stats["pending"]},
                    "state": f"fx:{backend}"})
            snap = self.telemetry.snapshot()
            for s in self.registry.with_role("dashboard"):
                if s.conn:
                    await s.conn.send_json(snap)


HUB: ServerHub | None = None


# ================================================================= http =====
async def fx_status(_request):
    return web.json_response(await _fx_call(neural_fx.status))


async def fx_hero(request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if HUB:
        HUB.fx_stats["pending"] += 1
    t0 = time.perf_counter()
    out = await _fx_call(neural_fx.hero, payload)
    if HUB:
        HUB.fx_stats["pending"] -= 1
        HUB.fx_stats["count"] += 1
        HUB.fx_stats["last_ms"] = int((time.perf_counter() - t0) * 1000)
        HUB.fx_stats["backend"] = out.get("backend")
    return web.json_response(out)


async def scene_status(_request):
    st = HUB.last_state if HUB else {}
    return web.json_response({
        "level": st.get("level"),
        "progress": st.get("genProgress"),
        "metrics": st.get("sceneMetrics"),
        "phase": st.get("phase"),
    })


async def hw_status(_request):
    st = HUB.last_state if HUB else {}
    return web.json_response({
        "desk": st.get("llm"),
        "fx": await _fx_call(neural_fx.status),
        # Env-derived, same defaults as the engine's geniex client. The server
        # reports the address; it never talks to GenieX itself.
        "geniex_url": os.environ.get("GF_GENIEX_URL", "http://127.0.0.1:18181/v1"),
        "model": os.environ.get("GF_GENIEX_MODEL",
                                "qualcomm/Qwen3-4B-Instruct-2507:W4A16"),
        "ai100_report": ({
            "configured": AI100_STORE.artwork.settings.configured,
            "model": AI100_STORE.artwork.settings.model,
        } if AI100_STORE else {"configured": False, "model": None}),
    })


class _ReportGameAdapter:
    """Duck-types the old monolith `game` for ai100.web.ReportWeb across the
    process boundary: report status comes from the engine's published
    snapshot; simulation is forwarded to the engine as a discrete event. The
    server never computes a report — assets are read from the shared on-disk
    ReportStore, which works across processes."""

    def __init__(self, hub: ServerHub):
        self.hub = hub

    @property
    def report_card(self):
        return (self.hub.last_state or {}).get("postGameReport")

    def queue_report(self, shots, kicks_total, *, player_name="DEMO STRIKER",
                     require_end=False, generation=None):
        asyncio.create_task(self.hub.elink.event(Event(
            kind="report_sim", device_id="server", role="server",
            data={"shots": shots, "kicks_total": kicks_total,
                  "player_name": player_name})))

    async def broadcast(self):
        pass  # the engine broadcasts when the card lands


AI100_STORE = None


def make_app(hub: ServerHub) -> web.Application:
    global AI100_STORE
    app = web.Application(client_max_size=8 * 1024 * 1024)
    app.router.add_get("/ws", make_ws_handler(hub))
    app.router.add_get("/fx/status", fx_status)
    app.router.add_post("/fx/hero", fx_hero)
    app.router.add_get("/scene/status", scene_status)
    app.router.add_get("/hw/status", hw_status)
    app.router.add_get("/", lambda r: web.HTTPFound("/tv.html"))
    app.router.add_get("/telemetry", lambda r: web.HTTPFound("/telemetry.html"))
    # ai100 post-match report endpoints (a71a0af) — optional: missing deps
    # must not take the game down.
    try:
        repo_root = str(LAPTOP.parent)
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from ai100 import report_engine as _report_engine
        from ai100.web import ReportWeb
        AI100_STORE = _report_engine.ReportStore()
        ReportWeb(_ReportGameAdapter(hub), AI100_STORE,
                  int(os.environ.get("GF_KICKS", 5))).register(app)
    except Exception as e:
        print(f"[ai100] report endpoints disabled ({e})")
    app.router.add_static("/", PUBLIC, show_index=True)
    return app


# ================================================================= main =====
async def main(link_mode: str | None = None, http_port: int = 8080) -> None:
    global FX_POOL, HUB
    link_mode = link_mode or os.environ.get("GF_LINK", "inproc")
    FX_POOL = ProcessPoolExecutor(max_workers=1)
    asyncio.get_running_loop().run_in_executor(FX_POOL, neural_fx.status)  # warm

    registry = Registry()
    telemetry = TelemetryStore()

    if link_mode == "tcp":
        engine_port = int(os.environ.get("GF_ENGINE_PORT", 8899))
        elink = TcpLink(port=engine_port)
    else:
        elink = InProcLink()

    hub = ServerHub(registry, telemetry, elink)
    HUB = hub
    await elink.start()

    if link_mode == "tcp":
        print(f"Link  :  tcp — start the engine with `python -m engine` "
              f"(port {elink.port})")
    else:
        # Engine runs as a task in this loop: one process on demo day (A§9.3).
        from engine.link import InProcEngineLink
        from engine.match import Engine
        engine = Engine(InProcEngineLink(elink.to_engine, elink.from_engine))
        asyncio.create_task(engine.run())
        await asyncio.sleep(0.1)             # let the first broadcast cache
        print("Link  :  inproc — engine embedded in this process")

    app = make_app(hub)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", http_port).start()
    print(f"HTTP  :  http://0.0.0.0:{http_port}   (tv.html · phone.html · telemetry.html)")

    cert, key = LAPTOP / "cert.pem", LAPTOP / "key.pem"
    if cert.exists() and key.exists():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        await web.TCPSite(runner, "0.0.0.0", 8443, ssl_context=ctx).start()
        print("HTTPS :  https://0.0.0.0:8443  (use this for the phone camera)")
    else:
        print("No cert.pem/key.pem — HTTPS off. Phone cameras off-localhost need it:")
        print('  openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 -subj "/CN=gesture-football"')

    await start_discovery(ws_port=http_port)

    asyncio.create_task(hub.lifecycle_loop())
    asyncio.create_task(hub.telem_loop())

    fx = await _fx_call(neural_fx.status)
    print(f"Desk  =  {hub.last_state.get('llm') or 'engine not up yet'}")
    print(f"FX    =  {fx.get('backend', '?').upper()} · {fx.get('model') or 'procedural'}")
    print("Scene =  engine-managed (GET /scene/status)")
    await asyncio.Event().wait()


def run() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Gesture Football device server")
    p.add_argument("--link", choices=("inproc", "tcp"),
                   default=os.environ.get("GF_LINK", "inproc"))
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args()
    try:
        asyncio.run(main(link_mode=args.link, http_port=args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
