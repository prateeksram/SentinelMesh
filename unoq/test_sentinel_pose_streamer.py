import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

from unoq.mediapipe_onnx_backend import _ssd_anchors, available_dnn_targets
from unoq.sentinel_pose_streamer import OpticalFlowTracker, SystemTelemetrySampler


class FakeCamera:
    pass


class MediaPipeOnnxBackendTests(unittest.TestCase):
    def test_generated_anchors_match_mediapipe_detector_layout(self):
        anchors = _ssd_anchors(np)
        self.assertEqual((2254, 2), anchors.shape)
        np.testing.assert_allclose(anchors[0], [0.5 / 28, 0.5 / 28])
        np.testing.assert_allclose(anchors[1567], [27.5 / 28, 27.5 / 28])
        np.testing.assert_allclose(anchors[1568], [0.5 / 14, 0.5 / 14])
        np.testing.assert_allclose(anchors[-1], [6.5 / 7, 6.5 / 7])

    def test_cpu_target_is_exposed(self):
        self.assertIn("cpu", available_dnn_targets(cv2))


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

    def test_system_sampler_reads_real_linux_counter_shapes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            sys_root = root / "sys"
            proc.mkdir()
            (proc / "stat").write_text("cpu 100 0 50 850 0 0 0 0\n")
            (proc / "meminfo").write_text(
                "MemTotal: 1000000 kB\nMemAvailable: 250000 kB\n"
            )
            thermal = sys_root / "class" / "thermal" / "thermal_zone0"
            thermal.mkdir(parents=True)
            (thermal / "temp").write_text("55000\n")
            kgsl = sys_root / "class" / "kgsl" / "kgsl-3d0"
            kgsl.mkdir(parents=True)
            (kgsl / "gpu_busy_percentage").write_text("23\n")

            sampler = SystemTelemetrySampler(
                interval_s=0.1, proc_root=proc, sys_root=sys_root
            )
            # 100 ticks elapsed: 70 busy and 30 idle.
            (proc / "stat").write_text("cpu 150 0 70 880 0 0 0 0\n")
            metrics = sampler.sample(time.monotonic() + 1.0)

            self.assertAlmostEqual(70.0, metrics["cpu_pct"], places=1)
            self.assertAlmostEqual(75.0, metrics["memory_pct"], places=1)
            self.assertAlmostEqual(55.0, metrics["temperature_c"], places=1)
            self.assertAlmostEqual(23.0, metrics["gpu_pct"], places=1)
            self.assertEqual("kgsl", metrics["gpu_source"])

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
