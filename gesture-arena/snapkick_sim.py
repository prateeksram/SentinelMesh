"""
snapkick.pose.v1 simulator — pretends to be the UNO Q pose pipeline.

Sends schema-faithful UDP packets to the game server: idle person frames
at 10 fps with a wandering shot direction, and a kick every ~4 seconds
with a randomized trajectory (predicted goal impact, power, apex).

Usage:
    python snapkick_sim.py               # sends to 127.0.0.1:5005
    python snapkick_sim.py 192.168.1.42  # sends to a specific laptop IP
"""

import json
import math
import random
import socket
import sys
import time

LAPTOP_IP = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
UDP_PORT = 5005
FPS = 10.0
KICK_EVERY_S = 4.0

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
seq = 0
next_kick = time.time() + 2.0
print(f"snapkick sim -> {LAPTOP_IP}:{UDP_PORT}")

while True:
    seq += 1
    now = time.time()
    kicking = now >= next_kick

    person = {
        "track_id": 1,
        "score": 0.94,
        "bbox": [0.34, 0.2, 0.34, 0.7],
        "landmarks": {  # minimal set; the game reads only higher-level fields
            "right_ankle": {"x": 0.58, "y": 0.78, "z": 0.0, "visibility": 0.95},
            "left_ankle":  {"x": 0.45, "y": 0.84, "z": 0.0, "visibility": 0.92},
        },
        "ankle_velocity": {"left": [0.02, 0.01, 0.0],
                           "right": [1.42 if kicking else 0.05, -0.31, 0.0]},
        "kick_candidate": kicking,
        "kick_confidence": round(random.uniform(0.8, 0.95), 2) if kicking else 0.1,
        "gesture": "kick" if kicking else "idle",
        "kick_foot": "right",
        "shot_direction_deg": round(15.0 * math.sin(now * 0.6), 1),
    }

    if kicking:
        gx = round(random.uniform(-3.2, 3.2), 2)   # lateral impact (m)
        gz = round(random.uniform(0.15, 2.3), 2)   # impact height (m)
        power = round(random.uniform(0.5, 0.95), 2)
        person["shot_power"] = power
        person["force_estimate_n"] = round(25 + 25 * power, 1)
        person["trajectory"] = {
            "launch_velocity": [round(gx / 0.5, 1), 21.7, 5.8],
            "launch_speed": round(15 + 12 * power, 1),
            "launch_angle_deg": round(8 + 10 * gz / 2.44, 1),
            "predicted_apex_m": round(0.6 + gz * 0.7, 2),
            "predicted_goal_x": gx,
            "predicted_goal_z": gz,
            "predicted_path": [[round(t, 1), round(gx * t / 0.6, 2),
                                round(20 * t, 2), round(gz * math.sin(math.pi * min(t / 0.6, 1)), 2)]
                               for t in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)],
        }
        next_kick = now + KICK_EVERY_S
        print(f"kick! x={gx:+.2f}m z={gz:.2f}m power={power:.2f}")

    packet = {
        "schema": "snapkick.pose.v1",
        "source": "snapkick-sim",
        "seq": seq,
        "t_capture_ns": time.time_ns(),
        "frame": {"width": 640, "height": 480},
        "people": [person],
        "diagnostics": {"fps": FPS, "backend": "sim",
                        "capture_ms": 4.2, "inference_ms": 28.7,
                        "postprocess_ms": 1.6, "transport": "udp"},
    }
    sock.sendto(json.dumps(packet).encode(), (LAPTOP_IP, UDP_PORT))
    time.sleep(1.0 / FPS)
