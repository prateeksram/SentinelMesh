import json
import unittest

from retargeting import (
    BodyProfile,
    GeometricRetargeter,
    L_ANK,
    L_ELB,
    L_HIP,
    L_KNE,
    L_SHO,
    L_WRI,
    R_HIP,
    R_SHO,
    normalize_pose_message,
    normalize_profile_message,
    segment_length,
)
from server import Desk, Game


def pose_points():
    points = [[0.5, 0.5, 0.0] for _ in range(33)]
    values = {
        0: (0.50, 0.12, -0.03),
        11: (0.40, 0.30, 0.00), 12: (0.60, 0.30, 0.00),
        13: (0.34, 0.44, -0.01), 14: (0.66, 0.44, -0.01),
        15: (0.30, 0.58, -0.02), 16: (0.70, 0.58, -0.02),
        23: (0.45, 0.55, 0.00), 24: (0.55, 0.55, 0.00),
        25: (0.44, 0.73, -0.01), 26: (0.56, 0.73, -0.01),
        27: (0.43, 0.91, 0.00), 28: (0.57, 0.91, 0.00),
        29: (0.43, 0.94, 0.02), 30: (0.57, 0.94, 0.02),
        31: (0.41, 0.95, -0.05), 32: (0.59, 0.95, -0.05),
    }
    for index, point in values.items():
        points[index] = list(point)
    return points


def pose_message(timestamp=1000, source="UNO Q"):
    return {
        "type": "pose_state",
        "schema": "sentinel.pose.state.v1",
        "timestampMs": timestamp,
        "source": source,
        "points": pose_points(),
    }


def profile_message():
    return {
        "type": "body_profile",
        "schema": "sentinel.body.profile.v1",
        "heightCm": 180.0,
        "weightKg": 80.0,
        "torsoM": 0.52,
    }


class FakeSocket:
    def __init__(self):
        self.messages = []

    async def send_str(self, message):
        self.messages.append(json.loads(message))


class RetargetingTests(unittest.TestCase):
    def test_protocol_rejects_wrong_shape_and_accepts_all_sources(self):
        self.assertIsNone(normalize_pose_message({"schema": "sentinel.pose.state.v1", "points": []}))
        packet = normalize_pose_message(pose_message(source="SNAPDRAGON NPU"))
        self.assertEqual("SNAPDRAGON NPU", packet["source"])
        self.assertEqual(33, len(packet["points"]))

    def test_calibration_controls_fixed_human_segment_lengths(self):
        profile = normalize_profile_message(profile_message())
        self.assertIsNotNone(profile)
        solver = GeometricRetargeter(profile)
        result = solver.solve(normalize_pose_message(pose_message()))
        points = [tuple(point) for point in result["p"]]
        dims = profile.dimensions
        self.assertAlmostEqual(dims["torso"], segment_length(points, L_HIP, L_SHO), delta=0.08)
        self.assertAlmostEqual(dims["upper_arm"], segment_length(points, L_SHO, L_ELB), places=4)
        self.assertAlmostEqual(dims["forearm"], segment_length(points, L_ELB, L_WRI), places=4)
        self.assertAlmostEqual(dims["thigh"], segment_length(points, L_HIP, L_KNE), places=4)
        self.assertAlmostEqual(dims["shin"], segment_length(points, L_KNE, L_ANK), places=4)
        self.assertAlmostEqual(dims["shoulders"], segment_length(points, L_SHO, R_SHO), places=4)
        self.assertAlmostEqual(dims["hips"], segment_length(points, L_HIP, R_HIP), places=4)
        self.assertEqual("canonical_m", result["space"])

    def test_default_profile_is_safe_when_calibration_is_absent(self):
        result = GeometricRetargeter(BodyProfile()).solve(normalize_pose_message(pose_message()))
        self.assertFalse(result["profile"]["calibrated"])
        self.assertEqual(175.0, result["profile"]["heightCm"])


class RetargetingServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_phone_pose_is_retargeted_only_to_tv(self):
        game = Game(Desk())
        phone, tv = FakeSocket(), FakeSocket()
        game.sockets[phone] = "phone"
        game.sockets[tv] = "tv"
        await game.on_message(phone, profile_message())
        phone.messages.clear()
        tv.messages.clear()
        await game.on_message(phone, pose_message())
        self.assertEqual([], phone.messages)
        self.assertEqual(1, len(tv.messages))
        self.assertEqual("retarget_state", tv.messages[0]["type"])
        self.assertEqual("UNO Q", tv.messages[0]["source"])
        self.assertEqual(180.0, tv.messages[0]["profile"]["heightCm"])
        self.assertIsNotNone(game.last_retarget)


if __name__ == "__main__":
    unittest.main()
