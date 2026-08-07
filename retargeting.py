"""Source-agnostic human pose retargeting for the QPlay TV renderer.

The Android app sends the same 33 MediaPipe joints for every inference source
(phone NPU/GPU/CPU or UNO Q).  This module turns those noisy, source-specific
coordinates into a stable, metric skeleton with fixed segment lengths.  It is
intentionally dependency-free so the game keeps working on every laptop.  A
MuJoCo/Mink backend can be selected when the optional packages are installed;
the constrained geometric solver remains the fail-safe.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import time
from typing import Iterable


SCHEMA = "sentinel.retarget.v1"
PROFILE_SCHEMA = "sentinel.body.profile.v1"
POSE_SCHEMA = "sentinel.pose.state.v1"

NOSE = 0
L_SHO, R_SHO = 11, 12
L_ELB, R_ELB = 13, 14
L_WRI, R_WRI = 15, 16
L_HIP, R_HIP = 23, 24
L_KNE, R_KNE = 25, 26
L_ANK, R_ANK = 27, 28
L_HEEL, R_HEEL = 29, 30
L_FOOT, R_FOOT = 31, 32


Vec3 = tuple[float, float, float]


def _clamp(value: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, value))


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a: Vec3, value: float) -> Vec3:
    return (a[0] * value, a[1] * value, a[2] * value)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: Vec3, fallback: Vec3 = (0.0, 1.0, 0.0)) -> Vec3:
    length = _norm(a)
    return _mul(a, 1.0 / length) if length > 1e-7 else fallback


def _mid(a: Vec3, b: Vec3) -> Vec3:
    return _mul(_add(a, b), 0.5)


def _distance(a: Vec3, b: Vec3) -> float:
    return _norm(_sub(a, b))


def _finite(value, lo: float, hi: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < lo or number > hi:
        return None
    return number


@dataclass(frozen=True)
class BodyProfile:
    height_m: float = 1.75
    weight_kg: float = 75.0
    torso_m: float = 0.504
    calibrated: bool = False

    @classmethod
    def from_message(cls, raw: dict | None) -> "BodyProfile":
        if not isinstance(raw, dict):
            return cls()
        height_cm = _finite(raw.get("heightCm"), 120.0, 230.0)
        weight_kg = _finite(raw.get("weightKg"), 30.0, 220.0)
        if height_cm is None or weight_kg is None:
            return cls()
        height_m = height_cm / 100.0
        torso = _finite(raw.get("torsoM"), 0.25, 0.80)
        if torso is None:
            torso = height_m * 0.288
        return cls(height_m, weight_kg, torso, True)

    def wire(self) -> dict:
        return {
            "schema": PROFILE_SCHEMA,
            "heightCm": round(self.height_m * 100.0, 2),
            "weightKg": round(self.weight_kg, 2),
            "torsoM": round(self.torso_m, 4),
            "calibrated": self.calibrated,
        }

    @property
    def dimensions(self) -> dict[str, float]:
        h = self.height_m
        # De Leva-style adult proportions, deliberately kept simple.  Torso is
        # the directly calibrated shoulder-midpoint to hip-midpoint measure.
        width_scale = _clamp(math.sqrt(self.weight_kg / 75.0), 0.82, 1.22)
        return {
            "torso": self.torso_m,
            "shoulders": h * 0.259 * width_scale,
            "hips": h * 0.191 * width_scale,
            "upper_arm": h * 0.186,
            "forearm": h * 0.146,
            "thigh": h * 0.245,
            "shin": h * 0.246,
            "foot": h * 0.152,
        }


def normalize_profile_message(raw: dict | None) -> BodyProfile | None:
    if not isinstance(raw, dict) or raw.get("schema") != PROFILE_SCHEMA:
        return None
    profile = BodyProfile.from_message(raw)
    return profile if profile.calibrated else None


def normalize_pose_message(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict) or raw.get("schema") != POSE_SCHEMA:
        return None
    points_raw = raw.get("points")
    if not isinstance(points_raw, list) or len(points_raw) != 33:
        return None
    points: list[Vec3] = []
    for point in points_raw:
        if not isinstance(point, list) or len(point) < 3:
            return None
        xyz = (
            _finite(point[0], -10.0, 10.0),
            _finite(point[1], -10.0, 10.0),
            _finite(point[2], -10.0, 10.0),
        )
        if any(value is None for value in xyz):
            return None
        points.append((xyz[0], xyz[1], xyz[2]))  # type: ignore[arg-type]
    timestamp_ms = _finite(raw.get("timestampMs"), 0.0, 1e15)
    if timestamp_ms is None:
        timestamp_ms = time.time() * 1000.0
    return {
        "timestampMs": int(timestamp_ms),
        "source": str(raw.get("source", "unknown"))[:48],
        "points": points,
    }


def _two_bone(
    root: Vec3,
    observed_mid: Vec3,
    observed_end: Vec3,
    first_len: float,
    second_len: float,
    fallback_bend: Vec3,
) -> tuple[Vec3, Vec3, Vec3]:
    """Analytic two-link IK with the observed joint as the SEW plane hint."""
    target = _sub(observed_end, root)
    raw_reach = _norm(target)
    axis = _unit(target, (0.0, -1.0, 0.0))
    reach = _clamp(raw_reach, abs(first_len - second_len) + 1e-4,
                   first_len + second_len - 1e-4)
    end = _add(root, _mul(axis, reach))
    along = (first_len * first_len - second_len * second_len + reach * reach) / (2.0 * reach)
    height = math.sqrt(max(0.0, first_len * first_len - along * along))
    hint = _sub(observed_mid, root)
    bend = _sub(hint, _mul(axis, _dot(hint, axis)))
    if _norm(bend) < 1e-5:
        bend = _sub(fallback_bend, _mul(axis, _dot(fallback_bend, axis)))
    if _norm(bend) < 1e-5:
        bend = _cross(axis, (0.0, 0.0, 1.0))
    bend = _unit(bend, (1.0, 0.0, 0.0))
    mid = _add(_add(root, _mul(axis, along)), _mul(bend, height))
    return mid, end, bend


class GeometricRetargeter:
    """Fast constrained fallback inspired by SEW geometric retargeting.

    It keeps a stable bend-plane per limb, fixed anthropometric segment
    lengths, a planted-foot ground reference, and one time-aware smoothing
    stage.  No game or kick-estimation state is modified.
    """

    def __init__(self, profile: BodyProfile | None = None):
        self.profile = profile or BodyProfile()
        self.previous: list[Vec3] | None = None
        self.previous_ms: int | None = None
        self.ground_raw_y: float | None = None
        self.bends: dict[str, Vec3] = {
            "la": (-1.0, 0.0, 0.0), "ra": (1.0, 0.0, 0.0),
            "ll": (-0.2, 0.0, 1.0), "rl": (0.2, 0.0, 1.0),
        }
        self.seq = 0

    @property
    def backend(self) -> str:
        return "geometric-cpu"

    def set_profile(self, profile: BodyProfile) -> None:
        if profile != self.profile:
            self.profile = profile
            self.previous = None
            self.previous_ms = None
            self.ground_raw_y = None

    def _canonicalize(self, raw: list[Vec3]) -> tuple[list[Vec3], float]:
        hip_mid = _mid(raw[L_HIP], raw[R_HIP])
        shoulder_mid = _mid(raw[L_SHO], raw[R_SHO])
        observed_torso = _distance(hip_mid, shoulder_mid)
        if observed_torso < 1e-4:
            raise ValueError("degenerate torso")
        scale = _clamp(self.profile.torso_m / observed_torso, 0.20, 8.0)
        left_ground = max(raw[L_ANK][1], raw[L_HEEL][1], raw[L_FOOT][1])
        right_ground = max(raw[R_ANK][1], raw[R_HEEL][1], raw[R_FOOT][1])
        ground = max(left_ground, right_ground)
        if self.ground_raw_y is None:
            self.ground_raw_y = ground
        else:
            # Follow camera/body drift slowly; a lifted kicking foot cannot pull
            # the pitch upward because the lower of the two feet is selected.
            delta_m = (ground - self.ground_raw_y) * scale
            gain = 0.65 if abs(delta_m) < 0.035 else 0.08
            self.ground_raw_y += (ground - self.ground_raw_y) * gain
        return [
            (
                (p[0] - hip_mid[0]) * scale,
                (self.ground_raw_y - p[1]) * scale,
                -(p[2] - hip_mid[2]) * scale,
            )
            for p in raw
        ], scale

    def solve(self, pose: dict) -> dict:
        points, scale = self._canonicalize(pose["points"])
        dims = self.profile.dimensions
        pelvis = _mid(points[L_HIP], points[R_HIP])

        side = _sub(points[R_HIP], points[L_HIP])
        side = _unit((side[0], side[1] * 0.15, side[2]), (1.0, 0.0, 0.0))
        points[L_HIP] = _add(pelvis, _mul(side, -dims["hips"] * 0.5))
        points[R_HIP] = _add(pelvis, _mul(side, dims["hips"] * 0.5))

        observed_shoulder_mid = _mid(points[L_SHO], points[R_SHO])
        up = _unit(_sub(observed_shoulder_mid, pelvis), (0.0, 1.0, 0.0))
        if up[1] < 0.15:
            up = _unit((up[0], 0.15, up[2]))
        shoulder_mid = _add(pelvis, _mul(up, dims["torso"]))
        observed_shoulder_side = _sub(points[R_SHO], points[L_SHO])
        shoulder_side = _unit(observed_shoulder_side, side)
        points[L_SHO] = _add(shoulder_mid, _mul(shoulder_side, -dims["shoulders"] * 0.5))
        points[R_SHO] = _add(shoulder_mid, _mul(shoulder_side, dims["shoulders"] * 0.5))

        for name, root_i, mid_i, end_i in (
            ("la", L_SHO, L_ELB, L_WRI), ("ra", R_SHO, R_ELB, R_WRI),
        ):
            middle, end, bend = _two_bone(
                points[root_i], points[mid_i], points[end_i],
                dims["upper_arm"], dims["forearm"], self.bends[name],
            )
            points[mid_i], points[end_i], self.bends[name] = middle, end, bend

        for name, root_i, mid_i, end_i in (
            ("ll", L_HIP, L_KNE, L_ANK), ("rl", R_HIP, R_KNE, R_ANK),
        ):
            middle, end, bend = _two_bone(
                points[root_i], points[mid_i], points[end_i],
                dims["thigh"], dims["shin"], self.bends[name],
            )
            points[mid_i], points[end_i], self.bends[name] = middle, end, bend

        for ankle_i, heel_i, foot_i in (
            (L_ANK, L_HEEL, L_FOOT), (R_ANK, R_HEEL, R_FOOT),
        ):
            foot_dir = _unit(_sub(points[foot_i], points[ankle_i]), (0.0, 0.0, 1.0))
            points[foot_i] = _add(points[ankle_i], _mul(foot_dir, dims["foot"]))
            points[heel_i] = _add(points[ankle_i], _mul(foot_dir, -dims["foot"] * 0.28))

        timestamp_ms = pose["timestampMs"]
        if self.previous is not None and self.previous_ms is not None:
            dt = _clamp((timestamp_ms - self.previous_ms) / 1000.0, 1.0 / 120.0, 0.25)
            smoothed: list[Vec3] = []
            for current, old in zip(points, self.previous):
                velocity = _distance(current, old) / dt
                cutoff_hz = 3.2 + min(9.0, velocity * 2.2)
                alpha = 1.0 - math.exp(-2.0 * math.pi * cutoff_hz * dt)
                smoothed.append(_add(old, _mul(_sub(current, old), alpha)))
            points = smoothed
        self.previous = points
        self.previous_ms = timestamp_ms
        self.seq += 1
        return {
            "type": "retarget_state",
            "schema": SCHEMA,
            "seq": self.seq,
            "timestampMs": timestamp_ms,
            "source": pose["source"],
            "backend": self.backend,
            "space": "canonical_m",
            "profile": self.profile.wire(),
            "p": [[round(v, 5) for v in point] for point in points],
            "diagnostics": {"inputScale": round(scale, 4), "fallback": False},
        }


class PoseRetargeter:
    """Runtime backend selector with a guaranteed dependency-free fallback."""

    def __init__(self, profile: BodyProfile | None = None):
        self.geometric = GeometricRetargeter(profile)
        self.solver = self.geometric
        self.backend_error: str | None = None
        requested = os.environ.get("GF_RETARGET_BACKEND", "auto").strip().lower()
        if requested not in ("off", "geometric"):
            try:
                from retargeting_mink import MinkRetargeter
                self.solver = MinkRetargeter(profile or BodyProfile(), self.geometric)
            except Exception as exc:  # optional install or platform mismatch
                self.backend_error = f"{type(exc).__name__}: {exc}"[:160]

    @property
    def backend(self) -> str:
        return self.solver.backend

    def set_profile(self, profile: BodyProfile) -> None:
        self.geometric.set_profile(profile)
        self.solver.set_profile(profile)

    def solve(self, pose: dict) -> dict:
        try:
            result = self.solver.solve(pose)
        except Exception as exc:
            self.backend_error = f"{type(exc).__name__}: {exc}"[:160]
            self.solver = self.geometric
            result = self.geometric.solve(pose)
            result["diagnostics"]["fallback"] = True
        if self.backend_error:
            result["diagnostics"]["backendError"] = self.backend_error
        return result


def segment_length(points: Iterable[Vec3], first: int, second: int) -> float:
    values = list(points)
    return _distance(values[first], values[second])
