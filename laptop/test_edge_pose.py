import unittest

try:
    from .server import normalize_edge_packet
except ImportError:
    from server import normalize_edge_packet


class EdgePosePacketTests(unittest.TestCase):
    def test_optical_flow_motion_is_sanitized_and_preserved(self):
        landmarks = [[0.5, 0.5, 0.0, 0.9] for _ in range(33)]
        packet = normalize_edge_packet({
            "schema": "sentinel.edge.pose.v1",
            "seq": 7,
            "t_capture_ns": 123,
            "frame": {"width": 640, "height": 480},
            "landmarks": landmarks,
            "motion": {
                "t_ns": 456,
                "fps": 29.7,
                "left": {
                    "vx": -0.8,
                    "vy": -0.3,
                    "peak_vx": -1.2,
                    "peak_vy": -0.5,
                    "dx": -0.06,
                    "dy": -0.04,
                    "confidence": 1.4,
                    "samples": 3,
                },
            },
        })

        self.assertIsNotNone(packet)
        self.assertEqual(456, packet["motion"]["t_ns"])
        self.assertEqual(1.0, packet["motion"]["left"]["confidence"])
        self.assertEqual(3, packet["motion"]["left"]["samples"])
        self.assertIsNone(packet["motion"]["right"])

    def test_legacy_pose_without_motion_remains_valid(self):
        packet = normalize_edge_packet({
            "schema": "sentinel.edge.pose.v1",
            "seq": 1,
            "t_capture_ns": 10,
            "landmarks": [],
        })

        self.assertIsNotNone(packet)
        self.assertNotIn("motion", packet)


if __name__ == "__main__":
    unittest.main()
