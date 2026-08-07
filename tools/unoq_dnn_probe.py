#!/usr/bin/env python3
"""Read-only CPU/OpenCL probe for the UNO Q OpenCV-DNN pose models."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from pathlib import Path

import cv2
import numpy as np


def percentile(values, fraction):
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def device_details():
    details = {}
    try:
        device = cv2.ocl.Device.getDefault()
    except (AttributeError, cv2.error):
        return details
    for name in ("name", "vendorName", "version", "OpenCL_C_Version", "driverVersion"):
        try:
            value = getattr(device, name)
            details[name] = value() if callable(value) else value
        except (AttributeError, cv2.error):
            pass
    return details


def target_map():
    return {
        "cpu": cv2.dnn.DNN_TARGET_CPU,
        "opencl": cv2.dnn.DNN_TARGET_OPENCL,
        "opencl-fp16": cv2.dnn.DNN_TARGET_OPENCL_FP16,
    }


def benchmark(model_path, shape, target_name, warmup, iterations):
    net = cv2.dnn.readNet(str(model_path))
    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    net.setPreferableTarget(target_map()[target_name])
    tensor = np.zeros(shape, dtype=np.float32)
    output_names = net.getUnconnectedOutLayersNames()
    samples = []
    output_shapes = None
    for index in range(warmup + iterations):
        net.setInput(tensor)
        started = time.perf_counter()
        outputs = net.forward(output_names)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if output_shapes is None:
            output_shapes = [list(np.asarray(value).shape) for value in outputs]
        if index >= warmup:
            samples.append(elapsed_ms)
    return {
        "target": target_name,
        "median_ms": round(statistics.median(samples), 2),
        "p95_ms": round(percentile(samples, 0.95), 2),
        "min_ms": round(min(samples), 2),
        "max_ms": round(max(samples), 2),
        "outputs": output_shapes,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-dir",
        default="/home/arduino/models/opencv-mediapipe",
    )
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument(
        "--person-model",
        default="person_detection_mediapipe_2023mar.onnx",
    )
    parser.add_argument(
        "--pose-model",
        default="pose_estimation_mediapipe_2023mar.onnx",
    )
    parser.add_argument(
        "--targets",
        nargs="+",
        choices=tuple(target_map()),
        default=list(target_map()),
    )
    args = parser.parse_args()
    model_dir = Path(args.model_dir)
    models = {
        "person": (
            model_dir / args.person_model,
            (1, 3, 224, 224),
        ),
        "pose": (
            model_dir / args.pose_model,
            (1, 256, 256, 3),
        ),
    }
    available_ids = {
        int(value)
        for value in cv2.dnn.getAvailableTargets(cv2.dnn.DNN_BACKEND_OPENCV)
    }
    report = {
        "system": platform.platform(),
        "python": platform.python_version(),
        "opencv": cv2.__version__,
        "opencl_have": bool(cv2.ocl.haveOpenCL()),
        "opencl_use_before": bool(cv2.ocl.useOpenCL()),
        "opencl_device": device_details(),
        "available_targets": [
            name for name, value in target_map().items() if int(value) in available_ids
        ],
        "models": {},
    }
    cv2.ocl.setUseOpenCL(True)
    report["opencl_use_after"] = bool(cv2.ocl.useOpenCL())
    for model_name, (path, shape) in models.items():
        item = {"path": str(path), "bytes": path.stat().st_size, "benchmarks": []}
        for target_name in args.targets:
            if int(target_map()[target_name]) not in available_ids:
                item["benchmarks"].append(
                    {"target": target_name, "error": "not exposed by OpenCV build"}
                )
                continue
            try:
                item["benchmarks"].append(
                    benchmark(
                        path,
                        shape,
                        target_name,
                        max(0, args.warmup),
                        max(1, args.iterations),
                    )
                )
            except Exception as exc:
                item["benchmarks"].append(
                    {"target": target_name, "error": f"{type(exc).__name__}: {exc}"}
                )
        report["models"][model_name] = item
    build_lines = [
        line.strip()
        for line in cv2.getBuildInformation().splitlines()
        if "OpenCL" in line or "Parallel framework" in line
    ]
    report["build"] = build_lines
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
