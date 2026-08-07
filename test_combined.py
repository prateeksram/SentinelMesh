#!/usr/bin/env python3
"""
End-to-end test of the combined stack:

    UNO Q (scripted UDP snapkick.pose.v1)  →  snapkick_bridge  →  server.py
    TV (this script, WebSocket)            ←──────────────────────┘

Launches server.py + snapkick_bridge.py itself (fast timings), then plays one
match per sport with scripted metric trajectories and asserts the hybrid
referee: football geometry gates (wide / post) + keeper outcomes, and
darts / basketball ring points.

Run:    python test_combined.py
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import aiohttp

ROOT = Path(__file__).parent
URL = "http://127.0.0.1:8080/ws"
UDP = ("127.0.0.1", 5005)

# Scripted impacts (goalX, goalZ) per sport, kick/throw 1..3, with expectations.
FOOTBALL = [
    ((5.00, 1.00), ("wide",)),           # way off frame — geometry says wide
    ((-3.70, 1.00), ("post",)),          # 4 cm outside the post — woodwork
    ((0.00, 1.20), ("goal", "save")),    # centre, on target — keeper contest
]
DARTS = [
    ((0.00, 1.73), 100), ((0.25, 1.73), 60), ((0.00, 1.20), 30),
]
BASKET = [
    ((0.00, 2.00), 100), ((0.50, 2.00), 40), ((2.00, 0.50), 0),
]


def snap_packet(seq, tid, gx, gz):
    """Schema-faithful snapkick.pose.v1 kick packet (mirrors snapkick_sim.py)."""
    return json.dumps({
        "schema": "snapkick.pose.v1", "source": "test-combined", "seq": seq,
        "t_capture_ns": time.time_ns(),
        "frame": {"width": 640, "height": 480},
        "people": [{
            "track_id": tid, "score": 0.95, "bbox": [0.3, 0.2, 0.4, 0.7],
            "kick_candidate": True, "kick_confidence": 0.9,
            "gesture": "kick", "kick_foot": "right",
            "shot_direction_deg": 0.0, "shot_power": 0.8,
            "force_estimate_n": 300.0,
            "trajectory": {
                "launch_speed": 20.0, "launch_angle_deg": 14.0,
                "predicted_apex_m": 1.1,
                "predicted_goal_x": gx, "predicted_goal_z": gz,
            },
        }],
        "diagnostics": {"fps": 10.0, "backend": "test", "transport": "udp"},
    }).encode()


async def play_match(ws, udp, sport, impacts, seq0):
    """Drive one match as TV + scripted UNO Q; return the final state."""
    kicked = None
    seq = seq0
    started = False
    async for msg in ws:
        st = json.loads(msg.data)
        if st.get("type") != "state":
            continue
        if st["phase"] == "lobby":
            if st["sport"] != sport:
                await ws.send_json({"type": "sport", "sport": sport})
            elif st["connected"]["unoq"] and not started:
                started = True
                await ws.send_json({"type": "start"})
        elif st["phase"] == "shoot" and started and kicked != st["kick"]:
            kicked = st["kick"]
            (gx, gz) = impacts[st["kick"] - 1][0]
            seq += 1
            # distinct track ids sidestep the bridge's per-track cooldown
            udp.sendto(snap_packet(seq, st["kick"], gx, gz), UDP)
        elif st["phase"] == "end" and started:
            # `started` guards against stale end snapshots from the previous
            # match (the server re-broadcasts `end` whenever the post-game
            # report card updates) that are still queued on this socket.
            return st, seq
    raise AssertionError("websocket closed before match end")


async def run():
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    async with aiohttp.ClientSession() as s:
        # wait for the server to come up
        for _ in range(50):
            try:
                ws = await s.ws_connect(URL)
                break
            except aiohttp.ClientError:
                await asyncio.sleep(0.2)
        else:
            raise AssertionError("server never came up")
        async with ws:
            await ws.send_json({"type": "hello", "client": "tv"})
            seq = 0

            # ---------------- football: hybrid referee ----------------
            st, seq = await play_match(ws, udp, "football", FOOTBALL, seq)
            shots = st["shotmap"]
            assert len(shots) == 3, f"expected 3 kicks, got {len(shots)}"
            for i, (_, allowed) in enumerate(FOOTBALL):
                r = shots[i]["result"]
                assert r in allowed, f"kick {i+1}: {r} not in {allowed}"
            assert all("goalX" in sh for sh in shots), "metric impact not recorded"
            assert all(sh["keeperZone"] in "LCR" for sh in shots)
            goals = sum(1 for sh in shots if sh["result"] == "goal")
            assert st["score"] == goals, "score != goals"
            assert shots[2]["zone"] == "C", "impact zones wrong"
            # SceneEngine campaign: full time must have designed the next venue.
            want_level = {0: 1, 1: 1, 2: 2, 3: 3}[goals]
            assert st["level"] == want_level, \
                f"campaign level {st['level']}, expected {want_level} for {goals} goals"
            assert st["scene"], "no scene generated at full time"
            assert st["sceneMetrics"]["source"] in ("template", "geniex", "openai")
            ring_for = {1: 1.0, 2: 0.9, 3: 0.8, 4: 0.7, 5: 0.6}
            assert abs(st["ringScale"] - ring_for[want_level]) < 1e-6, \
                f"ringScale {st['ringScale']} != level-{want_level} scale"
            print(f"FOOTBALL ok — {[sh['result'] for sh in shots]} "
                  f"score {st['score']}/{st['kicksTotal']} → level {st['level']} "
                  f"({st['sceneMetrics']['source']} scene)")
            # abort = full campaign reset → deterministic rings for the next sport
            await ws.send_json({"type": "abort"})

            # ---------------- darts: ring geometry ----------------
            st, seq = await play_match(ws, udp, "darts", DARTS, seq)
            shots = st["shotmap"]
            for i, (_, pts) in enumerate(DARTS):
                got = shots[i].get("points", 0)
                assert got == pts, f"dart {i+1}: {got} pts, expected {pts}"
                assert shots[i]["result"] == ("hit" if pts else "miss")
            assert st["score"] == sum(p for _, p in DARTS), "dart total wrong"
            # Target-sport campaign: 3/3 on target → level 3 → rings at 0.8×.
            hits = sum(1 for sh in shots if sh["result"] == "hit")
            want_level = {0: 1, 1: 1, 2: 2, 3: 3}[hits]
            assert st["level"] == want_level, \
                f"darts campaign level {st['level']}, expected {want_level}"
            assert abs(st["ringScale"] - ring_for[want_level]) < 1e-6, \
                "target-sport ringScale not applied from the scene"
            print(f"DARTS ok — {[sh.get('points', 0) for sh in shots]} "
                  f"total {st['score']} → level {st['level']} "
                  f"rings ×{st['ringScale']}")
            await ws.send_json({"type": "abort"})

            # ---------------- basketball: ring geometry ----------------
            st, seq = await play_match(ws, udp, "basketball", BASKET, seq)
            shots = st["shotmap"]
            for i, (_, pts) in enumerate(BASKET):
                got = shots[i].get("points", 0)
                assert got == pts, f"shot {i+1}: {got} pts, expected {pts}"
            assert st["score"] == sum(p for _, p in BASKET), "basket total wrong"
            print(f"BASKETBALL ok — {[sh.get('points', 0) for sh in shots]} "
                  f"total {st['score']}")

    print("OK — hybrid referee, ring scoring, bridge and sport switching all good")


def main():
    # Windows PowerShell can inherit cp1252 even when the source is UTF-8.
    # Keep progress reporting from aborting an otherwise successful E2E run.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    env = dict(os.environ,
               GF_ANNOUNCE_S="0.3", GF_COUNTDOWN_S="0.3",
               GF_RESOLVE_S="0.4", GF_SHOOT_WINDOW="5.0")
    server = subprocess.Popen([sys.executable, str(ROOT / "server.py")],
                              env=env, cwd=ROOT)
    bridge = subprocess.Popen([sys.executable, str(ROOT / "snapkick_bridge.py")],
                              cwd=ROOT)
    try:
        asyncio.run(run())
    finally:
        bridge.terminate()
        server.terminate()


if __name__ == "__main__":
    main()
