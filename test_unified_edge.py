import asyncio
import json
import socket
import time
import unittest

import aiohttp
from aiohttp import web

from server import Desk, EdgePoseProtocol, Game, make_app, normalize_edge_packet


def edge_packet(seq=7):
    return {
        "schema": "sentinel.edge.pose.v1",
        "seq": seq,
        "t_capture_ns": 1_234_567_890,
        "frame": {"width": 640, "height": 480, "rotation": 0, "mirrored": True},
        "landmarks": [[0.5, 0.5, 0.0, 0.95] for _ in range(33)],
        "motion": {
            "t_ns": 1_234_567_890,
            "fps": 29.5,
            "left": {
                "vx": -0.3, "vy": 0.1, "peak_vx": -0.5, "peak_vy": 0.2,
                "dx": -0.08, "dy": 0.03, "confidence": 0.9, "samples": 4,
            },
        },
        "diagnostics": {"fps": 9.8, "inference_ms": 41.0, "backend": "uno-q-test"},
    }


class FakeSocket:
    def __init__(self):
        self.messages = []

    async def send_str(self, message):
        self.messages.append(message)


class UnifiedEdgeTests(unittest.IsolatedAsyncioTestCase):
    def test_root_host_exposes_edge_camera_and_status_routes(self):
        routes = {route.resource.canonical for route in make_app().router.routes()}
        self.assertTrue({
            "/edge/frame", "/edge/source/frame", "/edge/source/camera.mjpg",
            "/edge/frame.jpg", "/edge/camera.mjpg", "/edge/status",
        }.issubset(routes))

    def test_raw_edge_packet_preserves_landmarks_and_flow(self):
        packet = normalize_edge_packet(edge_packet())
        self.assertIsNotNone(packet)
        self.assertEqual(33, len(packet["landmarks"]))
        self.assertEqual(4, packet["motion"]["left"]["samples"])
        self.assertEqual("uno-q-test", packet["diagnostics"]["backend"])

    async def test_edge_pose_is_sent_only_to_phone_clients(self):
        game = Game(Desk())
        phone = FakeSocket()
        tv = FakeSocket()
        game.sockets[phone] = "phone"
        game.sockets[tv] = "tv"
        await game.broadcast_edge_pose(normalize_edge_packet(edge_packet()))
        self.assertEqual(1, len(phone.messages))
        self.assertEqual(0, len(tv.messages))
        self.assertIn('"type":"edge_pose"', phone.messages[0])

    async def test_android_state_trajectory_drives_metric_referee_fields(self):
        game = Game(Desk())
        phone = FakeSocket()
        game.sockets[phone] = "phone"
        game.phase = "shoot"
        await game.on_message(phone, {
            "type": "kick", "zone": "R", "power": 0.78, "force": 240,
            "dirDeg": 8, "height": "H", "spin": 0.2, "strike": "drive", "foot": "R",
            "kickState": {
                "schema": "sentinel.kick.state.v1", "source": "UNO Q",
                "peakFootSpeedMps": 4.6, "lateralVelocityMps": 0.8,
                "upwardVelocityMps": 0.7, "pathDisplacementM": 0.34,
                "liftM": 0.12, "swingDurationMs": 370, "confidence": 0.84,
            },
            "trajectory": {
                "schema": "sentinel.trajectory.v1", "model": "sentinel.pose-ballistic.v1",
                "confidence": 0.78, "launchVelocity": [1.2, 16.0, 3.5],
                "launchSpeedMps": 16.42, "flightTimeS": 0.73,
                "goalX": 2.1, "goalZ": 1.15, "apexM": 1.3,
                "points": [[0.0, 0.0, 0.0, 0.11], [0.73, 2.1, 11.0, 1.15]],
            },
        })
        self.assertEqual("UNO Q", game.kick_msg["kickState"]["source"])
        self.assertEqual(2.1, game.kick_msg["goalX"])
        self.assertEqual(1.15, game.kick_msg["goalZ"])
        self.assertEqual(2, len(game.kick_msg["trajectory"]["points"]))

    async def test_root_host_round_trips_preview_and_pose_to_phone(self):
        runner = web.AppRunner(make_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        http_port = site._server.sockets[0].getsockname()[1]
        edge_transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            EdgePoseProtocol, local_addr=("127.0.0.1", 0)
        )
        udp_port = edge_transport.get_extra_info("sockname")[1]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(f"http://127.0.0.1:{http_port}/ws") as ws:
                    await ws.send_json({"type": "hello", "client": "phone"})
                    jpeg = b"\xff\xd8\xff\xe0unoq-preview\xff\xd9"
                    async with session.post(
                        f"http://127.0.0.1:{http_port}/edge/frame", data=jpeg
                    ) as posted:
                        self.assertEqual(200, posted.status)
                        posted_seq = (await posted.json())["seq"]
                    async with session.get(
                        f"http://127.0.0.1:{http_port}/edge/frame.jpg"
                    ) as fetched:
                        self.assertEqual(200, fetched.status)
                        self.assertEqual(str(posted_seq), fetched.headers["X-Edge-Seq"])
                        self.assertEqual(jpeg, await fetched.read())

                    packet = edge_packet(seq=99)
                    packet["t_capture_ns"] = time.time_ns()
                    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    try:
                        sender.sendto(json.dumps(packet).encode("utf-8"), ("127.0.0.1", udp_port))
                    finally:
                        sender.close()

                    for _ in range(4):
                        message = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                        if message.get("type") == "edge_pose":
                            self.assertEqual(99, message["seq"])
                            self.assertEqual(33, len(message["landmarks"]))
                            break
                    else:
                        self.fail("phone did not receive the UNO Q edge_pose frame")
        finally:
            edge_transport.close()
            await runner.cleanup()


if __name__ == "__main__":
    unittest.main()
