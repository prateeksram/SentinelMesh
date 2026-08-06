import unittest

import cv2
import numpy as np

from unoq.sentinel_pose_streamer import OpticalFlowTracker


class FakeCamera:
    pass


class OpticalFlowTrackerTests(unittest.TestCase):
    def test_tracks_foot_relative_to_stationary_pelvis(self):
        landmarks = [[0.5, 0.5, 0.0, 0.95] for _ in range(33)]
        coordinates = {
            23: (0.45, 0.42), 24: (0.55, 0.42),
            25: (0.44, 0.60), 26: (0.56, 0.60),
            27: (0.38, 0.78), 28: (0.58, 0.78),
            29: (0.40, 0.80), 30: (0.60, 0.80),
            31: (0.42, 0.79), 32: (0.62, 0.79),
        }
        for index, (x, y) in coordinates.items():
            landmarks[index] = [x, y, 0.0, 0.95]

        first = self._frame(coordinates)
        moved = dict(coordinates)
        for index in (27, 29, 31):
            x, y = moved[index]
            moved[index] = (x - 0.04, y - 0.02)
        second = self._frame(moved)

        tracker = OpticalFlowTracker(cv2, np, FakeCamera(), enabled=True)
        tracker.anchor(first, 1_000_000_000, landmarks)
        tracker._track(second, 1_033_333_333)
        motion = tracker.snapshot()

        self.assertIsNotNone(motion)
        self.assertGreaterEqual(motion["left"]["samples"], 1)
        self.assertLess(motion["left"]["dx"], -0.01)
        self.assertLess(abs(motion["right"]["dx"]), 0.02)

    def test_detector_miss_keeps_recent_flow_anchor(self):
        tracker = OpticalFlowTracker(cv2, np, FakeCamera(), enabled=True)
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        landmarks = [[0.5, 0.5, 0.0, 0.9] for _ in range(33)]
        tracker.anchor(frame, 1_000_000_000, landmarks)

        tracker.anchor(frame, 1_100_000_000, [])

        motion = tracker.snapshot()
        self.assertIsNotNone(motion)
        self.assertEqual(motion["left"]["samples"], 0)

    @staticmethod
    def _frame(coordinates):
        frame = np.zeros((200, 200, 3), dtype=np.uint8)
        for index, (x, y) in coordinates.items():
            px, py = int(x * 200), int(y * 200)
            color = (80 + (index * 17) % 175,) * 3
            cv2.rectangle(frame, (px - 4, py - 4), (px + 4, py + 4), color, -1)
            cv2.line(frame, (px - 5, py), (px + 5, py), (255, 255, 255), 1)
            cv2.line(frame, (px, py - 5), (px, py + 5), (255, 255, 255), 1)
        return frame


if __name__ == "__main__":
    unittest.main()
