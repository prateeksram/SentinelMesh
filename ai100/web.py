"""HTTP endpoints for AI100-generated post-match reports.

Keeping these handlers here makes the feature portable: the laptop game only
registers this small adapter and queues a report when a match ends.
"""

from __future__ import annotations

import html
import os
import socket

from aiohttp import web

from . import report_engine


class ReportWeb:
    """Expose report status, assets, download landing page, and simulation."""

    def __init__(self, game, store: report_engine.ReportStore, kicks_total: int):
        self.game = game
        self.store = store
        self.kicks_total = kicks_total

    def register(self, app: web.Application) -> None:
        app.router.add_get("/api/report", self.status)
        app.router.add_post("/api/report/simulate", self.simulate)
        app.router.add_get("/report/{token}/qr.png", self.qr)
        app.router.add_get("/report/{token}.{extension:png|pdf}", self.asset)
        app.router.add_get("/report/{token}", self.landing)

    @staticmethod
    def _lan_ip() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("8.8.8.8", 80))
                return str(probe.getsockname()[0])
        except OSError:
            return "127.0.0.1"

    def _public_base(self, request: web.Request) -> str:
        configured = os.environ.get("GF_PUBLIC_BASE_URL", "").strip().rstrip("/")
        if configured:
            return configured
        host = request.host
        if host.startswith("localhost") or host.startswith("127.0.0.1"):
            default_port = "8443" if request.scheme == "https" else "8080"
            port = host.rsplit(":", 1)[1] if ":" in host else default_port
            host = f"{self._lan_ip()}:{port}"
        return f"{request.scheme}://{host}"

    async def status(self, _request: web.Request) -> web.Response:
        return web.json_response(self.game.report_card or {"status": "idle"})

    async def asset(self, request: web.Request) -> web.StreamResponse:
        token = request.match_info["token"]
        extension = request.match_info["extension"]
        path = self.store.asset(token, extension)
        if path is None:
            raise web.HTTPNotFound(text="Report expired or not found")
        content_type = "image/png" if extension == "png" else "application/pdf"
        disposition = "attachment" if request.query.get("download") == "1" else "inline"
        response = web.FileResponse(
            path,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": (
                    f'{disposition}; filename="gesture-football-report.{extension}"'
                ),
            },
        )
        response.content_type = content_type
        return response

    async def qr(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        if self.store.metadata(token) is None:
            raise web.HTTPNotFound(text="Report expired or not found")
        target = f"{self._public_base(request)}/report/{token}"
        return web.Response(
            body=report_engine.qr_png(target),
            content_type="image/png",
            headers={"Cache-Control": "no-store", "X-Report-URL": target},
        )

    async def landing(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        meta = self.store.metadata(token)
        if meta is None:
            raise web.HTTPNotFound(text="This report has expired")
        analytics = meta["analytics"]
        nickname = html.escape(str(analytics.get("nickname", "POST-MATCH REPORT")))
        rate = html.escape(str(analytics.get("conversionRate", 0)))
        force = html.escape(str(analytics.get("maxForce", 0)))
        body = _landing_html(token, nickname, rate, force)
        return web.Response(
            text=body,
            content_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    async def simulate(self, request: web.Request) -> web.Response:
        if (
            request.remote not in {"127.0.0.1", "::1", None}
            and os.environ.get("GF_ENABLE_REPORT_SIM") != "1"
        ):
            raise web.HTTPForbidden(text="Report simulation is local-only")
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        player_name = str(payload.get("playerName") or "DEMO STRIKER")[:28]
        shots = payload.get("shotmap")
        if not isinstance(shots, list):
            shots = report_engine.sample_shotmap()
        self.game.queue_report(
            shots,
            len(shots) or self.kicks_total,
            player_name=player_name,
            require_end=False,
        )
        await self.game.broadcast()
        return web.json_response(self.game.report_card, status=202)


def _landing_html(token: str, nickname: str, rate: str, force: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{nickname} - Gesture Football</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#06111a;color:#f4f7f1;font-family:system-ui,sans-serif}}
main{{width:min(720px,100%);margin:auto;padding:24px}}.tag{{color:#3ec7f4;font-weight:800;letter-spacing:2px}}
h1{{margin:8px 0 4px;font-size:clamp(32px,9vw,56px)}}p{{color:#9db4c6}}img{{display:block;width:100%;border-radius:18px;border:1px solid #29475d;box-shadow:0 22px 60px #0008}}
.stats{{display:flex;gap:12px;margin:18px 0}}.stats b{{flex:1;background:#102536;padding:15px;border-radius:12px;text-align:center;color:#ffc400}}
.buttons{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:18px 0}}a{{display:block;text-align:center;text-decoration:none;font-weight:900;padding:16px;border-radius:12px;background:#ffc400;color:#07131c}}a+a{{background:#3ec7f4}}
small{{display:block;color:#7890a2;line-height:1.5;margin:16px 0 40px}}
</style></head><body><main><div class="tag">SNAPDRAGON MULTIVERSE - AI100 MATCH LAB</div>
<h1>{nickname}</h1><p>Your private post-match scouting card is ready.</p>
<div class="stats"><b>{rate}% conversion</b><b>{force} N max force</b></div>
<img src="/report/{token}.png" alt="Gesture Football post-match report">
<div class="buttons"><a href="/report/{token}.png?download=1">Download PNG</a><a href="/report/{token}.pdf?download=1">Download PDF</a></div>
<small>For fun, not a professional scouting assessment. The game sample and career penalty benchmarks are different sample sizes. Report assets expire after 30 minutes.</small>
</main></body></html>"""
