#!/usr/bin/env python3
"""UNO Q USB-camera source for SentinelMesh.

The OpenCV Zoo BlazePose backend is vendored beside this file so deployment is
self-contained. It publishes all 33 MediaPipe points required by the
SentinelMesh calibration flow. Camera capture and JPEG relay are latest-only
threads; slow pose inference cannot build a stale frame queue.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import threading
import time
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request


class SystemTelemetrySampler:
    """Low-rate Linux board metrics with no third-party dependency.

    Reading procfs/sysfs once per second is negligible next to pose inference.
    GPU utilization is reported only when a real KGSL/devfreq counter exists;
    callers can distinguish an unavailable counter from a genuine 0% sample.
    """

    def __init__(
        self,
        interval_s: float = 1.0,
        proc_root: str | Path = "/proc",
        sys_root: str | Path = "/sys",
    ):
        self.interval_s = max(0.1, float(interval_s))
        self.proc_root = Path(proc_root)
        self.sys_root = Path(sys_root)
        self._last_sample_at = 0.0
        self._cached: dict = {}
        self._last_cpu = self._read_cpu_times()
        self._last_process_s = self._process_seconds()
        self._last_process_at = time.monotonic()
        self._last_gpu_counters = None

    @staticmethod
    def _process_seconds() -> float:
        times = os.times()
        return float(times.user + times.system)

    @staticmethod
    def _number(path: Path):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", text)
            if match is None:
                return None
            value = float(match.group(0))
            return value if math.isfinite(value) else None
        except (OSError, ValueError, IndexError):
            return None

    def _read_cpu_times(self):
        try:
            fields = (self.proc_root / "stat").read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines()[0].split()
            if not fields or fields[0] != "cpu":
                return None
            values = [float(value) for value in fields[1:]]
            if len(values) < 4:
                return None
            idle = values[3] + (values[4] if len(values) > 4 else 0.0)
            return sum(values), idle
        except (OSError, ValueError, IndexError):
            return None

    def _read_memory(self):
        try:
            values = {}
            for line in (self.proc_root / "meminfo").read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines():
                name, _, rest = line.partition(":")
                if not rest:
                    continue
                values[name] = float(rest.strip().split()[0])
            total = values.get("MemTotal", 0.0)
            available = values.get("MemAvailable", values.get("MemFree", 0.0))
            if total <= 0:
                return None, None
            used_kib = max(0.0, total - available)
            return 100.0 * used_kib / total, used_kib / 1024.0
        except (OSError, ValueError, IndexError):
            return None, None

    def _read_temperature(self):
        samples = []
        thermal_root = self.sys_root / "class" / "thermal"
        for path in thermal_root.glob("thermal_zone*/temp"):
            value = self._number(path)
            if value is None:
                continue
            if abs(value) > 1000.0:
                value /= 1000.0
            if -20.0 <= value <= 150.0:
                samples.append(value)
        return max(samples) if samples else None

    def _read_gpu(self):
        kgsl = self.sys_root / "class" / "kgsl" / "kgsl-3d0"
        direct = self._number(kgsl / "gpu_busy_percentage")
        if direct is not None:
            return max(0.0, min(100.0, direct)), "kgsl"

        # Some Qualcomm kernels expose cumulative busy/total counters instead.
        try:
            values = [int(value) for value in (kgsl / "gpubusy").read_text().split()[:2]]
            if len(values) == 2:
                current = tuple(values)
                previous, self._last_gpu_counters = self._last_gpu_counters, current
                if previous is not None:
                    busy = current[0] - previous[0]
                    total = current[1] - previous[1]
                    if total > 0:
                        return max(0.0, min(100.0, 100.0 * busy / total)), "kgsl"
        except (OSError, ValueError):
            pass

        for root in (self.sys_root / "class" / "devfreq").glob("*"):
            load = self._number(root / "load")
            if load is not None:
                return max(0.0, min(100.0, load)), "devfreq"
        return None, None

    def sample(self, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else float(now)
        if self._cached and now - self._last_sample_at < self.interval_s:
            return dict(self._cached)

        current_cpu = self._read_cpu_times()
        cpu_pct = None
        if current_cpu is not None and self._last_cpu is not None:
            total = current_cpu[0] - self._last_cpu[0]
            idle = current_cpu[1] - self._last_cpu[1]
            if total > 0:
                cpu_pct = 100.0 * max(0.0, total - idle) / total
        self._last_cpu = current_cpu

        process_s = self._process_seconds()
        process_wall = max(1e-6, now - self._last_process_at)
        process_cpu_pct = 100.0 * max(0.0, process_s - self._last_process_s) / process_wall
        self._last_process_s, self._last_process_at = process_s, now

        memory_pct, memory_used_mb = self._read_memory()
        temperature_c = self._read_temperature()
        gpu_pct, gpu_source = self._read_gpu()
        self._cached = {
            "cpu_pct": None if cpu_pct is None else round(cpu_pct, 1),
            "process_cpu_pct": round(process_cpu_pct, 1),
            "memory_pct": None if memory_pct is None else round(memory_pct, 1),
            "memory_used_mb": None if memory_used_mb is None else round(memory_used_mb, 1),
            "temperature_c": None if temperature_c is None else round(temperature_c, 1),
            "gpu_pct": None if gpu_pct is None else round(gpu_pct, 1),
            "gpu_source": gpu_source,
        }
        self._last_sample_at = now
        return dict(self._cached)


class LatestCamera:
    def __init__(self, cv2, source: str, width: int, height: int, fps: float):
        self.cv2 = cv2
        camera_source = int(source) if source.isdigit() else source
        self.capture = cv2.VideoCapture(camera_source)
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.capture.set(cv2.CAP_PROP_FPS, fps)
        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        try:
            self.capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except Exception:
            pass
        if not self.capture.isOpened():
            raise RuntimeError(f"cannot open USB camera {source}")
        self.width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH)) or width
        self.height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) or height
        self._condition = threading.Condition()
        self._frame = None
        self._seq = -1
        self._capture_ns = 0
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop:
            ok, frame = self.capture.read()
            if not ok:
                time.sleep(0.05)
                continue
            with self._condition:
                self._frame = frame
                self._seq += 1
                self._capture_ns = time.monotonic_ns()
                self._condition.notify_all()

    def next(self, after_seq: int, timeout: float = 1.0):
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._seq <= after_seq and not self._stop:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            if self._frame is None:
                return None
            return self._seq, self._capture_ns, self._frame.copy()

    def close(self) -> None:
        self._stop = True
        with self._condition:
            self._condition.notify_all()
        self.capture.release()
        self._thread.join(timeout=1.0)


class JpegRelay:
    def __init__(self, cv2, url: str, fps: float, quality: int):
        self.cv2 = cv2
        self.url = url
        self.period = 1.0 / max(1.0, fps)
        self.quality = max(35, min(90, quality))
        self._condition = threading.Condition()
        self._frame = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def submit(self, frame) -> None:
        with self._condition:
            self._frame = frame
            self._condition.notify()

    def _run(self) -> None:
        last_sent = 0.0
        while not self._stop:
            with self._condition:
                if self._frame is None:
                    self._condition.wait(0.5)
                    continue
                frame = self._frame
                self._frame = None
            delay = self.period - (time.monotonic() - last_sent)
            if delay > 0:
                time.sleep(delay)
            ok, encoded = self.cv2.imencode(
                ".jpg", frame, [self.cv2.IMWRITE_JPEG_QUALITY, self.quality]
            )
            if not ok:
                continue
            try:
                req = urllib_request.Request(
                    self.url,
                    data=encoded.tobytes(),
                    headers={"Content-Type": "image/jpeg"},
                    method="POST",
                )
                with urllib_request.urlopen(req, timeout=0.5):
                    pass
                last_sent = time.monotonic()
            except (OSError, urllib_error.URLError):
                time.sleep(0.15)

    def close(self) -> None:
        self._stop = True
        with self._condition:
            self._condition.notify_all()
        self._thread.join(timeout=1.0)


class PreviewPump:
    """Copy new camera frames to the relay without waiting for pose inference."""
    def __init__(self, camera: LatestCamera, relay: JpegRelay):
        self.camera = camera
        self.relay = relay
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        seq = -1
        while not self._stop:
            item = self.camera.next(seq, timeout=0.5)
            if item is None:
                continue
            seq, _capture_ns, frame = item
            self.relay.submit(frame)

    def close(self) -> None:
        self._stop = True
        self._thread.join(timeout=1.0)


class OpticalFlowTracker:
    """Track pelvis-relative lower-body motion between expensive pose frames."""

    INDICES = (23, 24, 25, 26, 27, 28, 29, 30, 31, 32)
    LEFT_FOOT = (27, 29, 31)
    RIGHT_FOOT = (28, 30, 32)
    MAX_ANCHOR_AGE_NS = 1_000_000_000

    def __init__(self, cv2, np, camera: LatestCamera, enabled: bool = True):
        self.cv2 = cv2
        self.np = np
        self.camera = camera
        self.enabled = enabled
        self._lock = threading.Lock()
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._previous_gray = None
        self._points = None
        self._visibility = {}
        self._last_ns = 0
        self._anchor_ns = 0
        self._base_rel = {}
        self._last_rel = {}
        self._velocity = {"left": (0.0, 0.0), "right": (0.0, 0.0)}
        self._peak = {"left": (0.0, 0.0), "right": (0.0, 0.0)}
        self._confidence = {"left": 0.0, "right": 0.0}
        self._samples = 0
        self._fps_ema = 0.0

    def start(self) -> None:
        if self.enabled:
            self._thread.start()

    def anchor(self, frame, capture_ns: int, landmarks) -> None:
        if not self.enabled:
            return
        if len(landmarks) != 33:
            # Detector misses are precisely when flow is useful. Keep the last
            # good pose anchor and let snapshot() expire it if pose does not
            # recover quickly enough.
            return
        height, width = frame.shape[:2]
        points = self.np.array(
            [
                [float(landmarks[index][0]) * width, float(landmarks[index][1]) * height]
                for index in self.INDICES
            ],
            dtype=self.np.float32,
        ).reshape(-1, 1, 2)
        gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
        visibility = {index: float(landmarks[index][3]) for index in self.INDICES}
        with self._lock:
            self._previous_gray = gray
            self._points = points
            self._visibility = visibility
            self._last_ns = capture_ns
            self._anchor_ns = capture_ns
            self._samples = 0
            self._fps_ema = 0.0
            self._velocity = {"left": (0.0, 0.0), "right": (0.0, 0.0)}
            self._peak = {"left": (0.0, 0.0), "right": (0.0, 0.0)}
            self._confidence = {"left": 0.0, "right": 0.0}
            initial = self._relative_positions(points, width, height, None)
            self._base_rel = dict(initial)
            self._last_rel = dict(initial)

    def clear(self) -> None:
        with self._lock:
            self._previous_gray = None
            self._points = None
            self._last_ns = 0
            self._anchor_ns = 0
            self._base_rel = {}
            self._last_rel = {}
            self._samples = 0

    def snapshot(self):
        if not self.enabled:
            return None
        with self._lock:
            if self._last_ns <= 0 or not self._last_rel:
                return None
            if self._last_ns - self._anchor_ns > self.MAX_ANCHOR_AGE_NS:
                return None
            result = {
                "t_ns": self._last_ns,
                "fps": round(self._fps_ema, 2),
            }
            for side in ("left", "right"):
                current = self._last_rel.get(side)
                base = self._base_rel.get(side)
                if current is None or base is None:
                    result[side] = None
                    continue
                vx, vy = self._velocity[side]
                peak_vx, peak_vy = self._peak[side]
                result[side] = {
                    "vx": round(vx, 6),
                    "vy": round(vy, 6),
                    "peak_vx": round(peak_vx, 6),
                    "peak_vy": round(peak_vy, 6),
                    "dx": round(current[0] - base[0], 6),
                    "dy": round(current[1] - base[1], 6),
                    "confidence": round(self._confidence[side], 4),
                    "samples": self._samples,
                }
            return result

    def close(self) -> None:
        self._stop = True
        if self.enabled:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        seq = -1
        while not self._stop:
            item = self.camera.next(seq, timeout=0.5)
            if item is None:
                continue
            seq, capture_ns, frame = item
            self._track(frame, capture_ns)

    def _track(self, frame, capture_ns: int) -> None:
        with self._lock:
            if self._previous_gray is None or self._points is None or capture_ns <= self._last_ns:
                return
            gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
            next_points, status, error = self.cv2.calcOpticalFlowPyrLK(
                self._previous_gray,
                gray,
                self._points,
                None,
                winSize=(31, 31),
                maxLevel=3,
                criteria=(
                    self.cv2.TERM_CRITERIA_EPS | self.cv2.TERM_CRITERIA_COUNT,
                    20,
                    0.02,
                ),
            )
            if next_points is None or status is None:
                self._previous_gray = gray
                self._last_ns = capture_ns
                return
            valid = status.reshape(-1).astype(bool)
            next_flat = next_points.reshape(-1, 2)
            previous_flat = self._points.reshape(-1, 2)
            next_flat[~valid] = previous_flat[~valid]
            next_points = next_flat.reshape(-1, 1, 2).astype(self.np.float32)
            height, width = gray.shape[:2]
            relative = self._relative_positions(next_points, width, height, valid)
            dt = (capture_ns - self._last_ns) / 1e9
            if 0.001 <= dt <= 0.25:
                instant_fps = 1.0 / dt
                self._fps_ema = instant_fps if self._fps_ema <= 0 else 0.85 * self._fps_ema + 0.15 * instant_fps
                errors = error.reshape(-1) if error is not None else self.np.zeros(len(valid))
                for side in ("left", "right"):
                    current = relative.get(side)
                    previous = self._last_rel.get(side)
                    if current is None or previous is None:
                        self._confidence[side] = 0.0
                        continue
                    raw_vx = (current[0] - previous[0]) / dt
                    raw_vy = (current[1] - previous[1]) / dt
                    old_vx, old_vy = self._velocity[side]
                    vx = 0.58 * raw_vx + 0.42 * old_vx
                    vy = 0.58 * raw_vy + 0.42 * old_vy
                    self._velocity[side] = (vx, vy)
                    if math.hypot(vx, vy) >= math.hypot(*self._peak[side]):
                        self._peak[side] = (vx, vy)
                    foot_indices = self.LEFT_FOOT if side == "left" else self.RIGHT_FOOT
                    slots = [self.INDICES.index(index) for index in (23, 24, *foot_indices)]
                    track_ratio = sum(1 for slot in slots if valid[slot]) / len(slots)
                    visible = sum(self._visibility.get(self.INDICES[slot], 0.0) for slot in slots) / len(slots)
                    good_errors = [float(errors[slot]) for slot in slots if valid[slot]]
                    mean_error = sum(good_errors) / len(good_errors) if good_errors else 100.0
                    error_score = max(0.0, min(1.0, 1.0 - mean_error / 35.0))
                    age_s = max(0.0, (capture_ns - self._anchor_ns) / 1e9)
                    age_score = max(0.0, 1.0 - age_s / 1.0)
                    self._confidence[side] = max(
                        0.0,
                        min(1.0, track_ratio * visible * error_score * age_score),
                    )
                self._samples += 1
                self._last_rel = relative
            self._points = next_points
            self._previous_gray = gray
            self._last_ns = capture_ns

    def _relative_positions(self, points, width: int, height: int, valid):
        flat = points.reshape(-1, 2)

        def average(indices):
            slots = [self.INDICES.index(index) for index in indices]
            usable = [slot for slot in slots if valid is None or valid[slot]]
            if not usable:
                return None
            return (
                sum(float(flat[slot][0]) for slot in usable) / len(usable),
                sum(float(flat[slot][1]) for slot in usable) / len(usable),
            )

        hip = average((23, 24))
        if hip is None:
            return {}
        result = {}
        for side, indices in (("left", self.LEFT_FOOT), ("right", self.RIGHT_FOOT)):
            foot = average(indices)
            if foot is not None:
                result[side] = ((foot[0] - hip[0]) / width, (foot[1] - hip[1]) / height)
        return result


class AdaptiveDetectorInterval:
    """Use frequent recovery detection and a cheaper cadence for a stable torso."""

    def __init__(self, backend, normal: int, stable: int, recovery: int):
        self.backend = backend
        self.normal = max(1, normal)
        self.stable = max(self.normal, stable)
        self.recovery = max(1, min(self.normal, recovery))
        self.stable_frames = 0
        self.active = self.normal
        self.attribute = next(
            (name for name in ("detector_interval", "_detector_interval") if hasattr(backend, name)),
            None,
        )

    def update(self, landmarks) -> int:
        if len(landmarks) == 33:
            torso_visibility = min(landmarks[index][3] for index in (11, 12, 23, 24))
            self.stable_frames = self.stable_frames + 1 if torso_visibility >= 0.65 else 0
            target = self.stable if self.stable_frames >= 8 else self.normal
        else:
            self.stable_frames = 0
            target = self.recovery
        if self.attribute is not None and target != self.active:
            try:
                setattr(self.backend, self.attribute, target)
                self.active = target
            except (AttributeError, TypeError, ValueError):
                self.attribute = None
        return self.active


class JsonlRecorder:
    def __init__(self, path: str | None):
        self._file = None
        if path:
            output = Path(path).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            self._file = output.open("a", encoding="utf-8", buffering=1)
            print(f"recording raw edge poses to {output}", flush=True)

    def write(self, packet: dict) -> None:
        if self._file is not None:
            self._file.write(json.dumps(packet, separators=(",", ":")) + "\n")

    def close(self) -> None:
        if self._file is not None:
            self._file.close()


def parse_args():
    parser = argparse.ArgumentParser(description="SentinelMesh UNO Q pose source")
    parser.add_argument("--laptop-ip", required=True)
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument("--udp-port", type=int, default=9999)
    parser.add_argument("--camera", default="/dev/video0")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--camera-fps", type=float, default=30.0)
    parser.add_argument("--target-fps", type=float, default=10.0)
    parser.add_argument("--preview-fps", type=float, default=5.0)
    parser.add_argument("--jpeg-quality", type=int, default=68)
    parser.add_argument("--model", required=True, help="BlazePose landmark ONNX")
    parser.add_argument("--person-model", help="OpenCV Zoo person detector ONNX")
    parser.add_argument("--roi-url", help="Optional external OpenCV Zoo ROI endpoint")
    parser.add_argument("--roi-hz", type=float, default=5.0)
    parser.add_argument("--detector-interval", type=int, default=4)
    parser.add_argument("--detector-stable-interval", type=int, default=6)
    parser.add_argument("--detector-recovery-interval", type=int, default=1)
    parser.add_argument(
        "--dnn-target",
        choices=("cpu", "opencl", "opencl-fp16"),
        default="cpu",
        help="OpenCV DNN execution target; CPU is the measured UNO Q default",
    )
    parser.add_argument(
        "--optical-flow",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="track pelvis-relative feet on camera frames between pose inferences",
    )
    parser.add_argument(
        "--record-jsonl",
        help="optional append-only raw packet recording for offline replay",
    )
    parser.add_argument("--log-interval", type=float, default=1.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    import cv2
    import numpy as np
    try:
        from .mediapipe_onnx_backend import MediaPipeOnnxBackend
    except ImportError:
        # Direct board launch: python3 /home/arduino/sentinelmesh/sentinel_pose_streamer.py
        from mediapipe_onnx_backend import MediaPipeOnnxBackend

    if not args.person_model and not args.roi_url:
        raise ValueError("pass --person-model for local detection or --roi-url for split mode")
    backend = MediaPipeOnnxBackend(
        args.model,
        args.person_model,
        1,
        roi_url=args.roi_url,
        roi_hz=args.roi_hz,
        detector_interval=args.detector_interval,
        dnn_target=args.dnn_target,
    )
    # SnapKick normally publishes a gameplay subset. Sentinel calibration needs
    # the complete indexed MediaPipe topology, including elbows and wrists.
    backend._LANDMARK_NAMES = {index: str(index) for index in range(33)}

    camera = LatestCamera(
        cv2, str(args.camera), args.width, args.height, args.camera_fps
    )
    frame_url = f"http://{args.laptop_ip}:{args.http_port}/edge/frame"
    relay = JpegRelay(cv2, frame_url, args.preview_fps, args.jpeg_quality)
    preview_pump = PreviewPump(camera, relay)
    flow_tracker = OpticalFlowTracker(cv2, np, camera, enabled=args.optical_flow)
    adaptive_detector = AdaptiveDetectorInterval(
        backend,
        normal=args.detector_interval,
        stable=args.detector_stable_interval,
        recovery=args.detector_recovery_interval,
    )
    recorder = JsonlRecorder(args.record_jsonl)
    system_telemetry = SystemTelemetrySampler()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    camera.start()
    relay.start()
    preview_pump.start()
    flow_tracker.start()

    seq = 0
    camera_seq = -1
    fps_ema = 0.0
    last_infer_at = time.perf_counter()
    last_log = 0.0
    min_period = 1.0 / max(1.0, args.target_fps)
    print(
        f"Sentinel UNO Q camera={args.camera} {camera.width}x{camera.height} "
        f"pose={args.target_fps:g} preview={args.preview_fps:g} laptop={args.laptop_ip}",
        flush=True,
    )
    try:
        while True:
            item = camera.next(camera_seq)
            if item is None:
                continue
            camera_seq, capture_ns, frame = item
            started = time.perf_counter()
            detections = backend.detect(frame, capture_ns // 1_000_000)
            inference_ms = (time.perf_counter() - started) * 1000.0
            best = max(detections, key=lambda detection: detection.score) if detections else None
            landmarks = []
            if best is not None:
                for index in range(33):
                    point = best.landmarks.get(str(index))
                    if point is None:
                        landmarks = []
                        break
                    landmarks.append([
                        round(float(point.x), 6),
                        round(float(point.y), 6),
                        round(float(point.z), 6),
                        round(float(point.visibility), 4),
                    ])

            motion = flow_tracker.snapshot()
            flow_tracker.anchor(frame, capture_ns, landmarks)
            detector_interval = adaptive_detector.update(landmarks)

            now = time.perf_counter()
            elapsed = max(1e-6, now - last_infer_at)
            instant_fps = 1.0 / elapsed
            fps_ema = instant_fps if fps_ema == 0.0 else 0.9 * fps_ema + 0.1 * instant_fps
            last_infer_at = now
            packet = {
                "schema": "sentinel.edge.pose.v1",
                "seq": seq,
                "t_capture_ns": capture_ns,
                "frame": {
                    "width": camera.width,
                    "height": camera.height,
                    "rotation": 0,
                    "mirrored": True,
                },
                "landmarks": landmarks,
                "motion": motion,
                "diagnostics": {
                    "fps": round(fps_ema, 2),
                    "inference_ms": round(inference_ms, 2),
                    "backend": backend.name,
                    "detector_interval": detector_interval,
                    "flow_enabled": args.optical_flow,
                },
                "telemetry": system_telemetry.sample(),
            }
            payload = json.dumps(packet, separators=(",", ":")).encode("utf-8")
            udp.sendto(payload, (args.laptop_ip, args.udp_port))
            recorder.write(packet)
            seq += 1

            if args.log_interval > 0 and now - last_log >= args.log_interval:
                print(
                    f"pose_fps={fps_ema:.1f} infer_ms={inference_ms:.1f} "
                    f"body={bool(landmarks)} packet_bytes={len(payload)}",
                    flush=True,
                )
                last_log = now
            remaining = min_period - (time.perf_counter() - started)
            if remaining > 0:
                time.sleep(remaining)
    finally:
        flow_tracker.close()
        preview_pump.close()
        relay.close()
        camera.close()
        backend.close()
        udp.close()
        recorder.close()


if __name__ == "__main__":
    raise SystemExit(main())
