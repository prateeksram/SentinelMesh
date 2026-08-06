#!/usr/bin/env python3
"""Replay a UNO Q JSONL recording through Sentinel's real UDP/phone path."""

from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Replay sentinel.edge.pose.v1 JSONL")
    parser.add_argument("recording", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--speed", type=float, default=1.0, help="1=real time, 2=twice as fast")
    parser.add_argument("--no-timing", action="store_true")
    return parser.parse_args()


def load_packets(path: Path):
    packets = []
    with path.expanduser().open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            packet = json.loads(line)
            if packet.get("schema") != "sentinel.edge.pose.v1":
                raise ValueError(f"line {line_number}: unsupported schema")
            packets.append(packet)
    if not packets:
        raise ValueError("recording contains no pose packets")
    return packets


def main() -> int:
    args = parse_args()
    packets = load_packets(args.recording)
    first_capture = int(packets[0].get("t_capture_ns", 0))
    replay_origin = time.monotonic_ns()
    speed = max(0.01, args.speed)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for sequence, packet in enumerate(packets):
            original_capture = int(packet.get("t_capture_ns", first_capture))
            relative_ns = max(0, original_capture - first_capture)
            target_ns = replay_origin + int(relative_ns / speed)
            if not args.no_timing:
                delay = (target_ns - time.monotonic_ns()) / 1e9
                if delay > 0:
                    time.sleep(delay)
            packet["seq"] = sequence
            packet["t_capture_ns"] = target_ns
            motion = packet.get("motion")
            if isinstance(motion, dict) and int(motion.get("t_ns", 0)) > 0:
                motion_relative = max(0, int(motion["t_ns"]) - first_capture)
                motion["t_ns"] = replay_origin + int(motion_relative / speed)
            payload = json.dumps(packet, separators=(",", ":")).encode("utf-8")
            udp.sendto(payload, (args.host, args.port))
        print(
            f"replayed {len(packets)} frames to udp://{args.host}:{args.port} "
            f"at {speed:g}x",
            flush=True,
        )
    finally:
        udp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
