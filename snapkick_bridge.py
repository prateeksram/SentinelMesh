#!/usr/bin/env python3
"""
snapkick_bridge — UNO Q pose pipeline → Gesture Football striker.

Listens for `snapkick.pose.v1` UDP packets (the UNO Q kick/trajectory
pipeline, or ball-game's snapkick_sim.py) and joins the match server as a
"unoq" striker over WebSocket, translating:

  * live pose aim (predicted_goal_x / shot_direction_deg) → {"type":"aim"}
  * gated kicks (kick_candidate + confidence + cooldown)  → {"type":"kick"}
    carrying zone/power/force plus the REAL metric impact (goalX/goalZ),
    apex and launch speed so the server can referee by geometry and the TV
    can fly the ball along the true trajectory.

Run:    python snapkick_bridge.py [--host 127.0.0.1:8080] [--udp-port 5005]
Test:   python ../..../ball-game/snapkick_sim.py   (schema-faithful sim)
"""

import argparse
import asyncio
import json
import math
import time

import aiohttp

# Must match the UNO Q calibration (same numbers as game_server.py / server.py).
GOAL_DIST_M = 11.0        # kick spot → goal plane (for direction-only aim)
GOAL_HALF_W_M = 3.66
ZONE_THIRD_M = GOAL_HALF_W_M / 3.0

SNAPKICK_MIN_CONF = 0.60  # min kick_confidence to accept a kick
KICK_COOLDOWN_S = 0.8     # a kick spans several 10 fps frames
AIM_RESEND_S = 0.5        # resend the same zone at most this often


def zone_of_x(xm: float) -> str:
    if xm < -ZONE_THIRD_M / 2:
        return "L"
    if xm > ZONE_THIRD_M / 2:
        return "R"
    return "C"


class SnapkickProtocol(asyncio.DatagramProtocol):
    """Parses snapkick.pose.v1 and pushes aim/kick events onto a queue."""

    def __init__(self, queue: asyncio.Queue):
        self.queue = queue
        self.last_seq = -1
        self.last_kick = {}          # track_id -> last accepted kick time
        self.last_zone = None
        self.last_aim_sent = 0.0
        self.packets = 0

    def datagram_received(self, data, addr):
        try:
            d = json.loads(data)
        except json.JSONDecodeError:
            return
        if not str(d.get("schema", "")).startswith("snapkick.pose"):
            return                                    # legacy IMU packets: ignore
        seq = int(d.get("seq", -1))
        if 0 <= seq <= self.last_seq and self.last_seq - seq < 1000:
            return                                    # stale / reordered
        self.last_seq = seq
        self.packets += 1
        if self.packets == 1:
            print(f"[bridge] first snapkick packet from {addr[0]}")

        people = d.get("people") or []
        if not people:
            return
        p = max(people, key=lambda q: q.get("score", 0.0))
        traj = p.get("trajectory") or {}

        # ---- live aim → zone ----
        xm = None
        if "predicted_goal_x" in traj:
            xm = float(traj["predicted_goal_x"])
        elif "shot_direction_deg" in p:
            xm = GOAL_DIST_M * math.tan(math.radians(float(p["shot_direction_deg"])))
        if xm is not None:
            zone = zone_of_x(xm)
            now = time.monotonic()
            if zone != self.last_zone or now - self.last_aim_sent > AIM_RESEND_S:
                self.last_zone = zone
                self.last_aim_sent = now
                self._put({"type": "aim", "zone": zone})

        # ---- kick trigger (gated exactly like ball-game's handle_snapkick) ----
        if (p.get("kick_candidate")
                and float(p.get("kick_confidence", 0)) >= SNAPKICK_MIN_CONF
                and "predicted_goal_x" in traj):
            tid, now = p.get("track_id", 0), time.monotonic()
            if now - self.last_kick.get(tid, 0.0) > KICK_COOLDOWN_S:
                self.last_kick[tid] = now
                gx = float(traj["predicted_goal_x"])
                gz = float(traj.get("predicted_goal_z", 1.0))
                power = min(1.0, max(0.0, float(p.get("shot_power", 0.6))))
                apex = float(traj.get("predicted_apex_m", 1.2))
                speed = float(traj.get("launch_speed", 15.0))
                force = int(round(float(
                    p.get("force_estimate_n", 25 + 355 * power))))
                dir_deg = int(round(float(
                    traj.get("launch_angle_deg",
                             p.get("shot_direction_deg", 0)))))
                foot = "L" if str(p.get("kick_foot", "right")).lower().startswith("l") else "R"
                kick = {
                    "type": "kick",
                    "zone": zone_of_x(gx),
                    "power": round(power, 3),
                    "force": force,
                    "dirDeg": dir_deg,
                    "height": "H" if gz > 1.22 else "L",
                    "spin": round(max(-1.0, min(1.0, gx / GOAL_HALF_W_M)), 3),
                    "strike": "chip" if apex > 1.5 else "drive",
                    "foot": foot,
                    "goalX": round(gx, 2),
                    "goalZ": round(gz, 2),
                    "apexM": round(apex, 2),
                    "speed": round(speed, 1),
                }
                print(f"[bridge] kick -> {kick['zone']} x={gx:+.2f}m z={gz:.2f}m "
                      f"power={power:.2f} force={force}N")
                self._put(kick)

    def _put(self, msg: dict):
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass                                       # drop aim spam, never block


async def ws_loop(url: str, queue: asyncio.Queue):
    """Keep a striker connection alive; forward queued aim/kick messages."""
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, heartbeat=20) as ws:
                    print(f"[bridge] connected to {url} as 'unoq'")
                    await ws.send_json({"type": "hello", "client": "unoq"})
                    while True:
                        msg = await queue.get()
                        await ws.send_json(msg)
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
            print(f"[bridge] server unreachable ({e}) - retrying in 2 s")
            await asyncio.sleep(2)


async def main():
    ap = argparse.ArgumentParser(description="UNO Q snapkick → match server bridge")
    ap.add_argument("--host", default="127.0.0.1:8080",
                    help="match server host:port (default 127.0.0.1:8080)")
    ap.add_argument("--udp-port", type=int, default=5005,
                    help="UDP port the UNO Q sends to (default 5005)")
    args = ap.parse_args()
    url = f"ws://{args.host}/ws"

    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: SnapkickProtocol(queue), local_addr=("0.0.0.0", args.udp_port))
    print(f"[bridge] listening for snapkick.pose.v1 on UDP :{args.udp_port}")
    try:
        await ws_loop(url, queue)
    finally:
        transport.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
