"""Self-contained OpenCV-DNN MediaPipe pose backend for the UNO Q.

The original implementation was recovered from the SnapKick repository's
``stash@{0}``.  Keeping it here makes the SentinelMesh deployment reproducible:
the streamer no longer imports code from a mutable checkout elsewhere on the
board.

The detector and pose preprocessing are adapted from OpenCV Zoo's
``person_detection_mediapipe`` and ``pose_estimation_mediapipe`` examples:
https://github.com/opencv/opencv_zoo
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Dict, List
from urllib import error as urllib_error
from urllib import request as urllib_request


@dataclass
class Landmark:
    x: float
    y: float
    z: float = 0.0
    visibility: float = 1.0


@dataclass
class Detection:
    landmarks: Dict[str, Landmark]
    score: float
    bbox: List[float]


def _ssd_anchors(np):
    """Generate the 2,254 MediaPipe person-detector anchor centers.

    OpenCV Zoo ships these as a large literal table.  The table is exactly
    three regular grids: 28x28 with two anchors per cell, 14x14 with two, and
    7x7 with six.  Generating it removes roughly 2,200 deployment-only lines.
    """

    anchors = []
    for grid, repeats in ((28, 2), (14, 2), (7, 6)):
        for y in range(grid):
            cy = (y + 0.5) / grid
            for x in range(grid):
                cx = (x + 0.5) / grid
                anchors.extend((cx, cy) for _ in range(repeats))
    return np.asarray(anchors, dtype=np.float32)


def available_dnn_targets(cv2) -> dict[str, int]:
    """Return OpenCV-DNN targets actually exposed by this OpenCV build."""

    backend = cv2.dnn.DNN_BACKEND_OPENCV
    available = {int(value) for value in cv2.dnn.getAvailableTargets(backend)}
    candidates = {
        "cpu": cv2.dnn.DNN_TARGET_CPU,
        "opencl": cv2.dnn.DNN_TARGET_OPENCL,
        "opencl-fp16": cv2.dnn.DNN_TARGET_OPENCL_FP16,
    }
    return {name: value for name, value in candidates.items() if int(value) in available}


def _target_id(cv2, target: str) -> int:
    targets = available_dnn_targets(cv2)
    if target not in targets:
        exposed = ", ".join(targets) or "none"
        raise RuntimeError(
            f"OpenCV DNN target {target!r} is unavailable; this build exposes: {exposed}"
        )
    return targets[target]


def _configure_net(cv2, net, target_id: int) -> None:
    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    net.setPreferableTarget(target_id)


class _PersonDetector:
    def __init__(
        self,
        cv2,
        np,
        model_path: str,
        target_id: int,
        *,
        nms_threshold: float = 0.3,
        score_threshold: float = 0.5,
        top_k: int = 5000,
    ):
        self.cv2 = cv2
        self.np = np
        self.input_size = np.asarray([224, 224])
        self.nms_threshold = nms_threshold
        self.score_threshold = score_threshold
        self.top_k = top_k
        self.model = cv2.dnn.readNet(model_path)
        _configure_net(cv2, self.model, target_id)
        self.anchors = _ssd_anchors(np)

    def infer(self, image):
        cv2, np = self.cv2, self.np
        original_shape = np.asarray([image.shape[1], image.shape[0]])
        pad_bias = np.asarray([0.0, 0.0])
        value = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        value = (value - 0.5) * 2.0
        ratio = min(self.input_size / value.shape[:2])
        if value.shape[:2] != tuple(self.input_size):
            ratio_size = (np.asarray(value.shape[:2]) * ratio).astype(np.int32)
            value = cv2.resize(value, (int(ratio_size[1]), int(ratio_size[0])))
            pad_h = int(self.input_size[0] - ratio_size[0])
            pad_w = int(self.input_size[1] - ratio_size[1])
            left, top = pad_w // 2, pad_h // 2
            value = cv2.copyMakeBorder(
                value,
                top,
                pad_h - top,
                left,
                pad_w - left,
                cv2.BORDER_CONSTANT,
                None,
                (0, 0, 0),
            )
            pad_bias[:] = (left, top)
        blob = np.transpose(value, (2, 0, 1))[None, ...]
        pad_bias = (pad_bias / ratio).astype(np.int32)

        self.model.setInput(blob)
        outputs = self.model.forward(self.model.getUnconnectedOutLayersNames())
        box_output = next(output for output in outputs if output.shape[-1] >= 12)
        score_output = next(output for output in outputs if output.shape[-1] == 1)
        score = np.clip(score_output[0, :, 0].astype(np.float64), -100, 100)
        score = 1.0 / (1.0 + np.exp(-score))
        box_delta = box_output[0, :, :4]
        landmark_delta = box_output[0, :, 4:]
        scale = max(original_shape)

        centers = box_delta[:, :2] / self.input_size
        sizes = box_delta[:, 2:] / self.input_size
        xy1 = (centers - sizes / 2 + self.anchors) * scale
        xy2 = (centers + sizes / 2 + self.anchors) * scale
        boxes = np.concatenate((xy1, xy2), axis=1)
        boxes -= [pad_bias[0], pad_bias[1], pad_bias[0], pad_bias[1]]
        keep = cv2.dnn.NMSBoxes(
            boxes,
            score,
            self.score_threshold,
            self.nms_threshold,
            top_k=self.top_k,
        )
        keep = np.asarray(keep).reshape(-1)
        if keep.size == 0:
            return np.empty((0, 13), dtype=np.float32)

        selected_score = score[keep]
        selected_box = boxes[keep]
        selected_landmarks = landmark_delta[keep].reshape(-1, 4, 2)
        selected_landmarks = selected_landmarks / self.input_size
        selected_anchors = self.anchors[keep]
        for index, points in enumerate(selected_landmarks):
            points += selected_anchors[index]
        selected_landmarks *= scale
        selected_landmarks -= pad_bias
        return np.c_[
            selected_box.reshape(-1, 4),
            selected_landmarks.reshape(-1, 8),
            selected_score.reshape(-1, 1),
        ]

    def close(self) -> None:
        self.model = None


class _PoseEstimator:
    def __init__(self, cv2, np, model_path: str, target_id: int, confidence: float = 0.5):
        self.cv2 = cv2
        self.np = np
        self.input_size = np.asarray([256, 256])
        self.confidence = confidence
        self.model = cv2.dnn.readNet(model_path)
        _configure_net(cv2, self.model, target_id)

    def infer(self, image, person):
        cv2, np = self.cv2, self.np
        pad_bias = np.asarray([0, 0], dtype=np.int32)
        person_keypoints = person[4:12].reshape(-1, 2)
        mid_hip = person_keypoints[0].copy()
        full_body = person_keypoints[1].copy()
        full_distance = np.linalg.norm(mid_hip - full_body)
        full_bbox = np.asarray(
            [mid_hip - full_distance, mid_hip + full_distance], dtype=np.int32
        )
        center = np.sum(full_bbox, axis=0) / 2
        half_size = (full_bbox[1] - full_bbox[0]) / 2
        full_bbox = np.asarray([center - half_size, center + half_size], dtype=np.int32)
        person_bbox = full_bbox.copy()
        person_bbox[:, 0] = np.clip(person_bbox[:, 0], 0, image.shape[1])
        person_bbox[:, 1] = np.clip(person_bbox[:, 1], 0, image.shape[0])
        cropped = image[
            person_bbox[0, 1] : person_bbox[1, 1],
            person_bbox[0, 0] : person_bbox[1, 0],
            :,
        ]
        if cropped.size == 0:
            return None
        left, top = person_bbox[0] - full_bbox[0]
        right, bottom = full_bbox[1] - person_bbox[1]
        cropped = cv2.copyMakeBorder(
            cropped,
            int(top),
            int(bottom),
            int(left),
            int(right),
            cv2.BORDER_CONSTANT,
            None,
            (0, 0, 0),
        )
        pad_bias += person_bbox[0] - [left, top]
        mid_hip -= pad_bias
        full_body -= pad_bias
        radians = np.pi / 2 - np.arctan2(
            -(full_body[1] - mid_hip[1]), full_body[0] - mid_hip[0]
        )
        radians -= 2 * np.pi * np.floor((radians + np.pi) / (2 * np.pi))
        angle = np.rad2deg(radians)
        rotation = cv2.getRotationMatrix2D(mid_hip, angle, 1.0)
        rotated = cv2.warpAffine(cropped, rotation, (cropped.shape[1], cropped.shape[0]))
        blob = cv2.resize(rotated, tuple(self.input_size), interpolation=cv2.INTER_AREA)
        blob = cv2.cvtColor(blob.astype(np.float32), cv2.COLOR_BGR2RGB) / 255.0

        self.model.setInput(blob[None, ...])
        outputs = self.model.forward(self.model.getUnconnectedOutLayersNames())
        landmarks, confidence, _mask, heatmap, world = outputs
        confidence = float(confidence[0][0])
        if confidence < self.confidence:
            return None
        landmarks = landmarks[0].reshape(-1, 5)
        world = world[0].reshape(-1, 3)
        landmarks[:, 3:] = 1.0 / (1.0 + np.exp(-landmarks[:, 3:]))

        rotated_bbox = np.asarray([[0, 0], [cropped.shape[1], cropped.shape[0]]])
        scale_factor = (rotated_bbox[1] - rotated_bbox[0]) / self.input_size
        landmarks[:, :2] = (landmarks[:, :2] - self.input_size / 2) * scale_factor
        landmarks[:, 2] *= max(scale_factor)
        coordinate_rotation = cv2.getRotationMatrix2D((0, 0), angle, 1.0)
        screen_rotated = np.dot(landmarks[:, :2], coordinate_rotation[:, :2])
        screen_rotated = np.c_[screen_rotated, landmarks[:, 2:]]
        world_rotated = np.dot(world[:, :2], coordinate_rotation[:, :2])
        world_rotated = np.c_[world_rotated, world[:, 2]]

        component = np.asarray(
            [[rotation[0, 0], rotation[1, 0]], [rotation[0, 1], rotation[1, 1]]]
        )
        translation = np.asarray([rotation[0, 2], rotation[1, 2]])
        inverse = np.c_[
            component,
            [-np.dot(component[0], translation), -np.dot(component[1], translation)],
        ]
        center = np.append(np.sum(rotated_bbox, axis=0) / 2, 1)
        original_center = np.asarray([np.dot(center, inverse[0]), np.dot(center, inverse[1])])
        landmarks[:, :2] = screen_rotated[:, :2] + original_center + pad_bias

        bbox = np.asarray(
            [np.amin(landmarks[:, :2], axis=0), np.amax(landmarks[:, :2], axis=0)]
        )
        center = np.sum(bbox, axis=0) / 2
        half_size = (bbox[1] - bbox[0]) * 1.25 / 2
        bbox = np.asarray([center - half_size, center + half_size])
        return bbox, landmarks, world_rotated, None, heatmap, confidence

    def close(self) -> None:
        self.model = None


class MediaPipeOnnxBackend:
    """Two-stage OpenCV-DNN person detector plus 33-point pose estimator."""

    _LANDMARK_NAMES = {index: str(index) for index in range(33)}

    def __init__(
        self,
        pose_model_path: str,
        person_model_path: str | None,
        num_poses: int,
        *,
        roi_url: str | None = None,
        roi_hz: float = 5.0,
        detector_interval: int = 4,
        dnn_target: str = "cpu",
    ):
        import cv2
        import numpy as np

        self.cv2 = cv2
        self.np = np
        self.dnn_target = dnn_target
        self.name = f"mediapipe-onnx-opencv-{dnn_target}"
        target_id = _target_id(cv2, dnn_target)
        self.num_poses = max(1, int(num_poses))
        self.detector_interval = max(1, int(detector_interval))
        self.person_detector = None
        self.roi_url = roi_url
        self._external_persons = []
        self._external_persons_lock = threading.Lock()
        self._roi_stop = threading.Event()
        self._roi_thread: threading.Thread | None = None
        self._last_roi_update = 0.0
        self._frame_index = 0
        self._cached_persons = []
        self._force_detector_refresh = True
        self.last_detector_ran = False
        if roi_url:
            self._roi_thread = threading.Thread(
                target=self._roi_poll_loop,
                args=(max(0.5, float(roi_hz)),),
                daemon=True,
            )
            self._roi_thread.start()
        else:
            if not person_model_path:
                raise ValueError("person_model_path is required when --roi-url is absent")
            self.person_detector = _PersonDetector(
                cv2,
                np,
                person_model_path,
                target_id,
                top_k=max(10, self.num_poses * 4),
            )
        self.pose_estimator = _PoseEstimator(cv2, np, pose_model_path, target_id)

    def detect(self, frame, timestamp_ms: int) -> List[Detection]:
        del timestamp_ms
        height, width = frame.shape[:2]
        if self.roi_url:
            with self._external_persons_lock:
                persons = [person.copy() for person in self._external_persons]
            self.last_detector_ran = False
        else:
            refresh = (
                self._force_detector_refresh
                or not self._cached_persons
                or self._frame_index % self.detector_interval == 0
            )
            if refresh:
                self._cached_persons = [
                    self.np.asarray(person, dtype=self.np.float32)
                    for person in self.person_detector.infer(frame)[: self.num_poses]
                ]
                self._force_detector_refresh = False
            persons = [person.copy() for person in self._cached_persons]
            self.last_detector_ran = refresh
            self._frame_index += 1

        detections = []
        for person in persons[: self.num_poses]:
            pose = self.pose_estimator.infer(frame, self.np.asarray(person).copy())
            if pose is None:
                self._force_detector_refresh = True
                continue
            bbox, landmarks, world, _mask, _heatmap, confidence = pose
            landmark_map = {}
            valid_x, valid_y = [], []
            for index, name in self._LANDMARK_NAMES.items():
                if index >= len(landmarks):
                    continue
                x, y, z, visibility, presence = landmarks[index]
                nx = max(0.0, min(1.0, float(x) / max(1, width)))
                ny = max(0.0, min(1.0, float(y) / max(1, height)))
                world_z = float(world[index][2]) if index < len(world) else float(z)
                landmark_map[name] = Landmark(
                    nx,
                    ny,
                    world_z,
                    visibility=float(min(visibility, presence)),
                )
                if visibility >= 0.25 and presence >= 0.25:
                    valid_x.append(nx)
                    valid_y.append(ny)
            if not valid_x:
                continue
            bbox = self.np.asarray(bbox, dtype=self.np.float32)
            x0, y0 = bbox[0]
            x1, y1 = bbox[1]
            detections.append(
                Detection(
                    landmarks=landmark_map,
                    score=float(confidence),
                    bbox=[
                        max(0.0, min(1.0, float(x0) / max(1, width))),
                        max(0.0, min(1.0, float(y0) / max(1, height))),
                        max(0.01, min(1.0, float(x1 - x0) / max(1, width))),
                        max(0.01, min(1.0, float(y1 - y0) / max(1, height))),
                    ],
                )
            )
        return detections

    def close(self) -> None:
        self._roi_stop.set()
        if self._roi_thread is not None:
            self._roi_thread.join(timeout=0.5)
        if self.person_detector is not None:
            self.person_detector.close()
        self.pose_estimator.close()

    def _roi_poll_loop(self, roi_hz: float) -> None:
        interval = 1.0 / roi_hz
        while not self._roi_stop.is_set():
            started = time.monotonic()
            try:
                with urllib_request.urlopen(self.roi_url, timeout=0.15) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                people = []
                if payload.get("status") in ("live", "waiting"):
                    for item in payload.get("people", [])[: self.num_poses]:
                        values = item.get("model_person", [])
                        if len(values) >= 13:
                            people.append(self.np.asarray(values[:13], dtype=self.np.float32))
                with self._external_persons_lock:
                    self._external_persons = people
                self._last_roi_update = time.monotonic()
            except (OSError, ValueError, TypeError, urllib_error.URLError):
                if time.monotonic() - self._last_roi_update > 1.0:
                    with self._external_persons_lock:
                        self._external_persons = []
            delay = interval - (time.monotonic() - started)
            if delay > 0:
                self._roi_stop.wait(delay)
